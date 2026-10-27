# Changelog

## 1.3.3 — 2026-10-27

- Focus About, help and public documentation on Spectral Harbor and its behavior.
- Use Spectral Harbor identifiers in generated raster tags and processing metadata, without changing raster values.
- Retain third-party license notices and source provenance in the legal/source records.

## 1.3.2 — 2026-10-27

- Make documentation and offline help English-only; remove the translated quick-start page and links.

## 1.3.1 — 2026-10-27

- Add structured Processing help, explanations for all parameters, local Help and User guide/About buttons.
- Clarify satellite pixel defaults, scene selection, ROI options, QA behavior and output differences.
- Visually disable scene-browser controls which do not apply to the selected processing mode.

## 1.3.0 — 2026-10-27

- Use the complete sensor-specific single-scene and chunked-median raster pipeline.
- Default Sentinel-2A to 10 m and Landsat to 30 m; preserve manual pixel-size overrides.
- Preserve selected bands, Float32/-9999 outputs, original normalization rules, native product names and metadata.
- Isolate rasterio/shapely processing from QGIS and GDAL search tasks; adapt the grid transformation and guard resource use.
- Keep reflectance.tif as a stable alias for automatic map loading.

## 1.2.0 — 2026-10-27

- Improve remote COG caching, timeouts/retries and Landsat token reuse.
- Add live band messages and stage progress; use bilinear reflectance and nearest-neighbour QA.

## 1.1.1 — 2026-10-27

- Automatically load the completed reflectance raster into QGIS after Processing succeeds.

## 1.1.0 — 2026-10-27

- Add the Processing provider, dynamic 12/7 band checklist, directory output, Batch and Modeler integration.

## 1.0.0 — 2026-10-27

- Initial standalone surface-reflectance downloader with satellite selection, ROI/date/cloud filters, single scenes, median and water options, original logo and documentation.
