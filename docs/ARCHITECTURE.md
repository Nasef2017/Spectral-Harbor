# Spectral Harbor architecture

## QGIS interface

The Qt6 scene browser and Processing provider run in QGIS. They send JSON jobs to fresh Python child processes from the same QGIS distribution. The band checklist follows the satellite. Pixel size defaults to 10 m for Sentinel-2A and 30 m for Landsat; untouched defaults follow satellite changes and manual overrides are retained. Zero means automatic selection in Batch/Modeler.

## Search and raster processing

Search uses the Earth Search STAC catalog and GDAL/OGR geometry tools for ROI coverage checks. Raster processing runs separately with rasterio and shapely, without loading QGIS, pyproj or osgeo in that process. The adapter maps scene/asset metadata to the processor, forwards selected bands and feedback, and checks memory limits. Detailed source provenance, modification records and third-party licenses are retained separately in the legal/source records.

Images are read as remote COG windows, normalized using sensor-specific calibration/baseline rules, and clipped to the ROI. The UTM grid follows the ROI centroid and snapped bounds. Reflectance is resampled bilinearly and categorical QA by nearest neighbour. The median uses 512-pixel chunks for scene observations; full output arrays remain in memory. A 25-million-pixel guard and conservative 1.5 GB output estimate reject oversized requests.

Single-scene full-ROI and temporal-median quality behavior differ as documented in USER_GUIDE.md. Single-scene method/strictness/threshold controls do not replace the median's hybrid/consensus policy. Only requested reflectance bands are read, so spectral water methods need their green/NIR or green/SWIR1 inputs selected.

## Transport, feedback and outputs

The reader uses TIFF-only remote probes, a read cache, a 30-second request timeout, four retries and a two-second retry delay. Public Landsat access uses short-lived account/container tokens, cached and refreshed before expiry. Stored source metadata excludes temporary access tokens. GUI logs redact remote URLs.

SH_EVENT telemetry forwards band messages and stage progress. Processing polls a private log every 0.1 s; the scene browser uses QProcess events. Progress is not a network-byte percentage. Cancel terminates the active private child and leaves any partial run directory incomplete. No global PROJ/DLL settings are changed.

Satellite/scene-specific products retain their filenames. reflectance.tif is a byte-identical hard-link alias, with a copy fallback. Raster PROCESSED_BY tags and scaling_applied_by processing metadata identify Spectral Harbor; image values and scientific settings are preserved. provenance.json is written last. Processing schedules successful map loading on the main thread via QgsProcessingContext.addLayerToLoadOnCompletion. Count/std/occurrence rasters are median outputs.

## Dependencies and validation

Use QGIS 4 / Qt6, Python >=3.12, GDAL >=3.8, NumPy >=1.26, rasterio >=1.3 and shapely >=2.0 from one QGIS installation. Do not install mixed native binary wheels into QGIS. Search and raster processing use separate processes and native-library sets.

Tests cover catalog filtering, parameter defaults, band ordering, raster normalization/quality, composite outputs, worker isolation, progress, cancellation and map loading. Scientific-source integrity and adapted-grid hashes are recorded in the source manifest. All-band live checks cover Sentinel-2A and Landsat 8/9. See VALIDATION.md for evidence and limits.

Installed updates verify the previous release, save a backup and check every replacement file. Restart QGIS to load a new release. No other installed plugin is changed. Third-party licensing and source attribution are retained in ATTRIBUTION.md, NOTICE and the source records.

References: https://gdal.org/en/stable/user/configoptions.html ; https://rasterio.readthedocs.io/en/stable/topics/reproject.html .
