# Spectral Harbor 1.3.3 user guide

Open [offline Help](HELP.html) for the same Processing help, a full parameter reference and About.

## Processing Toolbox (recommended)

Open **Processing → Toolbox → Spectral Harbor → Satellite imagery → Download surface reflectance**. Search the Toolbox for “Spectral Harbor” or “surface reflectance”. Choose the satellite; the band checklist immediately changes between 12 Sentinel-2A and 7 Landsat bands. Select a polygon layer (check **Selected features only** to use just its highlighted polygons; leave it unchecked to unite all layer polygons) OR an extent using the normal extent selector, which supports the map canvas, a layer extent and drawing an extent. Set the dates, cloud/coverage, bands, pixel size, composition mode, optional water settings and output directory, then click **Run**. **Add reflectance to QGIS map after completion** is enabled by default: the completed `reflectance.tif` is added to the map and Layers panel automatically. Disable it for file-only runs if desired.

Single-scene mode automatically uses the lowest scene-cloud candidate that passes ROI coverage among the capped results. Median uses all passing candidates. The optional exact scene IDs field can restrict this set; IDs outside the filtered/capped search cause an explicit error. To preview and manually choose scenes first, use the separate Raster menu/toolbar browser.

This is a real Processing algorithm, with batch and Modeler support. In Batch/Modeler, BANDS is a comma-separated code string (e.g. `B02,B03,B04` or `SR_B2,SR_B3,SR_B4`); blank means all bands for the selected satellite. Processing output `OUTPUT` is the unique run directory; `REFLECTANCE` is the resulting Float32 raster, usable downstream and automatically scheduled for QGIS map loading when LOAD_OUTPUT is true (default) and the context has a project. No individual raster filename is accepted as the destination. CLI algorithm ID: `spectralharbor:download_surface_reflectance`.

All native raster work remains in a separate process. Cancel in Processing stops the private worker. QGIS's Processing log receives each band-start/completion message live; the upstream per-band/per-chunk feedback updates progress. Progress describes the processing stage, not measured network byte percentage. The satellite default is 10 m for Sentinel-2A and 30 m for Landsat 8/9. The standard dialog updates an untouched default when satellite changes and preserves a manually entered pixel size. In Batch/Modeler, zero means the satellite default. Saved explicit values from earlier runs remain explicit overrides.

## Interactive scene-browser workflow

1. Choose **Sentinel-2A**, **Landsat-8**, **Landsat-9** or **Landsat-8 & 9**. Switching satellite clears search results and rebuilds the band checklist; bands start checked.
2. Choose an ROI source and click **Capture ROI**. Supported sources: current map extent; all features in the active polygon layer; selected polygon features; a drawn rectangle; a drawn polygon (right click to finish); or explicit WGS84 west,south,east,north bounds. Reopening the plugin restores the previous map tool and cancels incomplete drawing. Polygon holes and multipart geometry are preserved. Layer features are united before processing. WGS84 always uses longitude,latitude.
3. Set inclusive UTC start/end dates, maximum **scene-wide** cloud percentage, minimum footprint coverage of ROI, candidate scene cap and output pixel size. Cloud percentage is not an ROI-specific cloud assessment. Search uses exact ROI intersection.
4. Click **Search scenes**. Search and footprint checks run in the child process. Results are sorted by scene cloud then ID among returned candidates. The candidate cap is applied before coverage filtering; this is not a globally exhaustive best-scene search. Reduce the time interval or increase the cap when needed.
5. Select one result for **Single scene**, or use Ctrl/Shift to select scenes for **Temporal median**. The first result is selected initially. Missing asset keys in selected scenes disable the affected bands. Select at least one band; the upstream sensor-specific calibration and baseline rules are applied at download time.
6. Optionally enable **Keep water pixels only** and choose a method. Choose an existing output folder, then click **Download selected**. No individual output filename or temporary layer destination is accepted. A unique subfolder is created, and only after successful processing is reflectance added to QGIS.

Search again after changing ROI, satellite, dates, cloud, coverage or candidate cap. Pixel size, bands, water settings and composition mode can be changed after search. Cancel stops the child process; partial output directories can be removed. An unfinished run has no final provenance file.

## Reflectance bands

| Sentinel-2A | Asset | Native metres |
|---|---|---:|
| B01 coastal aerosol | coastal | 60 |
| B02 blue | blue | 10 |
| B03 green | green | 10 |
| B04 red | red | 10 |
| B05/B06/B07 red edge | rededge1/rededge2/rededge3 | 20 |
| B08 broad NIR | nir | 10 |
| B8A narrow NIR | nir08 | 20 |
| B09 water vapour | nir09 | 60 |
| B11/B12 SWIR | swir16/swir22 | 20 |

Copernicus Level-2A contains 12 spectral bands; B10 cirrus is not a surface-reflectance band in this product [1]. The application deliberately uses `sentinel-2-c1-l2a`, with `platform=sentinel-2a`, rather than assuming every Sentinel-2 collection uses identical radiometry.

| Landsat 8/9 SR | Asset | Native metres |
|---|---|---:|
| SR_B1 coastal aerosol | coastal | 30 |
| SR_B2 blue | blue | 30 |
| SR_B3 green | green | 30 |
| SR_B4 red | red | 30 |
| SR_B5 NIR | nir08 | 30 |
| SR_B6/SR_B7 SWIR | swir16/swir22 | 30 |

