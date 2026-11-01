# -*- coding: utf-8 -*-
"""
core/stac/processor.py
Shared Processing Engine for Satellite Imagery Downloader (Earth Search & Microsoft Planetary Computer).
Supports:
1. Sentinel-2A Level-2A (MSI Surface Reflectance)
2. Landsat-8 OLI Collection 2 Level-2 Surface Reflectance
3. Landsat-9 OLI-2 Collection 2 Level-2 Surface Reflectance
4. Landsat-8/9 Combined Temporal Median Composite

Sentinel-2B and Sentinel-2C are strictly excluded and rejected.
"""

import os
import json
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Any
import numpy as np
import rasterio

from .stac_client import (
    BAND_ASSET_MAP,
    ORDERED_BAND_LIST,
    LANDSAT_BAND_ASSET_MAP,
    LANDSAT_ORDERED_BAND_LIST,
    ALLOWED_PLATFORMS,
    DISALLOWED_PLATFORMS,
    PROVIDER_EARTH_SEARCH,
    normalize_provider_key
)
from .reflectance import (
    normalize_surface_reflectance,
    normalize_landsat_surface_reflectance
)
from .water_mask import (
    create_water_mask,
    decode_landsat_qa_pixel,
    decode_landsat_qa_radsat
)
from .grid import build_target_grid, read_raster_into_grid


class ProcessorError(Exception):
    """Custom exception for processing errors."""
    pass


def normalize_band_selection(bands_to_process: Optional[List[str]], is_landsat: bool) -> List[str]:
    """
    Validate and order user-selected bands according to canonical sensor band order
    so both providers always export identical band order.
    """
    default_bands = LANDSAT_ORDERED_BAND_LIST if is_landsat else ORDERED_BAND_LIST
    if not bands_to_process:
        return list(default_bands)

    requested_upper = {str(b).strip().upper() for b in bands_to_process if str(b).strip()}
    ordered = [b for b in default_bands if b.upper() in requested_upper]
    for b in bands_to_process:
        b_clean = str(b).strip()
        if b_clean and b_clean not in ordered:
            ordered.append(b_clean)
    return ordered if ordered else list(default_bands)


def _build_performance_diagnostics(
    provider: str,
    provider_timings: Dict[str, float],
    shared_timings: Dict[str, float]
) -> Dict[str, Any]:
    """
    Construct separated performance benchmark diagnostics:
    - Provider-specific measurements: STAC search, asset discovery, URL signing, remote asset access, remote raster reading
    - Shared processing measurements: reflectance conversion, masking, reprojection, resampling, median generation, GeoTIFF writing
    """
    prov_clean = {
        "stac_search": round(float(provider_timings.get("stac_search_sec", 0.0)), 6),
        "asset_discovery": round(float(provider_timings.get("asset_discovery_sec", 0.0)), 6),
        "url_signing": round(float(provider_timings.get("url_signing_sec", 0.0)), 6),
        "remote_asset_access": round(float(provider_timings.get("remote_asset_access_sec", 0.0)), 6),
        "remote_raster_reading": round(float(provider_timings.get("remote_raster_reading_sec", 0.0)), 6),
    }
    prov_clean["total_provider_specific_sec"] = round(sum(prov_clean.values()), 6)

    shared_clean = {
        "reflectance_conversion": round(float(shared_timings.get("reflectance_conversion_sec", 0.0)), 6),
        "masking": round(float(shared_timings.get("masking_sec", 0.0)), 6),
        "reprojection": round(float(shared_timings.get("reprojection_sec", 0.0)), 6),
        "resampling": round(float(shared_timings.get("resampling_sec", 0.0)), 6),
        "median_generation": round(float(shared_timings.get("median_generation_sec", 0.0)), 6),
        "geotiff_writing": round(float(shared_timings.get("geotiff_writing_sec", 0.0)), 6),
    }
    shared_clean["total_shared_processing_sec"] = round(sum(shared_clean.values()), 6)

    return {
        "provider": normalize_provider_key(provider),
        "provider_specific_timings_sec": prov_clean,
        "shared_processing_timings_sec": shared_clean,
        "total_pipeline_sec": round(
            prov_clean["total_provider_specific_sec"] + shared_clean["total_shared_processing_sec"], 6
        )
    }


def save_multiband_geotiff(
    output_path: str,
    band_arrays: List[np.ndarray],
    band_names: List[str],
    grid: dict,
    nodata_val: float = -9999.0,
    compression: str = "DEFLATE",
    metadata_extra: Optional[dict] = None
) -> str:
    """Save a multiband Float32 GeoTIFF with sensor-aware band descriptions and metadata."""
    num_bands = len(band_arrays)
    height, width = grid["height"], grid["width"]

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "nodata": nodata_val,
        "width": width,
        "height": height,
        "count": num_bands,
        "crs": grid["crs"],
        "transform": grid["transform"],
        "compress": compression.lower(),
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "interleave": "band"
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    with rasterio.open(output_path, "w", **profile) as dst:
        for idx, (arr, b_id) in enumerate(zip(band_arrays, band_names), start=1):
            dst.write(arr.astype(np.float32), idx)
            b_info = BAND_ASSET_MAP.get(b_id) or LANDSAT_BAND_ASSET_MAP.get(b_id, {})
            b_desc = f"{b_id} - {b_info.get('desc', b_id)} ({b_info.get('res', '')}m)"
            dst.set_band_description(idx, b_desc)

        tags = {
            "DATA_TYPE": "Surface Reflectance (Float32)",
            "PROCESSED_BY": "Bathymetrix-AI Satellite STAC Downloader",
            "UNITS": "Surface Reflectance [0.0 - 1.0]",
            "NODATA_VALUE": str(nodata_val),
            "SCALING_APPLIED": "True (Surface Reflectance, already scaled)",
            "DATE_PROCESSED": datetime.now().isoformat()
        }
        if metadata_extra:
            for k, v in metadata_extra.items():
                if isinstance(v, (str, int, float, bool)):
                    tags[str(k).upper()] = str(v)
        dst.update_tags(**tags)

    return output_path


