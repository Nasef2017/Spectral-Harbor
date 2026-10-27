# Spectral Harbor validation — 1.3.3

Validated on Windows with QGIS 4.0.2-Norrköping, Qt 6.11.0 and Python 3.12.13. The existing 33-test suite covers parameter defaults, satellite/band filtering, raster processing, selected-band order, median/quality outputs, subprocess isolation, live feedback, cancellation and automatic map loading. Nested isolated tests exercise 20 processing regression cases and three adapter cases without mixing native packages into the QGIS test process.

## Live data evidence

Earlier all-band smoke checks passed for Sentinel-2A (12 bands), Landsat-8 (7) and Landsat-9 (7) using a tiny Red Sea ROI. A separate real Processing run confirmed the default Sentinel-2A output grid is 10 m and returns a valid QGIS raster. These checks establish access and processing, not classification accuracy, a full-size regional benchmark or a guaranteed download time.

## Current product-label update

About, metadata, short help, parameter explanations, general documentation and changelog describe Spectral Harbor directly. Generated PROCESSED_BY tags, scaling_applied_by fields and run provenance identify Spectral Harbor. Image values, normalization, nodata, scene selection, composite settings and map loading are unchanged. The adapter regression test checks the raster and JSON identifiers. Required third-party licenses/notices and technical source provenance are retained separately.

## Documentation and package checks

All 19 Processing parameters have help text. The offline Help URL, About version, browser mode controls, eight local HTML links and English-only current documentation are checked. The help panel was rendered in QGIS and inspected. The package builder verifies required files, archive CRC, one top-level plugin directory and file hashes.

The installed update checks the prior release, creates a complete backup, verifies all replacement files and requires a QGIS restart. See installed-update-report.json in the source deliverable. The installable ZIP contains only the standalone plugin, legal files and its documentation. No other installed plugin is changed.

## Limits

Headless UI tests and tiny live rasters do not exercise every canvas interaction, service outage, full-size ROI or scientific environment. Single-scene full-ROI output can retain cloudy pixels when water-only is off. Median QA and water behavior follow the documented processing policy. Output arrays remain in memory even though observations are processed in chunks. Resource guards bound grid size and estimated output memory.


Release 1.3.3 verification: all 33 main-suite tests passed in 4.907 seconds, including the isolated processing suites and the updated raster/JSON branding assertions. Public product text was scanned, all eight local Help links resolved, original scientific source hashes were verified, and required legal notices were preserved.
