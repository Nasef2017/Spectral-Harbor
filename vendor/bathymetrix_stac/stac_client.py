# -*- coding: utf-8 -*-
"""
core/stac/stac_client.py
Multi-Provider STAC API Client & Normalization Layer for:
- Provider 0: Element 84 Earth Search (https://earth-search.aws.element84.com/v1)
- Provider 1: Microsoft Planetary Computer (https://planetarycomputer.microsoft.com/api/stac/v1)

Supported Sensors:
1. Sentinel-2A Level-2A Surface Reflectance
2. Landsat-8 OLI Collection 2 Level-2 Surface Reflectance
3. Landsat-9 OLI-2 Collection 2 Level-2 Surface Reflectance
4. Landsat-8/9 Combined

Sentinel-2B and Sentinel-2C are strictly excluded and rejected.
"""

import json
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Any, Tuple, Set


PROVIDER_EARTH_SEARCH = "earth_search"
PROVIDER_PLANETARY_COMPUTER = "planetary_computer"

EARTH_SEARCH_ENDPOINT = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1"
PLANETARY_COMPUTER_SAS_TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token"

S2_COLLECTION_ID = "sentinel-2-c1-l2a"
S2_PC_COLLECTION_ID = "sentinel-2-l2a"
LANDSAT_COLLECTION_ID = "landsat-c2-l2"
COLLECTION_ID = S2_COLLECTION_ID

PROVIDER_CONFIGS: Dict[str, Dict[str, Any]] = {
    PROVIDER_EARTH_SEARCH: {
        "key": PROVIDER_EARTH_SEARCH,
        "name": "Element 84 Earth Search",
        "endpoint": EARTH_SEARCH_ENDPOINT,
        "s2_collection": S2_COLLECTION_ID,
        "landsat_collection": LANDSAT_COLLECTION_ID,
        "requires_signing": False,
        "s2_platform_query_values": ["sentinel-2a"],
    },
    PROVIDER_PLANETARY_COMPUTER: {
        "key": PROVIDER_PLANETARY_COMPUTER,
        "name": "Microsoft Planetary Computer",
        "endpoint": PLANETARY_COMPUTER_ENDPOINT,
        "s2_collection": S2_PC_COLLECTION_ID,
        "landsat_collection": LANDSAT_COLLECTION_ID,
        "requires_signing": True,
        "s2_platform_query_values": ["Sentinel-2A", "sentinel-2a"],
    },
}

ALLOWED_PLATFORMS: Set[str] = {"sentinel-2a", "landsat-8", "landsat-9"}
DISALLOWED_PLATFORMS: Set[str] = {"sentinel-2b", "sentinel-2c"}

BAND_ASSET_MAP = {
    "B01": {"asset_key": "coastal", "common_name": "coastal", "desc": "Coastal Aerosol", "res": 60},
    "B02": {"asset_key": "blue", "common_name": "blue", "desc": "Blue", "res": 10},
    "B03": {"asset_key": "green", "common_name": "green", "desc": "Green", "res": 10},
    "B04": {"asset_key": "red", "common_name": "red", "desc": "Red", "res": 10},
    "B05": {"asset_key": "rededge1", "common_name": "rededge1", "desc": "Red Edge 1", "res": 20},
    "B06": {"asset_key": "rededge2", "common_name": "rededge2", "desc": "Red Edge 2", "res": 20},
    "B07": {"asset_key": "rededge3", "common_name": "rededge3", "desc": "Red Edge 3", "res": 20},
    "B08": {"asset_key": "nir", "common_name": "nir", "desc": "NIR (Broad)", "res": 10},
    "B8A": {"asset_key": "nir08", "common_name": "nir08", "desc": "Narrow NIR", "res": 20},
    "B09": {"asset_key": "nir09", "common_name": "nir09", "desc": "Water Vapour", "res": 60},
    "B11": {"asset_key": "swir16", "common_name": "swir16", "desc": "SWIR 1", "res": 20},
    "B12": {"asset_key": "swir22", "common_name": "swir22", "desc": "SWIR 2", "res": 20},
}

S2A_PROVIDER_ASSET_MAP: Dict[str, List[str]] = {
    "B01": ["coastal", "B01", "b01"],
    "B02": ["blue", "B02", "b02"],
    "B03": ["green", "B03", "b03"],
    "B04": ["red", "B04", "b04"],
    "B05": ["rededge1", "B05", "b05"],
    "B06": ["rededge2", "B06", "b06"],
    "B07": ["rededge3", "B07", "b07"],
    "B08": ["nir", "B08", "b08"],
    "B8A": ["nir08", "B8A", "b8a"],
    "B09": ["nir09", "B09", "b09"],
    "B11": ["swir16", "B11", "b11"],
    "B12": ["swir22", "B12", "b12"],
}