def save_singleband_raster(
    output_path: str,
    data_array: np.ndarray,
    grid: dict,
    dtype: str = "uint8",
    nodata_val: Optional[float] = 255,
    description: str = "",
    compression: str = "DEFLATE"
) -> str:
    """Save a single-band categorical or quality raster (e.g. Water Mask, Valid Count)."""
    height, width = grid["height"], grid["width"]

    profile = {
        "driver": "GTiff",
        "dtype": dtype,
        "nodata": nodata_val,
        "width": width,
        "height": height,
        "count": 1,
        "crs": grid["crs"],
        "transform": grid["transform"],
        "compress": compression.lower(),
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(data_array.astype(dtype), 1)
        if description:
            dst.set_band_description(1, description)

    return output_path


def process_single_scene(
    scene_meta: dict,
    aoi_geojson: dict,
    output_dir: str,
    bands_to_process: Optional[List[str]] = None,
    target_crs: Optional[str] = None,
    pixel_size: Optional[float] = None,
    water_only: bool = False,
    water_method: str = "hybrid",
    water_mode: str = "permissive",
    water_threshold: Optional[float] = None,
    save_water_mask_raster: bool = True,
    file_prefix: Optional[str] = None,
    feedback: Optional[Any] = None,
    provider_timings: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Process a single Sentinel-2A, Landsat-8, or Landsat-9 scene using the shared scientific pipeline.
    """
    scene_id = scene_meta["id"]
    plat = str(scene_meta.get("platform", "sentinel-2a")).lower().strip()
    provider_key = normalize_provider_key(scene_meta.get("provider", PROVIDER_EARTH_SEARCH))

    if plat in DISALLOWED_PLATFORMS or "s2b" in scene_id.lower() or "s2c" in scene_id.lower():
        raise ProcessorError(
            f"Scene {scene_id} belongs to removed satellite platform '{plat}'. "
            "Sentinel-2B and Sentinel-2C are strictly excluded from download and processing."
        )

    if plat == "sentinel-2a":
        is_landsat = False
        satellite_name = "Sentinel-2A"
        sensor_name = "MSI"
        collection_name = scene_meta.get("collection", "sentinel-2-c1-l2a")
        default_res = 10.0
        default_prefix = "S2A"
    elif plat in ["landsat-8", "landsat-9"]:
        is_landsat = True
        satellite_name = "Landsat-8" if plat == "landsat-8" else "Landsat-9"
        sensor_name = "OLI" if plat == "landsat-8" else "OLI-2"
        collection_name = scene_meta.get("collection", "landsat-c2-l2")
        default_res = 30.0
        default_prefix = "L8" if plat == "landsat-8" else "L9"
    else:
        raise ProcessorError(
            f"Unsupported satellite platform: '{plat}'. Only Sentinel-2A, Landsat-8, and Landsat-9 are supported."
        )

    eff_pixel_size = float(pixel_size) if pixel_size is not None else default_res
    prefix = file_prefix if file_prefix is not None else default_prefix
    target_bands = normalize_band_selection(bands_to_process, is_landsat=is_landsat)
    scene_date = scene_meta["date"].replace("-", "")

    prov_time_acc: Dict[str, float] = dict(provider_timings or {})
    shared_time_acc: Dict[str, float] = {
        "reflectance_conversion_sec": 0.0,
        "masking_sec": 0.0,
        "reprojection_sec": 0.0,
        "resampling_sec": 0.0,
        "median_generation_sec": 0.0,
        "geotiff_writing_sec": 0.0,
    }
    io_timing_acc: Dict[str, float] = {}

    if feedback:
        feedback.pushInfo(f"=== Starting Single Scene Processing: {scene_id} ({satellite_name} | Provider: {provider_key}) ===")
        feedback.pushInfo(f"Acquisition Date: {scene_meta['date']} | Cloud Cover: {scene_meta['cloud_cover']}% | Res: {eff_pixel_size}m")

    # 1. Build Target Grid
    if feedback:
        feedback.pushInfo("Building unified target spatial grid...")
    t_grid_start = time.perf_counter()
    grid = build_target_grid(aoi_geojson, target_crs=target_crs, pixel_size=eff_pixel_size)
    shared_time_acc["reprojection_sec"] += (time.perf_counter() - t_grid_start)
    height, width = grid["height"], grid["width"]
    aoi_poly_mask = grid["aoi_mask"]

    if feedback:
        feedback.pushInfo(f"Target Grid: {width}x{height} pixels | CRS: {grid['crs']} | Pixel Size: {eff_pixel_size}m")

    # 2. Read QA layers
    scl_arr = None
    qa_pixel_arr = None
    qa_radsat_arr = None

    if not is_landsat:
        if "SCL" in scene_meta["assets"]:
            if feedback:
                feedback.pushInfo("Reading Scene Classification Layer (SCL)...")
            scl_url = scene_meta["assets"]["SCL"]["href"]
            try:
                scl_arr, _ = read_raster_into_grid(
                    scl_url,
                    target_crs=grid["crs"],
                    target_transform=grid["transform"],
                    target_shape=(height, width),
                    is_classification=True,
                    src_nodata=0,
                    target_nodata=0,
                    provider=provider_key,
                    collection=collection_name,
                    timing_out=io_timing_acc
                )
            except Exception as err:
                if feedback:
                    feedback.pushWarning(f"Could not load SCL: {err}")
    else:
        if "QA_PIXEL" in scene_meta["assets"]:
            if feedback:
                feedback.pushInfo("Reading Landsat QA_PIXEL bitmask...")
            qa_url = scene_meta["assets"]["QA_PIXEL"]["href"]
            try:
                qa_pixel_arr, _ = read_raster_into_grid(
                    qa_url,
                    target_crs=grid["crs"],
                    target_transform=grid["transform"],
                    target_shape=(height, width),
                    is_classification=True,
                    src_nodata=1,
                    target_nodata=1,
                    provider=provider_key,
                    collection=collection_name,
                    timing_out=io_timing_acc
                )
            except Exception as err:
                if feedback:
                    feedback.pushWarning(f"Could not load QA_PIXEL: {err}")

        if "QA_RADSAT" in scene_meta["assets"]:
            if feedback:
                feedback.pushInfo("Reading Landsat QA_RADSAT bitmask...")
            rad_url = scene_meta["assets"]["QA_RADSAT"]["href"]
            try:
                qa_radsat_arr, _ = read_raster_into_grid(
                    rad_url,
                    target_crs=grid["crs"],
                    target_transform=grid["transform"],
                    target_shape=(height, width),
                    is_classification=True,
                    src_nodata=0,
                    target_nodata=0,
                    provider=provider_key,
                    collection=collection_name,
                    timing_out=io_timing_acc
                )
            except Exception as err:
                if feedback:
                    feedback.pushWarning(f"Could not load QA_RADSAT: {err}")

    # 3. Read and normalize each spectral band
    band_refl_arrays = []
    normalized_band_names = []
    provenance_log = {}

    total_bands = len(target_bands)
    for i, b_id in enumerate(target_bands):
        if feedback and hasattr(feedback, "isCanceled") and feedback.isCanceled():
            raise ProcessorError("Processing canceled by user.")

        if b_id not in scene_meta["assets"]:
            mandatory_bands = ["B02", "B03", "B04", "B08"] if not is_landsat else ["SR_B2", "SR_B3", "SR_B4", "SR_B5"]
            if b_id in mandatory_bands:
                raise ProcessorError(f"Mandatory Surface Reflectance band '{b_id}' not found in scene {scene_id} assets.")
            if feedback:
                feedback.pushWarning(f"Band {b_id} not found in scene assets; skipping.")
            continue

        b_meta = scene_meta["assets"][b_id]
        b_url = b_meta["href"]

        if feedback:
            feedback.pushInfo(f"[{i+1}/{total_bands}] Reading & normalizing {satellite_name} band {b_id}...")
            if hasattr(feedback, "setProgress"):
                feedback.setProgress(int((i + 1) / (total_bands + 3) * 100))

        raw_dn, _ = read_raster_into_grid(
            b_url,
            target_crs=grid["crs"],
            target_transform=grid["transform"],
            target_shape=(height, width),
            is_classification=False,
            src_nodata=b_meta.get("nodata", 0),
            target_nodata=0,
            provider=provider_key,
            collection=collection_name,
            timing_out=io_timing_acc
        )

        t_refl_start = time.perf_counter()
        if is_landsat:
            refl_arr, _, b_prov = normalize_landsat_surface_reflectance(
                raw_dn,
                scale=b_meta.get("scale", 0.0000275),
                offset=b_meta.get("offset", -0.2),
                raw_nodata=b_meta.get("nodata", 0),
                output_nodata=-9999.0
            )
        else:
            refl_arr, _, b_prov = normalize_surface_reflectance(
                raw_dn,
                scale=b_meta.get("scale", 0.0001),
                offset=b_meta.get("offset", None),
                raw_nodata=b_meta.get("nodata", 0),
                processing_baseline=scene_meta.get("processing_baseline"),
                boa_offset_applied=scene_meta.get("boa_offset_applied"),
                output_nodata=-9999.0
            )
        shared_time_acc["reflectance_conversion_sec"] += (time.perf_counter() - t_refl_start)

        t_mask_start = time.perf_counter()
        refl_arr[~aoi_poly_mask] = -9999.0
        shared_time_acc["masking_sec"] += (time.perf_counter() - t_mask_start)

        band_refl_arrays.append(refl_arr)
        normalized_band_names.append(b_id)
        provenance_log[b_id] = b_prov

    if not band_refl_arrays:
        raise ProcessorError(f"No spectral bands could be loaded for scene {scene_id}")

    # 4. Optional Water Masking
    water_bool_mask = None
    water_stats = {}
    water_uint8_mask = None

    if water_only or save_water_mask_raster:
        if feedback:
            feedback.pushInfo(f"Computing Water Mask using method: {water_method} (mode: {water_mode})...")

        def get_or_load_band(b_name):
            if b_name in normalized_band_names:
                idx = normalized_band_names.index(b_name)
                return band_refl_arrays[idx]
            if b_name in scene_meta["assets"]:
                try:
                    bm = scene_meta["assets"][b_name]
                    rdn, _ = read_raster_into_grid(
                        bm["href"],
                        target_crs=grid["crs"],
                        target_transform=grid["transform"],
                        target_shape=(height, width),
                        is_classification=False,
                        src_nodata=bm.get("nodata", 0),
                        target_nodata=0,
                        provider=provider_key,
                        collection=collection_name,
                        timing_out=io_timing_acc
                    )
                    t_r0 = time.perf_counter()
                    if is_landsat:
                        r_arr, _, _ = normalize_landsat_surface_reflectance(
                            rdn,
                            scale=bm.get("scale", 0.0000275),
                            offset=bm.get("offset", -0.2),
                            raw_nodata=bm.get("nodata", 0),
                            output_nodata=-9999.0
                        )
                    else:
                        r_arr, _, _ = normalize_surface_reflectance(
                            rdn,
                            scale=bm.get("scale", 0.0001),
                            offset=bm.get("offset", None),
                            raw_nodata=bm.get("nodata", 0),
                            processing_baseline=scene_meta.get("processing_baseline"),
                            boa_offset_applied=scene_meta.get("boa_offset_applied"),
                            output_nodata=-9999.0
                        )
                    shared_time_acc["reflectance_conversion_sec"] += (time.perf_counter() - t_r0)
                    r_arr[~aoi_poly_mask] = -9999.0
                    return r_arr
                except Exception:
                    return None
            return None

        if is_landsat:
            green = get_or_load_band("SR_B3")
            nir = get_or_load_band("SR_B5")
            swir1 = get_or_load_band("SR_B6") if water_method == "mndwi" else None
        else:
            green = get_or_load_band("B03")
            nir = get_or_load_band("B08")
            swir1 = get_or_load_band("B11") if water_method == "mndwi" else None

        t_wmask_start = time.perf_counter()
        water_bool_mask, water_uint8_mask, water_stats = create_water_mask(
            scl=scl_arr,
            qa_pixel=qa_pixel_arr,
            qa_radsat=qa_radsat_arr,
            green_refl=green,
            nir_refl=nir,
            swir1_refl=swir1,
            valid_mask=aoi_poly_mask,
            method=water_method,
            mode=water_mode,
            threshold=water_threshold,
            use_otsu=(water_threshold is None)
        )

        water_uint8_mask[~aoi_poly_mask] = 255

        if water_only:
            if feedback:
                feedback.pushInfo("Applying Water-Only Mask: non-water land pixels set to NoData (-9999.0)...")
            for arr in band_refl_arrays:
                arr[~water_bool_mask | ~aoi_poly_mask] = -9999.0
        shared_time_acc["masking_sec"] += (time.perf_counter() - t_wmask_start)

        if feedback:
            feedback.pushInfo(f"Water Mask Stats: {water_stats['water_percentage']}% Water | {water_stats['invalid_percentage']}% Invalid/Clouds")

    # 5. Export GeoTIFF Outputs
    os.makedirs(output_dir, exist_ok=True)
    mode_str = "WaterOnly" if water_only else "BOA_Float32"
    clean_base = scene_id if scene_id.startswith((prefix, "S2A", "S2B", "LC08", "LC09")) else f"{prefix}_{scene_date}_{scene_id}"
    refl_filename = f"{clean_base}_{mode_str}.tif"
    refl_path = os.path.join(output_dir, refl_filename)

    if feedback:
        feedback.pushInfo(f"Exporting Multiband Float32 GeoTIFF to: {refl_path}")

    scale_val = 0.0000275 if is_landsat else 0.0001
    offset_val = -0.2 if is_landsat else (-0.1 if scene_meta.get("processing_baseline") not in [None, "N/A"] else 0.0)

    t_write_start = time.perf_counter()
    save_multiband_geotiff(
        refl_path,
        band_arrays=band_refl_arrays,
        band_names=normalized_band_names,
        grid=grid,
        nodata_val=-9999.0,
        metadata_extra={
            "SCENE_ID": scene_id,
            "SATELLITE": satellite_name,
            "SENSOR": sensor_name,
            "PLATFORM": plat,
            "PROVIDER": provider_key,
            "STAC_COLLECTION": collection_name,
            "ACQUISITION_DATE": scene_meta["date"],
            "TILE": scene_meta.get("mgrs_tile", ""),
            "PROCESSING_LEVEL": "Collection 2 Level-2" if is_landsat else "Level-2A BOA",
            "WATER_ONLY_APPLIED": str(water_only),
            "WATER_METHOD": water_method,
            "SCALE_FACTOR": str(scale_val),
            "OFFSET": str(offset_val),
            "PIXEL_SIZE": str(eff_pixel_size)
        }
    )

    outputs = {
        "surface_reflectance": refl_path,
        "scene_id": scene_id,
        "satellite": satellite_name,
        "platform": plat,
        "provider": provider_key,
        "grid": grid,
        "bands": normalized_band_names,
        "water_stats": water_stats
    }

    if save_water_mask_raster and water_uint8_mask is not None:
        mask_filename = f"{clean_base}_WaterMask.tif"
        mask_path = os.path.join(output_dir, mask_filename)
        save_singleband_raster(
            mask_path,
            water_uint8_mask,
            grid=grid,
            dtype="uint8",
            nodata_val=255,
            description="Water Mask: 1=Water, 0=Land, 255=NoData"
        )
        outputs["water_mask"] = mask_path
    shared_time_acc["geotiff_writing_sec"] += (time.perf_counter() - t_write_start)

    prov_time_acc["url_signing_sec"] = prov_time_acc.get("url_signing_sec", 0.0) + io_timing_acc.get("url_signing_sec", 0.0)
    prov_time_acc["remote_asset_access_sec"] = prov_time_acc.get("remote_asset_access_sec", 0.0) + io_timing_acc.get("remote_asset_access_sec", 0.0)
    prov_time_acc["remote_raster_reading_sec"] = prov_time_acc.get("remote_raster_reading_sec", 0.0) + io_timing_acc.get("remote_raster_reading_sec", 0.0)
    shared_time_acc["reprojection_sec"] += io_timing_acc.get("reprojection_sec", 0.0)
    shared_time_acc["resampling_sec"] += io_timing_acc.get("resampling_sec", 0.0)

    perf_diagnostics = _build_performance_diagnostics(
        provider=provider_key,
        provider_timings=prov_time_acc,
        shared_timings=shared_time_acc
    )
    outputs["performance_diagnostics"] = perf_diagnostics

    json_path = os.path.join(output_dir, f"{prefix}_{scene_date}_{scene_id}_Processing_Metadata.json")
    meta_doc = {
        "provider": provider_key,
        "satellite": satellite_name,
        "platform": plat,
        "sensor": sensor_name,
        "stac_collection": collection_name,
        "processing_mode": "Single Scene",
        "scene_id": scene_id,
        "acquisition_date": scene_meta["datetime"],
        "cloud_cover_percent": scene_meta["cloud_cover"],
        "processing_level": "Collection 2 Level-2" if is_landsat else f"Processing Baseline {scene_meta.get('processing_baseline', 'N/A')}",
        "tile": scene_meta.get("mgrs_tile", ""),
        "crs": str(grid["crs"]),
        "spatial_resolution_meters": eff_pixel_size,
        "dimensions": [width, height],
        "input_data_type": "uint16",
        "output_data_type": "float32",
        "scale_factor": scale_val,
        "offset": offset_val,
        "scaling_applied_by": "Bathymetrix-AI STAC Reader (Surface Reflectance, already scaled)",
        "bands": normalized_band_names,
        # Strip temporary SAS tokens from persisted metadata
        "source_assets": {
            b: str(scene_meta["assets"][b]["href"]).split("?")[0]
            for b in normalized_band_names if b in scene_meta["assets"]
        },
        "water_only": water_only,
        "water_mask_statistics": water_stats,
        "band_normalization_provenance": provenance_log,
        "performance_diagnostics": perf_diagnostics,
        "generation_timestamp": datetime.now().isoformat(),
        "export_path": refl_path
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_doc, f, indent=2)
    outputs["metadata_json"] = json_path

    if feedback:
        feedback.pushInfo(f"=== Single Scene Processing Completed Successfully: {refl_path} ===")

    return outputs


def process_temporal_median_composite(
    scenes_meta: List[dict],
    aoi_geojson: dict,
    output_dir: str,
    bands_to_process: Optional[List[str]] = None,
    target_crs: Optional[str] = None,
    pixel_size: Optional[float] = None,
    water_only: bool = False,
    water_temporal_policy: str = "consensus",
    water_occurrence_threshold: float = 50.0,
    min_valid_observations: int = 1,
    save_valid_count_raster: bool = True,
    save_temp_std_raster: bool = True,
    save_water_occurrence_raster: bool = True,
    chunk_size: int = 512,
    file_prefix: Optional[str] = None,
    feedback: Optional[Any] = None,
    provider_timings: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Process Temporal Median Composite across multiple scenes using the shared scientific pipeline.
    """
    if not scenes_meta:
        raise ProcessorError("No scenes provided for temporal median composite.")

    platforms_present = set()
    for s in scenes_meta:
        plat = str(s.get("platform", "")).lower().strip()
        s_id = str(s.get("id", ""))
        if plat in DISALLOWED_PLATFORMS or "s2b" in s_id.lower() or "s2c" in s_id.lower():
            raise ProcessorError(
                f"Scene {s_id} belongs to removed satellite platform '{plat}'. "
                "Sentinel-2B and Sentinel-2C cannot enter Median Composite processing."
            )
        if plat not in ALLOWED_PLATFORMS:
            raise ProcessorError(f"Scene {s_id} platform '{plat}' is not supported. Supported: Sentinel-2A, Landsat-8, Landsat-9.")
        platforms_present.add(plat)

    has_s2a = "sentinel-2a" in platforms_present
    has_landsat = ("landsat-8" in platforms_present) or ("landsat-9" in platforms_present)

    if has_s2a and has_landsat:
        raise ProcessorError(
            "Sentinel-2A and Landsat-8/9 cannot be combined in the same median composite automatically. "
            "Cross-sensor harmonization is outside the scope of this update. "
            "Please composite Sentinel-2A and Landsat scenes separately."
        )

    provider_key = normalize_provider_key(scenes_meta[0].get("provider", PROVIDER_EARTH_SEARCH))

    if has_landsat:
        is_landsat = True
        default_res = 30.0
        has_l8 = "landsat-8" in platforms_present
        has_l9 = "landsat-9" in platforms_present
        if has_l8 and has_l9:
            satellite_label = "Landsat-8 & Landsat-9"
            sensor_label = "OLI/OLI-2"
            default_prefix = "Landsat_Composite"
        elif has_l8:
            satellite_label = "Landsat-8"
            sensor_label = "OLI"
            default_prefix = "L8_Composite"
        else:
            satellite_label = "Landsat-9"
            sensor_label = "OLI-2"
            default_prefix = "L9_Composite"
        collection_label = scenes_meta[0].get("collection", "landsat-c2-l2")
    else:
        is_landsat = False
        satellite_label = "Sentinel-2A"
        sensor_label = "MSI"
        default_res = 10.0
        default_prefix = "S2A_Composite"
        collection_label = scenes_meta[0].get("collection", "sentinel-2-c1-l2a")

    eff_pixel_size = float(pixel_size) if pixel_size is not None else default_res
    prefix = file_prefix if file_prefix is not None else default_prefix
    target_bands = normalize_band_selection(bands_to_process, is_landsat=is_landsat)

    sorted_scenes = sorted(scenes_meta, key=lambda s: s.get("datetime", ""))
    date_start = sorted_scenes[0]["date"].replace("-", "")
    date_end = sorted_scenes[-1]["date"].replace("-", "")

    prov_time_acc: Dict[str, float] = dict(provider_timings or {})
    shared_time_acc: Dict[str, float] = {
        "reflectance_conversion_sec": 0.0,
        "masking_sec": 0.0,
        "reprojection_sec": 0.0,
        "resampling_sec": 0.0,
        "median_generation_sec": 0.0,
        "geotiff_writing_sec": 0.0,
    }
    io_timing_acc: Dict[str, float] = {}

    if feedback:
        feedback.pushInfo(f"=== Starting Temporal Median Composite ({len(sorted_scenes)} scenes | Provider: {provider_key}) ===")
        feedback.pushInfo(f"Satellite: {satellite_label} | Range: {sorted_scenes[0]['date']} to {sorted_scenes[-1]['date']} | Res: {eff_pixel_size}m")
        feedback.pushInfo(f"Water-Only: {water_only} (Policy: {water_temporal_policy}, Threshold: {water_occurrence_threshold}%)")

    t_grid_start = time.perf_counter()
    grid = build_target_grid(aoi_geojson, target_crs=target_crs, pixel_size=eff_pixel_size)
    shared_time_acc["reprojection_sec"] += (time.perf_counter() - t_grid_start)
    height, width = grid["height"], grid["width"]
    aoi_poly_mask = grid["aoi_mask"]

    if feedback:
        feedback.pushInfo(f"Target Grid: {width}x{height} pixels | CRS: {grid['crs']} | Chunk Size: {chunk_size}x{chunk_size}")

    num_bands = len(target_bands)
    composite_bands = [np.full((height, width), -9999.0, dtype=np.float32) for _ in range(num_bands)]
    valid_count_raster = np.zeros((height, width), dtype=np.int16)
    temp_std_raster = np.full((height, width), -9999.0, dtype=np.float32)
    water_occurrence_raster = np.full((height, width), -9999.0, dtype=np.float32)
    final_water_mask = np.zeros((height, width), dtype=np.uint8)

    num_chunks_y = int(np.ceil(height / chunk_size))
    num_chunks_x = int(np.ceil(width / chunk_size))
    total_chunks = num_chunks_y * num_chunks_x
    chunk_idx = 0

    for cy in range(num_chunks_y):
        y0 = cy * chunk_size
        y1 = min(height, (cy + 1) * chunk_size)
        ch_h = y1 - y0

        for cx in range(num_chunks_x):
            x0 = cx * chunk_size
            x1 = min(width, (cx + 1) * chunk_size)
            ch_w = x1 - x0

            chunk_idx += 1
            if feedback and hasattr(feedback, "isCanceled") and feedback.isCanceled():
                raise ProcessorError("Temporal Composite processing canceled by user.")

            chunk_aoi = aoi_poly_mask[y0:y1, x0:x1]
            if not np.any(chunk_aoi):
                continue

            if feedback:
                feedback.pushInfo(f"Processing chunk [{chunk_idx}/{total_chunks}] ({ch_w}x{ch_h} px)...")
                if hasattr(feedback, "setProgress"):
                    feedback.setProgress(int((chunk_idx / total_chunks) * 100))

            chunk_transform = grid["transform"] * rasterio.Affine.translation(x0, y0)

            band_stacks = {b_id: [] for b_id in target_bands}
            unmasked_band_stacks = {b_id: [] for b_id in target_bands}
            water_stacks = []
            valid_observation_counts = np.zeros((ch_h, ch_w), dtype=np.int16)

            for s_meta in sorted_scenes:
                s_prov = normalize_provider_key(s_meta.get("provider", provider_key))
                s_col = s_meta.get("collection", collection_label)
                cloud_invalid = np.zeros((ch_h, ch_w), dtype=bool)
                off_swath_invalid = np.zeros((ch_h, ch_w), dtype=bool)
                scene_is_water = None
                scl_chunk = None
                qa_chunk = None
                radsat_chunk = None

                if not is_landsat:
                    if "SCL" in s_meta["assets"]:
                        try:
                            scl_chunk, _ = read_raster_into_grid(
                                s_meta["assets"]["SCL"]["href"],
                                target_crs=grid["crs"],
                                target_transform=chunk_transform,
                                target_shape=(ch_h, ch_w),
                                is_classification=True,
                                src_nodata=0,
                                target_nodata=0,
                                provider=s_prov,
                                collection=s_col,
                                timing_out=io_timing_acc
                            )
                            t_m0 = time.perf_counter()
                            off_swath_invalid |= (scl_chunk == 0)
                            for c in [0, 1, 3, 8, 9, 10, 11]:
                                cloud_invalid |= (scl_chunk == c)
                            scene_is_water = (scl_chunk == 6)
                            shared_time_acc["masking_sec"] += (time.perf_counter() - t_m0)
                        except Exception:
                            scl_chunk = None
                else:
                    if "QA_PIXEL" in s_meta["assets"]:
                        try:
                            qa_chunk, _ = read_raster_into_grid(
                                s_meta["assets"]["QA_PIXEL"]["href"],
                                target_crs=grid["crs"],
                                target_transform=chunk_transform,
                                target_shape=(ch_h, ch_w),
                                is_classification=True,
                                src_nodata=1,
                                target_nodata=1,
                                provider=s_prov,
                                collection=s_col,
                                timing_out=io_timing_acc
                            )
                            t_m0 = time.perf_counter()
                            qa_dec = decode_landsat_qa_pixel(qa_chunk)
                            off_swath_invalid |= qa_dec["fill"]
                            cloud_invalid |= qa_dec["cloud_invalid_mask"]
                            scene_is_water = qa_dec["water"]
                            shared_time_acc["masking_sec"] += (time.perf_counter() - t_m0)
                        except Exception:
                            qa_chunk = None

                    if "QA_RADSAT" in s_meta["assets"]:
                        try:
                            radsat_chunk, _ = read_raster_into_grid(
                                s_meta["assets"]["QA_RADSAT"]["href"],
                                target_crs=grid["crs"],
                                target_transform=chunk_transform,
                                target_shape=(ch_h, ch_w),
                                is_classification=True,
                                src_nodata=0,
                                target_nodata=0,
                                provider=s_prov,
                                collection=s_col,
                                timing_out=io_timing_acc
                            )
                            t_m0 = time.perf_counter()
                            cloud_invalid |= decode_landsat_qa_radsat(radsat_chunk)
                            shared_time_acc["masking_sec"] += (time.perf_counter() - t_m0)
                        except Exception:
                            radsat_chunk = None

                scene_bands_valid = True
                chunk_band_refls = {}
                chunk_unmasked_refls = {}

                for b_id in target_bands:
                    if b_id not in s_meta["assets"]:
                        scene_bands_valid = False
                        break

                    b_meta = s_meta["assets"][b_id]
                    try:
                        raw_chunk, _ = read_raster_into_grid(
                            b_meta["href"],
                            target_crs=grid["crs"],
                            target_transform=chunk_transform,
                            target_shape=(ch_h, ch_w),
                            is_classification=False,
                            src_nodata=b_meta.get("nodata", 0),
                            target_nodata=0,
                            provider=s_prov,
                            collection=s_col,
                            timing_out=io_timing_acc
                        )
                    except Exception:
                        scene_bands_valid = False
                        break

                    t_r0 = time.perf_counter()
                    if is_landsat:
                        refl_chunk, v_mask, _ = normalize_landsat_surface_reflectance(
                            raw_chunk,
                            scale=b_meta.get("scale", 0.0000275),
                            offset=b_meta.get("offset", -0.2),
                            raw_nodata=b_meta.get("nodata", 0),
                            output_nodata=np.nan
                        )
                    else:
                        refl_chunk, v_mask, _ = normalize_surface_reflectance(
                            raw_chunk,
                            scale=b_meta.get("scale", 0.0001),
                            offset=b_meta.get("offset", None),
                            raw_nodata=b_meta.get("nodata", 0),
                            processing_baseline=s_meta.get("processing_baseline"),
                            boa_offset_applied=s_meta.get("boa_offset_applied"),
                            output_nodata=np.nan
                        )
                    shared_time_acc["reflectance_conversion_sec"] += (time.perf_counter() - t_r0)

                    t_m0 = time.perf_counter()
                    unmasked_chunk = refl_chunk.copy()
                    unmasked_chunk[(~v_mask) | off_swath_invalid | (~chunk_aoi)] = np.nan
                    chunk_unmasked_refls[b_id] = unmasked_chunk

                    bad_px = (~v_mask) | cloud_invalid | (~chunk_aoi)
                    refl_chunk[bad_px] = np.nan
                    shared_time_acc["masking_sec"] += (time.perf_counter() - t_m0)
                    chunk_band_refls[b_id] = refl_chunk

                if not scene_bands_valid or not chunk_band_refls:
                    continue

                if is_landsat:
                    green_k, nir_k, swir_k = "SR_B3", "SR_B5", "SR_B6"
                else:
                    green_k, nir_k, swir_k = "B03", "B08", "B11"

                for aux_b in (green_k, nir_k):
                    if aux_b not in chunk_band_refls and aux_b in s_meta["assets"]:
                        try:
                            aux_meta = s_meta["assets"][aux_b]
                            aux_raw, _ = read_raster_into_grid(
                                aux_meta["href"],
                                target_crs=grid["crs"],
                                target_transform=chunk_transform,
                                target_shape=(ch_h, ch_w),
                                is_classification=False,
                                src_nodata=aux_meta.get("nodata", 0),
                                target_nodata=0,
                                provider=s_prov,
                                collection=s_col,
                                timing_out=io_timing_acc
                            )
                            t_r0 = time.perf_counter()
                            if is_landsat:
                                aux_refl, aux_vm, _ = normalize_landsat_surface_reflectance(
                                    aux_raw,
                                    scale=aux_meta.get("scale", 0.0000275),
                                    offset=aux_meta.get("offset", -0.2),
                                    raw_nodata=aux_meta.get("nodata", 0),
                                    output_nodata=np.nan
                                )
                            else:
                                aux_refl, aux_vm, _ = normalize_surface_reflectance(
                                    aux_raw,
                                    scale=aux_meta.get("scale", 0.0001),
                                    offset=aux_meta.get("offset", None),
                                    raw_nodata=aux_meta.get("nodata", 0),
                                    processing_baseline=s_meta.get("processing_baseline"),
                                    boa_offset_applied=s_meta.get("boa_offset_applied"),
                                    output_nodata=np.nan
                                )
                            shared_time_acc["reflectance_conversion_sec"] += (time.perf_counter() - t_r0)
                            aux_refl[(~aux_vm) | cloud_invalid | (~chunk_aoi)] = np.nan
                            chunk_band_refls[aux_b] = aux_refl
                        except Exception:
                            pass

                t_wm0 = time.perf_counter()
                if green_k in chunk_band_refls and nir_k in chunk_band_refls:
                    green_ch = chunk_band_refls[green_k]
                    nir_ch = chunk_band_refls[nir_k]
                    swir_ch = chunk_band_refls.get(swir_k, None)
                    w_mask, _, _ = create_water_mask(
                        scl=scl_chunk,
                        qa_pixel=qa_chunk,
                        qa_radsat=radsat_chunk,
                        green_refl=green_ch,
                        nir_refl=nir_ch,
                        swir1_refl=swir_ch,
                        valid_mask=chunk_aoi & ~cloud_invalid,
                        method="hybrid",
                        mode="permissive"
                    )
                    water_stacks.append(w_mask)
                elif scene_is_water is not None:
                    water_stacks.append(scene_is_water & chunk_aoi & ~cloud_invalid)
                shared_time_acc["masking_sec"] += (time.perf_counter() - t_wm0)

                for b_id in target_bands:
                    band_stacks[b_id].append(chunk_band_refls[b_id])
                    unmasked_band_stacks[b_id].append(chunk_unmasked_refls[b_id])

            if not band_stacks[target_bands[0]]:
                continue

            t_med0 = time.perf_counter()
            ref_stack = np.array(band_stacks[target_bands[0]], dtype=np.float32)
            unmasked_ref_stack = np.array(unmasked_band_stacks[target_bands[0]], dtype=np.float32)
            valid_counts = np.sum(np.isfinite(ref_stack), axis=0)
            unmasked_valid_counts = np.sum(np.isfinite(unmasked_ref_stack), axis=0)

            eff_valid_counts = valid_counts.copy()
            if not water_only:
                recover_mask = (valid_counts < min_valid_observations) & (unmasked_valid_counts >= min_valid_observations) & chunk_aoi
                eff_valid_counts[recover_mask] = unmasked_valid_counts[recover_mask]

            valid_observation_counts[chunk_aoi] = eff_valid_counts[chunk_aoi]
            valid_count_raster[y0:y1, x0:x1] = valid_observation_counts

            sufficient_obs_mask = (eff_valid_counts >= min_valid_observations) & chunk_aoi

            chunk_water_bool = np.ones((ch_h, ch_w), dtype=bool)
            if water_stacks:
                w_stack_arr = np.array(water_stacks, dtype=np.float32)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=RuntimeWarning)
                    water_freq = np.zeros((ch_h, ch_w), dtype=np.float32)
                    has_obs = valid_counts > 0
                    water_sum = np.sum(w_stack_arr, axis=0)
                    water_freq[has_obs] = (water_sum[has_obs] / valid_counts[has_obs]) * 100.0
                    water_freq[~chunk_aoi | (eff_valid_counts == 0)] = -9999.0
                    water_occurrence_raster[y0:y1, x0:x1] = water_freq

                if water_temporal_policy == "consensus":
                    chunk_water_bool = (water_freq >= water_occurrence_threshold) & sufficient_obs_mask
                elif water_temporal_policy == "intersection":
                    chunk_water_bool = (water_freq >= 99.9) & sufficient_obs_mask
                elif water_temporal_policy == "union":
                    chunk_water_bool = (water_freq > 0.0) & sufficient_obs_mask
                else:
                    chunk_water_bool = (water_freq >= water_occurrence_threshold) & sufficient_obs_mask

                w_u8 = np.full((ch_h, ch_w), 255, dtype=np.uint8)
                w_u8[sufficient_obs_mask & ~chunk_water_bool] = 0
                w_u8[sufficient_obs_mask & chunk_water_bool] = 1
                final_water_mask[y0:y1, x0:x1] = w_u8

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                std_vals = np.nanstd(ref_stack, axis=0)
                if not water_only:
                    fallback_std_vals = np.nanstd(unmasked_ref_stack, axis=0)
                    recover_std = (valid_counts < 2) & (unmasked_valid_counts >= 2) & chunk_aoi
                    std_vals[recover_std] = fallback_std_vals[recover_std]
                std_out = np.full((ch_h, ch_w), -9999.0, dtype=np.float32)
                std_valid = (eff_valid_counts >= 2) & chunk_aoi
                if water_only:
                    std_valid &= chunk_water_bool
                std_out[std_valid] = std_vals[std_valid]
                temp_std_raster[y0:y1, x0:x1] = std_out

                for b_idx, b_id in enumerate(target_bands):
                    b_stack = np.array(band_stacks[b_id], dtype=np.float32)
                    unmasked_b_stack = np.array(unmasked_band_stacks[b_id], dtype=np.float32)
                    med_vals = np.nanmedian(b_stack, axis=0)
                    if not water_only:
                        fallback_med_vals = np.nanmedian(unmasked_b_stack, axis=0)
                        recover_px = (valid_counts < min_valid_observations) & chunk_aoi & np.isfinite(fallback_med_vals)
                        med_vals[recover_px] = fallback_med_vals[recover_px]
                    b_out = np.full((ch_h, ch_w), -9999.0, dtype=np.float32)

                    keep_px = sufficient_obs_mask & np.isfinite(med_vals)
                    if water_only:
                        keep_px &= chunk_water_bool

                    b_out[keep_px] = med_vals[keep_px]
                    composite_bands[b_idx][y0:y1, x0:x1] = b_out
            shared_time_acc["median_generation_sec"] += (time.perf_counter() - t_med0)

    os.makedirs(output_dir, exist_ok=True)
    mode_str = "WaterOnly" if water_only else "BOA_Float32"
    base_name = f"{prefix}_{date_start}_{date_end}_Median_{mode_str}"
    comp_path = os.path.join(output_dir, f"{base_name}.tif")

    if feedback:
        feedback.pushInfo(f"Exporting Temporal Median Composite GeoTIFF to: {comp_path}")

    scale_val = 0.0000275 if is_landsat else 0.0001
    offset_val = -0.2 if is_landsat else -0.1

    t_write_start = time.perf_counter()
    save_multiband_geotiff(
        comp_path,
        band_arrays=composite_bands,
        band_names=target_bands,
        grid=grid,
        nodata_val=-9999.0,
        metadata_extra={
            "COMPOSITE_METHOD": "Temporal Median (nanmedian)",
            "SATELLITE": satellite_label,
            "SENSOR": sensor_label,
            "PROVIDER": provider_key,
            "STAC_COLLECTION": collection_label,
            "NUM_SCENES": len(sorted_scenes),
            "DATE_START": sorted_scenes[0]["date"],
            "DATE_END": sorted_scenes[-1]["date"],
            "WATER_ONLY_APPLIED": str(water_only),
            "WATER_TEMPORAL_POLICY": water_temporal_policy,
            "SCALE_FACTOR": str(scale_val),
            "OFFSET": str(offset_val),
            "PIXEL_SIZE": str(eff_pixel_size)
        }
    )

    outputs = {
        "composite_reflectance": comp_path,
        "satellite": satellite_label,
        "provider": provider_key,
        "num_scenes": len(sorted_scenes),
        "date_start": sorted_scenes[0]["date"],
        "date_end": sorted_scenes[-1]["date"],
        "grid": grid,
        "bands": target_bands
    }

    if save_valid_count_raster:
        vc_path = os.path.join(output_dir, f"{base_name}_ValidCount.tif")
        valid_count_raster[~aoi_poly_mask] = -1
        save_singleband_raster(
            vc_path,
            valid_count_raster,
            grid=grid,
            dtype="int16",
            nodata_val=-1,
            description="Valid Observation Count per Pixel"
        )
        outputs["valid_count"] = vc_path

    if save_temp_std_raster:
        std_path = os.path.join(output_dir, f"{base_name}_TemporalStd.tif")
        save_singleband_raster(
            std_path,
            temp_std_raster,
            grid=grid,
            dtype="float32",
            nodata_val=-9999.0,
            description=f"Temporal Standard Deviation ({target_bands[0]})"
        )
        outputs["temporal_std"] = std_path

    if save_water_occurrence_raster:
        wo_path = os.path.join(output_dir, f"{base_name}_WaterOccurrence.tif")
        save_singleband_raster(
            wo_path,
            water_occurrence_raster,
            grid=grid,
            dtype="float32",
            nodata_val=-9999.0,
            description="Water Occurrence Frequency (%)"
        )
        outputs["water_occurrence"] = wo_path

    mask_path = os.path.join(output_dir, f"{base_name}_WaterMask.tif")
    final_water_mask[~aoi_poly_mask] = 255
    save_singleband_raster(
        mask_path,
        final_water_mask,
        grid=grid,
        dtype="uint8",
        nodata_val=255,
        description="Composite Water Mask: 1=Water, 0=Land, 255=NoData"
    )
    outputs["water_mask"] = mask_path
    shared_time_acc["geotiff_writing_sec"] += (time.perf_counter() - t_write_start)

    prov_time_acc["url_signing_sec"] = prov_time_acc.get("url_signing_sec", 0.0) + io_timing_acc.get("url_signing_sec", 0.0)
    prov_time_acc["remote_asset_access_sec"] = prov_time_acc.get("remote_asset_access_sec", 0.0) + io_timing_acc.get("remote_asset_access_sec", 0.0)
    prov_time_acc["remote_raster_reading_sec"] = prov_time_acc.get("remote_raster_reading_sec", 0.0) + io_timing_acc.get("remote_raster_reading_sec", 0.0)
    shared_time_acc["reprojection_sec"] += io_timing_acc.get("reprojection_sec", 0.0)
    shared_time_acc["resampling_sec"] += io_timing_acc.get("resampling_sec", 0.0)

    perf_diagnostics = _build_performance_diagnostics(
        provider=provider_key,
        provider_timings=prov_time_acc,
        shared_timings=shared_time_acc
    )
    outputs["performance_diagnostics"] = perf_diagnostics

    json_path = os.path.join(output_dir, f"{base_name}_Processing_Metadata.json")
    meta_doc = {
        "provider": provider_key,
        "satellite": satellite_label,
        "platforms": sorted(list(platforms_present)),
        "sensor": sensor_label,
        "stac_collection": collection_label,
        "processing_mode": "Temporal Median Composite",
        "num_scenes": len(sorted_scenes),
        "date_range": [sorted_scenes[0]["date"], sorted_scenes[-1]["date"]],
        "contributing_scenes": [
            {
                "id": s["id"],
                "platform": s.get("platform"),
                "date": s["date"],
                "cloud_cover": s["cloud_cover"],
                "tile": s.get("mgrs_tile", "")
            }
            for s in sorted_scenes
        ],
        "crs": str(grid["crs"]),
        "spatial_resolution_meters": eff_pixel_size,
        "dimensions": [width, height],
        "input_data_type": "uint16",
        "output_data_type": "float32",
        "scale_factor": scale_val,
        "offset": offset_val,
        "scaling_applied_by": "Bathymetrix-AI STAC Reader (Surface Reflectance, already scaled)",
        "bands": target_bands,
        "water_only": water_only,
        "water_temporal_policy": water_temporal_policy,
        "water_occurrence_threshold": water_occurrence_threshold,
        "performance_diagnostics": perf_diagnostics,
        "generation_timestamp": datetime.now().isoformat(),
        "export_path": comp_path
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_doc, f, indent=2)
    outputs["metadata_json"] = json_path

    if feedback:
        feedback.pushInfo(f"=== Temporal Median Composite Completed Successfully: {comp_path} ===")

    return outputs