Landsat thermal, cirrus and panchromatic bands are not offered as Level-2 SR bands [2]. SCL, QA_PIXEL and QA_RADSAT are auxiliary inputs, never reflectance bands. Only checked reflectance bands are read and exported, in the requested order. The copied upstream water logic operates on the selected reflectance inputs and available QA. Select green/NIR (and SWIR1 for MNDWI) if those methods are required; no unselected spectral bands are silently added by the reference processor.

## Scaling, quality and grid

Images are converted to physical surface reflectance using sensor-specific normalization, processing-baseline handling, existing-float detection and radiometric fallback rules. DN nodata is protected before normalization; the Float32 output uses -9999 nodata. The standard formula is DN × scale + offset (Sentinel commonly 0.0001 and -0.1; Landsat 0.0000275 and -0.2), subject to the normalization rules recorded in the processing metadata. These rules and QA behavior should be reported when using the outputs in research.

The output grid uses the ROI geometry centroid's WGS84 UTM zone and snapped pixel bounds, as the original grid. Bilinear reflectance and nearest-neighbour classification use the original rasterio reader and HTTP/cache settings. The pyproj.Transformer call is replaced by rasterio.warp.transform_geom to avoid the supplied native crash path. All processing runs outside QGIS, without importing QGIS, pyproj or osgeo in that worker.

Quality behavior now follows the original processor. Single-scene full-ROI processing does not automatically remove cloud-labelled reflectance pixels when water-only is off; enabling upstream water-only applies its mask. Temporal composites use the reference QA invalid masks, per-band nodata validity, valid-observation threshold and water policy. Optional QA read failures follow upstream warning/fallback behavior. This is a deliberate compatibility change from the stricter custom 1.0–1.2 engine. Do not assume that full-ROI single-scene output is a cloud-free product.

## Water methods

- **scl / qa**: use SCL class 6 or QA_PIXEL water bit 7 after quality masking.
- **ndwi**: `(green - NIR)/(green + NIR)` [5].
- **mndwi**: `(green - SWIR1)/(green + SWIR1)` [6].
- **hybrid, strict**: auxiliary water AND NDWI above threshold.
- **hybrid, permissive**: auxiliary water OR NDWI above threshold with green reflectance >0.005, always subject to valid-pixel and cloud masking. This is an implementation heuristic and requires local validation; it is not a separately validated published classifier.

For single scenes, the selected upstream method/strictness/threshold is passed unchanged. Temporal composites use the reference median function's hybrid/permissive water calculation and consensus default, rather than the single-scene method controls. Automatic Otsu thresholding [7] is computed per scene from finite, quality-valid ROI pixels; the original component's threshold fallback handles insufficient/constant samples. Disable automatic mode to use a fixed threshold. Thresholds and masks require local validation, especially for turbid water, built-up land, sunglint, shadows and shallow reefs. No atmospheric correction is run; inputs are already Level-2 products.

## Outputs

The processor writes satellite/scene-specific product filenames and metadata. A stable reflectance.tif alias is added for Spectral Harbor and automatically loaded into QGIS after successful completion. Where supported it is a hard link to the native primary raster; otherwise a byte-identical copy.

| File | Meaning |
|---|---|
| reflectance.tif | Alias of the native Float32 primary output, selected bands in requested order, -9999 nodata |
| S2A/L8/L9 scene-named reflectance | Original upstream single-scene product with descriptive band names and original tags |
| Composite-named reflectance | Original upstream median product |
| *_WaterMask.tif | Original categorical water mask (1 water, 0 non-water, 255 outside/invalid as upstream defines) |
| *_ValidCount.tif | Median only: original Int16 valid count from the first selected reference band |
| *_TemporalStd.tif | Median only: first selected band's temporal population standard deviation; -9999 for fewer than two valid observations/outside ROI |
| *_WaterOccurrence.tif | Median only: original water occurrence percentage, 0–100, -9999 nodata |
| *_Processing_Metadata.json | Original upstream scientific metadata and normalization provenance |
| provenance.json | Spectral Harbor completion record, configuration, source scene IDs, reference outputs, engine identity and loaded-library isolation diagnostics |

The original single-scene function does not create median count/std/occurrence files. Median works in 512 × 512 chunks, with original per-band nanmedian and QA rules. Sentinel and Landsat are never mixed in one composite; Landsat 8/9 can be combined. No cross-sensor spectral harmonization is applied.

## Limits

ROIs must be valid Polygon/MultiPolygon, within longitude [-180,180], latitude [-80,84] and no wider than six degrees. Split wide, polar or dateline areas. The processing grid uses ROI-centroid UTM; crossing a zone boundary is allowed but can increase distortion. A 25-million-pixel cap and conservative 1.5 GB memory estimate reject oversized jobs. The median uses upstream 512-pixel chunks for observation stacks, while full-size output arrays remain in memory. Up-sampling a 60 m band to 10 m does not add information. Minimum coverage is geometric footprint coverage, not valid-water coverage.

References [1]–[7] are in [REFERENCES.md](REFERENCES.md).


## Download and processing behavior

Selected bands are read as remote COG windows, normalized and clipped to the ROI, then exported as Float32 GeoTIFFs. Single-scene processing and chunked temporal median use the same sensor-specific calibration rules. Reflectance uses bilinear resampling; categorical QA uses nearest neighbour. The stable reflectance.tif alias is automatically added to QGIS when enabled.

Remote reads use TIFF-only probes, an in-memory read cache, a 30-second request timeout, four retries and a two-second retry delay. Public Landsat tokens are cached briefly and refreshed before expiry. Persistent source metadata does not include temporary access tokens. Progress reports the active band or processing chunk, rather than network-byte percentage. Actual duration depends on ROI, selected bands, service availability and network conditions.

Licensing, third-party source acknowledgments and technical provenance records are included separately.