AUX_ASSET_MAP = {
    "SCL": {"asset_key": "scl", "desc": "Scene Classification Layer", "res": 20},
    "AOT": {"asset_key": "aot", "desc": "Aerosol Optical Thickness", "res": 20},
    "WVP": {"asset_key": "wvp", "desc": "Water Vapour Content", "res": 20},
    "visual": {"asset_key": "visual", "desc": "True Color Image (TCI)", "res": 10},
    "preview": {"asset_key": "preview", "desc": "Preview Image", "res": 343},
    "thumbnail": {"asset_key": "thumbnail", "desc": "Thumbnail Image", "res": 343},
}

AUX_PROVIDER_ASSET_MAP: Dict[str, List[str]] = {
    "SCL": ["scl", "SCL"],
    "AOT": ["aot", "AOT"],
    "WVP": ["wvp", "WVP"],
    "visual": ["visual", "TCI"],
    "preview": ["preview", "rendered_preview", "thumbnail"],
    "thumbnail": ["thumbnail", "rendered_preview", "preview"],
}

ORDERED_BAND_LIST = [
    "B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12"
]

LANDSAT_BAND_ASSET_MAP = {
    "SR_B1": {"asset_key": "coastal", "common_name": "coastal", "desc": "Coastal Aerosol", "res": 30},
    "SR_B2": {"asset_key": "blue", "common_name": "blue", "desc": "Blue", "res": 30},
    "SR_B3": {"asset_key": "green", "common_name": "green", "desc": "Green", "res": 30},
    "SR_B4": {"asset_key": "red", "common_name": "red", "desc": "Red", "res": 30},
    "SR_B5": {"asset_key": "nir08", "common_name": "nir08", "desc": "NIR", "res": 30},
    "SR_B6": {"asset_key": "swir16", "common_name": "swir16", "desc": "SWIR 1", "res": 30},
    "SR_B7": {"asset_key": "swir22", "common_name": "swir22", "desc": "SWIR 2", "res": 30},
}

LANDSAT_AUX_ASSET_MAP = {
    "QA_PIXEL": {"asset_key": "qa_pixel", "desc": "Pixel Quality Assessment", "res": 30},
    "QA_RADSAT": {"asset_key": "qa_radsat", "desc": "Radiometric Saturation QA", "res": 30},
    "SR_QA_AEROSOL": {"asset_key": "qa_aerosol", "desc": "Surface Reflectance Aerosol QA", "res": 30},
    "thumbnail": {"asset_key": "thumbnail", "desc": "Thumbnail Image", "res": 300},
    "reduced_resolution_browse": {"asset_key": "reduced_resolution_browse", "desc": "Browse Image", "res": 300},
}

LANDSAT_ORDERED_BAND_LIST = [
    "SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"
]


class _SasTokenCache:
    tokens: Dict[str, Tuple[str, float]] = {}


_SAS_TOKEN_CACHE = _SasTokenCache()


def normalize_provider_key(provider: Optional[str]) -> str:
    """Normalize user or UI provider string into canonical provider key."""
    if not provider:
        return PROVIDER_EARTH_SEARCH
    p = str(provider).strip().lower().replace("-", "_").replace(" ", "_")
    if p in ("earth_search", "element_84_earth_search", "element84", "earthsearch", "aws", "0"):
        return PROVIDER_EARTH_SEARCH
    if p in ("planetary_computer", "microsoft_planetary_computer", "mpc", "pc", "microsoft", "1"):
        return PROVIDER_PLANETARY_COMPUTER
    return PROVIDER_EARTH_SEARCH


def get_planetary_computer_sas_token(collection_id: str, timeout: int = 10) -> Optional[str]:
    """Fetch and cache a public SAS token from Microsoft Planetary Computer's signing API."""
    now = time.time()
    cached = _SAS_TOKEN_CACHE.tokens.get(collection_id)
    if cached and cached[1] > now + 300:
        return cached[0]

    url = f"{PLANETARY_COMPUTER_SAS_TOKEN_URL}/{collection_id}"
    if not str(url).lower().startswith(("http://", "https://")):
        return None

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SpectralHarbor-QGIS-Plugin/1.4.0",
                "Accept": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("token", "").lstrip("?")
            if token:
                _SAS_TOKEN_CACHE.tokens[collection_id] = (token, now + 2700)
                return token
    except Exception:
        return None
    return None


def get_landsat_sas_token(timeout: int = 10) -> Optional[str]:
    """Fetch and cache a public SAS token for the Landsat Collection 2 Level-2 container."""
    return get_planetary_computer_sas_token(LANDSAT_COLLECTION_ID, timeout=timeout)


def sign_planetary_computer_url(url: str, collection_id: str = S2_PC_COLLECTION_ID) -> str:
    """Sign an Azure Blob Storage asset URL using Microsoft Planetary Computer's SAS token."""
    if not url or not isinstance(url, str):
        return url
    if "?" in url and ("sig=" in url or "sv=" in url):
        return url
    if ".blob.core.windows.net" not in url:
        return url

    token = get_planetary_computer_sas_token(collection_id)
    if not token:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{token}"


