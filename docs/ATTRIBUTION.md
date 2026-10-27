# Attribution and license audit — current distribution

Spectral Harbor is developed by Mohamed Aly Nasef with OpenAI Codex assistance. Its name, geometric SVG/PNG logo, interface, catalog integration, adapters, telemetry, packaging and documentation were created for this standalone project. The name has not undergone trademark clearance.

## Bathymetrix-AI components

The local Bathymetrix_AI STAC downloader was inspected at the user-specified QGIS profile path on 2026-10-27. Metadata declared version 8.0 and the root LICENSE was Apache-2.0. No root NOTICE file or accessible Git revision was available; no commit identity is invented. Project reference: https://github.com/Nasef2017/Bathymetrix-AI .

Production processing uses the copied `vendor/bathymetrix_stac/processor.py`, `reflectance.py`, `water_mask.py` and `stac_client.py`, unchanged byte for byte. The copied `grid.py` replaces the reported pyproj geometry-transform call with rasterio.warp.transform_geom, clamps UTM edge zones and adds a grid-size guard. Original and bundled SHA-256 values are in [upstream-source-manifest.json](upstream-source-manifest.json); the full grid diff is [upstream-grid.patch](upstream-grid.patch). The adapter also guards estimated memory, forwards selected bands and feedback, and uses the tested public Landsat URL resolver. It does not edit the scientific processor or normalization source files.

The original 20 upstream tests are included in the source project with only package import paths adapted; their upstream attribution is retained. A second unchanged copy of the upstream water module remains at `vendor/water_mask.py` for historical custom-engine regressions (SHA-256 d277982d21bf82e45040eade981c6b138ec667fd7083972737950a33083f152c). Production processing uses the copy inside bathymetrix_stac. No upstream images, logos, scientific manuscripts, bathymetry models, private datasets or research results are bundled.

## Distribution license

The combined plugin is GPL-3.0-or-later; see [LICENSE](../LICENSE) and [NOTICE](../NOTICE). All copied upstream components retain Apache-2.0 terms and attribution, with the complete original text at [vendor/LICENSE-Apache-2.0.txt](../vendor/LICENSE-Apache-2.0.txt). Apache-2.0 inclusion in GPLv3 works is described by the [Apache Software Foundation](https://www.apache.org/licenses/GPL-compatibility). This does not relicense the original Bathymetrix project or imply GPLv2-only compatibility. QGIS, NumPy, GDAL, rasterio and shapely are external runtime dependencies supplied by the QGIS distribution; their binaries are not bundled.

## Data and independence

Satellite data and access services retain separate terms; follow [REFERENCES.md](REFERENCES.md), actual acquisition years and product notices. No endorsement by ESA, Copernicus, NASA, USGS, Element 84, Microsoft or QGIS is implied. Bathymetrix_AI does not need to be installed and is not imported at runtime. Current updates target only the installed spectral_harbor plugin. The separately retained bathymetrix_v8_update bundle records the earlier four-file documentation/metadata update; it is not part of this installable plugin.
