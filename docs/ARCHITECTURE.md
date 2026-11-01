# Spectral Harbor architecture

## QGIS interface

The Qt6 scene browser and Processing provider run in QGIS. They send JSON jobs to fresh Python child processes from the same QGIS distribution. Both interfaces include an **Imagery provider** selector (`Element 84 Earth Search` or `Microsoft Planetary Computer`). The band checklist follows the satellite. Pixel size defaults to 10 m for Sentinel-2A and 30 m for Landsat; untouched defaults follow satellite changes and manual overrides are retained. Zero means automatic selection in Batch/Modeler.

## Provider abstraction and shared processing pipeline

Provider-specific logic in `catalog.py` is strictly limited to:
1. STAC endpoint and collection selection (`sentinel-2-c1-l2a` / `landsat-c2-l2` on Earth Search vs. `sentinel-2-l2a` / `landsat-c2-l2` on Microsoft Planetary Computer).
2. Scene discovery and metadata normalization (`normalize_feature`).
3. STAC asset and band-name mapping (`coastal`..`swir22`/`scl` vs. `B01`..`B12`/`B8A`/`SCL`, `SR_B1`..`SR_B7`/`QA_PIXEL`/`QA_RADSAT`).
4. Authentication and Azure Blob SAS URL signing where required (`sign_planetary_computer_url`, `asset_url`).
5. Resolving downloadable raster assets (`resolve_downloadable_asset_url`).

After normalization, both providers feed into the **exact same shared processing pipeline** (`reference_adapter.py` and `vendor/bathymetrix_stac/`). Search uses GDAL/OGR geometry tools for ROI coverage checks. Raster processing runs separately with rasterio and shapely, without loading QGIS, pyproj or osgeo in that process.

Images are read as remote COG windows, normalized using sensor-specific calibration/baseline rules, and clipped to the ROI. The UTM grid follows the ROI centroid and snapped bounds. Reflectance is resampled bilinearly and categorical QA by nearest neighbour. The median uses 512-pixel chunks for scene observations; full output arrays remain in memory. A 25-million-pixel guard and conservative 1.5 GB output estimate reject oversized requests.

## Transport, feedback, performance diagnostics and outputs

The reader uses an in-memory read cache, a 30-second request timeout, four retries and a two-second retry delay (`CPL_VSIL_CURL_ALLOWED_EXTENSIONS` is omitted so query-signed SAS URLs open cleanly). Public Planetary Computer and Landsat access uses short-lived SAS tokens, cached and refreshed before expiry. Stored source metadata (`_Processing_Metadata.json` and `provenance.json`) strips temporary access tokens. GUI logs redact remote URLs.

Performance diagnostics in `_Processing_Metadata.json` and `provenance.json` separate:
- **Provider-specific measurements**: `stac_search`, `asset_discovery`, `url_signing`, `remote_asset_access`, `remote_raster_reading`
- **Shared processing measurements**: `reflectance_conversion`, `masking`, `reprojection`, `resampling`, `median_generation`, `geotiff_writing`

Satellite/scene-specific products retain their descriptive filenames (`primary_raster.txt` records the primary output path). Raster `PROCESSED_BY` and `PROVIDER` tags and `scaling_applied_by` processing metadata identify Spectral Harbor; image values and scientific settings are preserved. `provenance.json` is written last. Processing schedules successful map loading on the main thread via `QgsProcessingContext.addLayerToLoadOnCompletion`.
