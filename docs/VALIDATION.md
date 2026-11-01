# Spectral Harbor validation — 1.4.1

Validated on Windows with QGIS 4.0.2-Norrköping, Qt 6.11.0 and Python 3.12.

## Multi-Provider Equivalence & Performance Benchmark

`tests/test_spectral_harbor_providers.py` (`9/9` tests passed) and `tests/benchmark_providers.py` verify that **Element 84 Earth Search** (`earth_search`) and **Microsoft Planetary Computer** (`planetary_computer`) produce identical outputs when given matching source scenes:

| Scenario | Provider Total (s) (ES / PC) | Shared Total (s) (ES / PC) | Schema & Band Order Identical | Valid Mask Agreement | Max Abs Diff | RMSE |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Sentinel-2A Single Scene** (12 bands, 10 m) | `0.0633` / `0.0476` | `0.0448` / `0.0440` | `True` | `100.0%` | `0.0` | `0.0` |
| **Sentinel-2A Median Composite** (2 scenes, 12 bands) | `0.0926` / `0.0890` | `0.3606` / `0.3328` | `True` | `100.0%` | `0.0` | `0.0` |
| **Landsat-8 Single Scene** (7 bands, 30 m) | `0.0308` / `0.0305` | `0.0326` / `0.0322` | `True` | `100.0%` | `0.0` | `0.0` |
| **Landsat-9 Single Scene** (7 bands, 30 m) | `0.0300` / `0.0304` | `0.0321` / `0.0332` | `True` | `100.0%` | `0.0` | `0.0` |
| **Landsat-8/9 Combined Median Composite** | `0.0635` / `0.0620` | `0.1544` / `0.0658` | `True` | `100.0%` | `0.0` | `0.0` |

## Regression Suite Coverage

1. Provider key normalization, STAC endpoints, and collection IDs (`sentinel-2-c1-l2a` vs `sentinel-2-l2a`, `landsat-c2-l2`).
2. STAC asset mapping (`coastal`..`swir22`/`scl` vs `B01`..`B12`/`B8A`/`SCL`) and explicit `raster:bands` calibration synthesis.
3. Planetary Computer Azure Blob SAS URL signing, token caching, double-signing prevention, and token redaction in persisted JSON metadata.
4. Single Scene and Temporal Median Composite numerical equivalence across Sentinel-2A, Landsat-8, Landsat-9, and Landsat-8/9 Combined.
5. Custom band subset selection with canonical band ordering and on-demand water-index band loading.
6. Separated `provider_specific_timings_sec` and `shared_processing_timings_sec` performance diagnostics.
7. Stationary high-albedo coastal target preservation (`water_only=False`) in `512x512` chunked temporal median composites across both providers.
