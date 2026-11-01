# Spectral Harbor 1.4.1 user guide

Open [offline Help](HELP.html) for the same Processing help, a full parameter reference and About.

## Processing Toolbox (recommended)

Open **Processing → Toolbox → Spectral Harbor → Satellite imagery → Download surface reflectance**. Choose the **Imagery provider** (`Element 84 Earth Search` or `Microsoft Planetary Computer`) and the **Satellite** (`Sentinel-2A`, `Landsat-8`, `Landsat-9`, or `Landsat-8 & 9`); the band checklist immediately changes between 12 Sentinel-2A and 7 Landsat bands. Select a polygon layer (check **Selected features only** to use just its highlighted polygons; leave it unchecked to unite all layer polygons) OR an extent using the normal extent selector. Set the dates, cloud/coverage, bands, pixel size, composition mode, optional water settings and output directory, then click **Run**. **Add reflectance to QGIS map after completion** is enabled by default.

Switching **Imagery provider** changes only scene discovery and remote asset signing; all downstream reflectance scaling, cloud/QA masking, water-only masking, UTM grid reprojection, temporal median compositing and Float32 GeoTIFF export use the exact same shared processing pipeline.

## Interactive scene-browser workflow

1. Choose **Element 84 Earth Search** or **Microsoft Planetary Computer** as the **Imagery provider**, and choose **Sentinel-2A**, **Landsat-8**, **Landsat-9** or **Landsat-8 & 9**.
2. Choose an ROI source and click **Capture ROI** (`Canvas extent`, `Active polygon layer`, `Selected polygons`, `Draw rectangle`, `Draw polygon`, or `WGS84 bounds`).
3. Set inclusive UTC start/end dates, maximum **scene-wide** cloud percentage, minimum footprint coverage of ROI, candidate scene cap and output pixel size.
4. Click **Search scenes**. Search and footprint checks run in the child process. Results are sorted by scene cloud then ID among returned candidates.
5. Select one result for **Single scene**, or use Ctrl/Shift to select scenes for **Temporal median**. Select at least one band.
6. Optionally enable **Keep water pixels only** and choose a method. Choose an existing output folder, then click **Download selected**.

## Reflectance bands & provider asset mapping

| Sentinel-2A | Earth Search Asset | Planetary Computer Asset | Native metres |
|---|---|---|---:|
| B01 coastal aerosol | `coastal` | `B01` | 60 |
| B02 blue | `blue` | `B02` | 10 |
| B03 green | `green` | `B03` | 10 |
| B04 red | `red` | `B04` | 10 |
| B05/B06/B07 red edge | `rededge1`/`rededge2`/`rededge3` | `B05`/`B06`/`B07` | 20 |
| B08 broad NIR | `nir` | `B08` | 10 |
| B8A narrow NIR | `nir08` | `B8A` | 20 |
| B09 water vapour | `nir09` | `B09` | 60 |
| B11/B12 SWIR | `swir16`/`swir22` | `B11`/`B12` | 20 |
| Scene Classification | `scl` | `SCL` | 20 |

| Landsat 8/9 SR | Earth Search & Planetary Computer Asset | Native metres |
|---|---|---:|
| SR_B1 coastal aerosol | `coastal` (`SR_B1`) | 30 |
| SR_B2 blue | `blue` (`SR_B2`) | 30 |
| SR_B3 green | `green` (`SR_B3`) | 30 |
| SR_B4 red | `red` (`SR_B4`) | 30 |
| SR_B5 NIR | `nir08` (`SR_B5`) | 30 |
| SR_B6/SR_B7 SWIR | `swir16`/`swir22` (`SR_B6`/`SR_B7`) | 30 |
| Quality / Saturation QA | `qa_pixel` (`QA_PIXEL`), `qa_radsat` (`QA_RADSAT`) | 30 |

## Scaling, quality, grid and outputs

Images are converted to physical Float32 surface reflectance (`-9999.0` nodata) using sensor-specific normalization (`0.0001 * DN - 0.1` for Sentinel-2A PB >= 04.00; `0.0000275 * DN - 0.2` for Landsat Collection 2 Level-2). When water-only masking is enabled with a custom band subset, required water-index bands (`B03`/`B08`/`B11` or `SR_B3`/`SR_B5`/`SR_B6`) are loaded on demand for mask computation while only the user-selected bands are written to the output reflectance GeoTIFF.

Each run writes the descriptive Float32 GeoTIFF, `*_WaterMask.tif`, `*_Processing_Metadata.json` (including separated `provider_specific_timings_sec` and `shared_processing_timings_sec`), `provenance.json`, and `primary_raster.txt` (plus `*_ValidCount.tif`, `*_TemporalStd.tif`, and `*_WaterOccurrence.tif` for median composites).
