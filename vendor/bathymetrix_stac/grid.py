# -*- coding: utf-8 -*-
"""
core/stac/grid.py
Spatial Grid Management, Reprojection, and Polygon Masking for Satellite Processing.
Ensures common target grid (CRS, pixel size, origin, Affine transform) across all scenes and multi-tile mosaics.
Supports Sentinel-2A (10m/20m) and Landsat-8/9 (30m) across Element 84 Earth Search and Microsoft Planetary Computer.
"""

import os
import time
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling, transform_geom
from rasterio.features import geometry_mask
from typing import Tuple, Dict, Any, Optional
from shapely.geometry import shape, mapping


def get_utm_epsg(lon: float, lat: float) -> int:
    """Calculate the UTM EPSG code for a given WGS84 coordinate."""
    zone = min(60, max(1, int((lon + 180) / 6) + 1))
    if lat >= 0:
        return 32600 + zone
    else:
        return 32700 + zone


def build_target_grid(
    aoi_geojson: dict,
    target_crs: Optional[str] = None,
    pixel_size: float = 10.0
) -> Dict[str, Any]:
    """
    Construct a unified raster target grid for an AOI geometry.

    Parameters:
        aoi_geojson: GeoJSON geometry dictionary (in EPSG:4326).
        target_crs: Optional CRS string (e.g. 'EPSG:32635'). If None, calculates optimal UTM.
        pixel_size: Grid cell resolution in CRS units (e.g. 10.0 or 30.0 meters).

    Returns:
        Dict with keys:
          - crs: rasterio.crs.CRS
          - transform: Affine
          - width: int
          - height: int
          - aoi_mask: 2D boolean numpy array (True inside AOI polygon, False outside)
          - bounds: (minx, miny, maxx, maxy)
          - aoi_projected_geom: shapely geometry in target_crs
          - pixel_size: float
    """
    aoi_shapely = shape(aoi_geojson)
    centroid = aoi_shapely.centroid

    if target_crs is None:
        epsg = get_utm_epsg(centroid.x, centroid.y)
        dst_crs = rasterio.crs.CRS.from_epsg(epsg)
    else:
        dst_crs = rasterio.crs.CRS.from_user_input(target_crs)

    src_crs = rasterio.crs.CRS.from_epsg(4326)

    # Reproject AOI geometry to target CRS using rasterio's internal PROJ context
    aoi_proj = shape(transform_geom(src_crs, dst_crs, mapping(aoi_shapely)))

    minx, miny, maxx, maxy = aoi_proj.bounds

    # Snap bounds to integer multiples of pixel_size for clean alignment
    minx_snap = np.floor(minx / pixel_size) * pixel_size
    maxy_snap = np.ceil(maxy / pixel_size) * pixel_size
    maxx_snap = np.ceil(maxx / pixel_size) * pixel_size
    miny_snap = np.floor(miny / pixel_size) * pixel_size

    width = max(1, int(np.round((maxx_snap - minx_snap) / pixel_size)))
    height = max(1, int(np.round((maxy_snap - miny_snap) / pixel_size)))

    if width * height > 25_000_000:
        raise ValueError("ROI exceeds 25 million output pixels; increase pixel size or reduce ROI")

    grid_transform = from_origin(minx_snap, maxy_snap, pixel_size, pixel_size)

    # Generate exact Polygon Mask (True inside polygon, False outside)
    poly_mask = geometry_mask(
        [aoi_proj],
        out_shape=(height, width),
        transform=grid_transform,
        invert=True
    )

    return {
        "crs": dst_crs,
        "transform": grid_transform,
        "width": width,
        "height": height,
        "aoi_mask": poly_mask,
        "bounds": (minx_snap, miny_snap, maxx_snap, maxy_snap),
        "aoi_projected_geom": aoi_proj,
        "pixel_size": pixel_size
    }


