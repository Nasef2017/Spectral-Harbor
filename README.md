# Spectral Harbor 1.3.3

Standalone QGIS 4 plugin for selected Sentinel-2A and Landsat 8/9 surface-reflectance bands, with independent image processing and temporal compositing.

Open **Processing → Toolbox → Spectral Harbor → Satellite imagery → Download surface reflectance**. Choose the satellite, polygon ROI OR extent, dates, bands and output directory. Sentinel-2A defaults to 10 m; Landsat defaults to 30 m. The result can be added to the map automatically. The Raster menu/toolbar opens a separate browser for previewing and selecting scenes.

Start with the [offline Help page](docs/HELP.html). Also included: [full guide](docs/USER_GUIDE.md), [dependencies and architecture](docs/ARCHITECTURE.md), [validation](docs/VALIDATION.md), [citations](docs/REFERENCES.md), [attribution](docs/ATTRIBUTION.md), [changelog](CHANGELOG.md), [license](LICENSE) and [notices](NOTICE).

Requires the QGIS distribution's Python >=3.12, GDAL >=3.8, NumPy >=1.26, rasterio >=1.3 and shapely >=2.0. No other QGIS plugin or cloud account is required. Internet access and the public data services are required. Experimental local release; restart QGIS after an installed update.
