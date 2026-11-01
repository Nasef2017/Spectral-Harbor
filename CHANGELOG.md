# Changelog

## 1.4.1 (2026-11-01)
- **Stationary High-Albedo Coastal Target Preservation (`water_only = False`)**: Separated true sensor off-swath/fill `NoData` (`SCL == 0` for Sentinel-2A; `QA_PIXEL` Bit 0 Fill for Landsat-8/9) from `QA/SCL` cloud/shadow/snow/saturation flags in `vendor/bathymetrix_stac/processor.py` (`process_temporal_median_composite`) and `engine.py`. When `water_only=False`, stationary bright coastal features (e.g., concrete breakwaters, bright carbonate sandbars, surf zones) misclassified by `SCL`/`QA` across all scenes fall back to `np.nanmedian` of valid unmasked reflectance rather than being erased to `-9999.0`, while transient clouds continue to be removed.
- **Memory-Safe `512x512` Chunked Temporal Compositing**: Maintained strict `512x512` spatial chunking (`chunk_size = 512`) in `process_temporal_median_composite` to protect RAM when compositing large scenes (`>100 MB`).
- **Expanded Regression & Equivalence Test Suite**: Added `test_water_only_false_preserves_stationary_bright_targets_in_composite` to `tests/test_spectral_harbor_providers.py` (`9/9` tests passing).

## 1.4.0 (2026-11-01)
- Added **Microsoft Planetary Computer** (`https://planetarycomputer.microsoft.com/api/stac/v1`) as a second STAC imagery provider alongside **Element 84 Earth Search** (`https://earth-search.aws.element84.com/v1`), sharing the exact same downstream scientific processing pipeline.
- Restricted provider-specific logic to STAC endpoint/collection selection (`sentinel-2-c1-l2a` vs `sentinel-2-l2a`, `landsat-c2-l2`), scene discovery and metadata normalization (`normalize_feature`), canonical band/asset mapping, and Azure Blob SAS token signing (`sign_planetary_computer_url`, `asset_url`).
- Removed `CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.TIF'` in `vendor/bathymetrix_stac/grid.py` and `engine.py` so GDAL `/vsicurl/` opens query-signed COG URLs (`?st=...&sig=...`) without rejecting query parameters.
- Added on-demand loading of required water-index bands (`B03`/`B08`/`B11` or `SR_B3`/`SR_B5`/`SR_B6`) when water-only masking is enabled with custom band subsets.
- Added separated performance diagnostics (`provider_specific_timings_sec` vs `shared_processing_timings_sec`) in `_Processing_Metadata.json` and `provenance.json`, plus regression/equivalence tests (`tests/test_spectral_harbor_providers.py`) and benchmarks (`tests/benchmark_providers.py`).

## 1.3.4 (2026-10-27)
- Use descriptive scene and composite filenames directly (e.g., S2A_T35RPQ_20260902T085432_L2A_BOA_Float32.tif) for saved rasters and QGIS map layers.
- Removed duplicate generic `reflectance.tif` creation, eliminating redundant file clutter.
- Standalone QGIS 4 Processing integration for Sentinel-2A and Landsat 8/9 STAC downloading.