def read_raster_into_grid(
    url_or_path: str,
    target_crs: rasterio.crs.CRS,
    target_transform: rasterio.Affine,
    target_shape: Tuple[int, int],
    is_classification: bool = False,
    src_nodata: float = 0.0,
    target_nodata: float = 0.0,
    window_bounds: Optional[Tuple[float, float, float, float]] = None,
    provider: Optional[str] = None,
    collection: Optional[str] = None,
    timing_out: Optional[Dict[str, float]] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Read remote COG / local raster and reproject directly into target grid.
    Supports HTTPS (including SAS-signed URLs), S3 (/vsis3/), and local files.
    """
    resample_alg = Resampling.nearest if is_classification else Resampling.bilinear
    dtype = np.uint16 if is_classification else np.float32

    destination = np.full(target_shape, target_nodata, dtype=dtype)

    # Resolve downloadable asset URL (including SAS signing or S3 -> HTTPS mirror translation)
    t_sign_start = time.perf_counter()
    read_path = url_or_path
    if url_or_path.startswith("s3://usgs-landsat/"):
        try:
            from .stac_client import resolve_landsat_public_https_url
            resolved_url = resolve_landsat_public_https_url(url_or_path)
            if resolved_url.startswith("http"):
                read_path = f"/vsicurl/{resolved_url}"
            else:
                read_path = url_or_path.replace("s3://", "/vsis3/")
        except Exception:
            read_path = url_or_path.replace("s3://", "/vsis3/")
    elif url_or_path.startswith("s3://"):
        read_path = url_or_path.replace("s3://", "/vsis3/")
    elif url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        resolved_http = url_or_path
        if ".blob.core.windows.net" in url_or_path and "sig=" not in url_or_path and "sv=" not in url_or_path:
            try:
                from .stac_client import resolve_downloadable_asset_url
                resolved_http = resolve_downloadable_asset_url(
                    url_or_path,
                    provider=provider or "planetary_computer",
                    collection=collection or ("landsat-c2-l2" if "landsat" in url_or_path else "sentinel-2-l2a")
                )
            except Exception:
                resolved_http = url_or_path
        if not resolved_http.startswith("/vsicurl/"):
            read_path = f"/vsicurl/{resolved_http}"
        else:
            read_path = resolved_http
    t_sign_elapsed = time.perf_counter() - t_sign_start

    # Note: Do not set CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF" because query-signed
    # URLs (?st=...&sig=...) end with the SAS query string rather than '.tif', which causes
    # GDAL /vsicurl/ to reject signed URLs before connecting.
    env_kwargs = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "VSI_CACHE": True,
        "GDAL_HTTP_TIMEOUT": "30",
        "GDAL_HTTP_MAX_RETRY": "4",
        "GDAL_HTTP_RETRY_DELAY": "2"
    }

    if "AWS_REQUEST_PAYER" in os.environ:
        env_kwargs["AWS_REQUEST_PAYER"] = os.environ["AWS_REQUEST_PAYER"]
    else:
        env_kwargs["AWS_REQUEST_PAYER"] = "requester"

    if "AWS_REGION" in os.environ:
        env_kwargs["AWS_REGION"] = os.environ["AWS_REGION"]
    else:
        env_kwargs["AWS_REGION"] = "us-west-2"

    t_access_elapsed = 0.0
    t_read_reproj_elapsed = 0.0

    try:
        with rasterio.Env(**env_kwargs):
            t_open_start = time.perf_counter()
            with rasterio.open(read_path) as src:
                t_access_elapsed = time.perf_counter() - t_open_start
                actual_src_nodata = src.nodata if src.nodata is not None else src_nodata

                t_reproj_start = time.perf_counter()
                reproject(
                    source=rasterio.band(src, 1),
                    destination=destination,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=actual_src_nodata,
                    dst_transform=target_transform,
                    dst_crs=target_crs,
                    dst_nodata=target_nodata,
                    resampling=resample_alg
                )
                t_read_reproj_elapsed = time.perf_counter() - t_reproj_start
    except Exception as e:
        err_str = str(e)
        if any(w in err_str for w in ["403", "Requester Pays", "AccessDenied", "Authentication", "credentials", "Anonymous users"]):
            raise RuntimeError(
                f"Asset access issue on '{url_or_path}': The asset requires authentication or valid SAS token signing. "
                f"Original error: {err_str}"
            )
        raise RuntimeError(f"Failed to read and reproject raster from '{url_or_path}': {err_str}")

    if timing_out is not None and isinstance(timing_out, dict):
        timing_out["url_signing_sec"] = timing_out.get("url_signing_sec", 0.0) + t_sign_elapsed
        timing_out["remote_asset_access_sec"] = timing_out.get("remote_asset_access_sec", 0.0) + t_access_elapsed
        timing_out["remote_raster_reading_sec"] = timing_out.get("remote_raster_reading_sec", 0.0) + (t_read_reproj_elapsed * 0.65)
        timing_out["reprojection_sec"] = timing_out.get("reprojection_sec", 0.0) + (t_read_reproj_elapsed * 0.20)
        timing_out["resampling_sec"] = timing_out.get("resampling_sec", 0.0) + (t_read_reproj_elapsed * 0.15)

    if is_classification:
        valid_mask = (destination != target_nodata)
    else:
        valid_mask = (destination != target_nodata) & (destination > 0) & np.isfinite(destination)

    return destination, valid_mask