def resolve_landsat_public_https_url(s3_or_https_url: str) -> str:
    """Translate AWS Requester-Pays 's3://usgs-landsat/...' URIs or unsigned blob URLs into signed HTTPS URLs."""
    if not s3_or_https_url or not isinstance(s3_or_https_url, str):
        return s3_or_https_url

    if "s3://usgs-landsat/" in s3_or_https_url:
        token = get_landsat_sas_token()
        if token:
            if "s3://usgs-landsat/collection02/" in s3_or_https_url:
                rel = s3_or_https_url.split("s3://usgs-landsat/collection02/")[-1]
            else:
                rel = s3_or_https_url.split("s3://usgs-landsat/")[-1]
            return f"https://landsateuwest.blob.core.windows.net/landsat-c2/{rel}?{token}"

    if ".blob.core.windows.net" in s3_or_https_url:
        return sign_planetary_computer_url(s3_or_https_url, collection_id=LANDSAT_COLLECTION_ID)

    return s3_or_https_url


def resolve_downloadable_asset_url(
    raw_href: str,
    provider: str = PROVIDER_EARTH_SEARCH,
    collection: str = S2_COLLECTION_ID
) -> str:
    """Unified provider-aware resolver for downloadable raster asset URLs."""
    if not raw_href or not isinstance(raw_href, str):
        return raw_href

    if "s3://usgs-landsat/" in raw_href:
        return resolve_landsat_public_https_url(raw_href)

    norm_provider = normalize_provider_key(provider)
    if norm_provider == PROVIDER_PLANETARY_COMPUTER or ".blob.core.windows.net" in raw_href:
        col = collection
        if col == S2_COLLECTION_ID:
            col = S2_PC_COLLECTION_ID
        return sign_planetary_computer_url(raw_href, collection_id=col)

    return raw_href


def resolve_asset_for_band(
    item_assets: dict,
    band_id: str,
    platform: Optional[str] = None,
    provider: Optional[str] = None
) -> Optional[dict]:
    """
    Resolve asset metadata (href, raster:bands, scale, offset) for a given band ID
    across both Element 84 Earth Search and Microsoft Planetary Computer.
    """
    # 1. Check Sentinel-2 maps
    if band_id in S2A_PROVIDER_ASSET_MAP:
        for candidate_key in S2A_PROVIDER_ASSET_MAP[band_id]:
            if candidate_key in item_assets:
                return item_assets[candidate_key]
    if band_id in BAND_ASSET_MAP:
        key = BAND_ASSET_MAP[band_id]["asset_key"]
        if key in item_assets:
            return item_assets[key]
    if band_id in AUX_PROVIDER_ASSET_MAP:
        for candidate_key in AUX_PROVIDER_ASSET_MAP[band_id]:
            if candidate_key in item_assets:
                return item_assets[candidate_key]
    if band_id in AUX_ASSET_MAP:
        key = AUX_ASSET_MAP[band_id]["asset_key"]
        if key in item_assets:
            return item_assets[key]

    # 2. Check Landsat maps
    if band_id in LANDSAT_BAND_ASSET_MAP:
        key = LANDSAT_BAND_ASSET_MAP[band_id]["asset_key"]
        if key in item_assets:
            return item_assets[key]
    if band_id in LANDSAT_AUX_ASSET_MAP:
        key = LANDSAT_AUX_ASSET_MAP[band_id]["asset_key"]
        if key in item_assets:
            return item_assets[key]

    # 3. Direct key match
    if band_id in item_assets:
        return item_assets[band_id]

    for k, asset in item_assets.items():
        if k.lower() == band_id.lower():
            return asset

    # 4. Search through eo:bands
    b_clean = band_id.upper().replace("SR_", "").replace("B0", "B")
    for k, asset in item_assets.items():
        if "eo:bands" in asset and isinstance(asset["eo:bands"], list):
            for eb in asset["eo:bands"]:
                name = str(eb.get("name", "")).upper()
                c_name = str(eb.get("common_name", "")).lower()
                if (
                    name == band_id.upper()
                    or name == b_clean
                    or name == f"OLI_{b_clean}"
                    or name == f"OLI_{band_id.upper()}"
                    or c_name == band_id.lower()
                ):
                    return asset

    # 5. Search through href filename suffix
    target_suffixes = [f"{band_id}.tif".lower(), f"_{band_id}.tif".lower()]
    if band_id.startswith("SR_"):
        b_num = band_id.replace("SR_", "")
        target_suffixes.extend([f"_{b_num}.tif".lower(), f"sr_{b_num}.tif".lower()])
    for k, asset in item_assets.items():
        href = asset.get("href", "").split("?")[0].lower()
        for suf in target_suffixes:
            if href.endswith(suf):
                return asset

    return None
