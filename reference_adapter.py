"""Run the attributed Bathymetrix processing code in its own rasterio-only worker.

The processor/normalization modules are shared across Element 84 Earth Search and
Microsoft Planetary Computer. The grid uses rasterio's native geometry transform
instead of the reported pyproj call.
"""
import json
import time
import sys
from pathlib import Path
from .catalog import validate, asset_url, normalize_feature, normalize_provider, PLATFORMS
from .vendor.bathymetrix_stac import processor, stac_client, grid


class Feedback:
    def __init__(self, log, progress):
        self.log = log
        self.progress = progress
        self.percent = 0

    def pushInfo(self, message):
        self.log(message)

    def pushWarning(self, message):
        self.log('WARNING: ' + str(message))

    def reportError(self, message, fatalError=False):
        self.log('ERROR: ' + str(message))

    def isCanceled(self):
        return False  # The controlling QGIS process kills this child.

    def setProgress(self, value):
        self.percent = max(self.percent, min(99, float(value)))
        if self.progress is not None:
            self.progress(self.percent)


def scene_metadata(item, provider=None):
    norm_item = normalize_feature(item, provider=provider)
    props = norm_item['properties']
    platform = str(props.get('platform', '')).lower()
    norm_provider = normalize_provider(provider or props.get('spectralharbor:provider'))
    assets = {}
    if platform not in {'sentinel-2a', 'landsat-8', 'landsat-9'}:
        raise ValueError('Unsupported scene platform')
    s2 = platform == 'sentinel-2a'
    band_map = stac_client.BAND_ASSET_MAP if s2 else stac_client.LANDSAT_BAND_ASSET_MAP
    aux_map = stac_client.AUX_ASSET_MAP if s2 else stac_client.LANDSAT_AUX_ASSET_MAP
    for code in list(band_map) + list(aux_map):
        asset = stac_client.resolve_asset_for_band(
            norm_item['assets'], code, platform=platform, provider=norm_provider
        )
        if asset:
            rb = (asset.get('raster:bands') or [{}])[0]
            assets[code] = {
                'href': asset['href'],
                'scale': rb.get('scale', 0.0001 if s2 else 0.0000275),
                'offset': rb.get('offset', (0.0 if props.get('earthsearch:boa_offset_applied') is True else None) if s2 else -0.2),
                'nodata': rb.get('nodata', 0),
                'data_type': rb.get('data_type', 'uint16'),
                'spatial_resolution': rb.get('spatial_resolution', band_map.get(code, {}).get('res', 30)),
            }
    acquired = props['datetime']
    return dict(
        id=norm_item['id'],
        provider=norm_provider,
        platform=platform,
        collection=norm_item.get('collection'),
        satellite='Sentinel-2A' if s2 else ('Landsat-8' if platform == 'landsat-8' else 'Landsat-9'),
        datetime=acquired,
        date=acquired.split('T')[0],
        cloud_cover=props.get('eo:cloud_cover', 0),
        processing_baseline=str(props.get('s2:processing_baseline', 'N/A')),
        boa_offset_applied=props.get('earthsearch:boa_offset_applied'),
        assets=assets,
    )


def process(items, config, outdir, log=print, progress=None, provider_timings=None):
    validate(config)
    if not items or (config.get('mode') == 'single' and len(items) != 1):
        raise ValueError('Invalid scene selection')
    provider_key = normalize_provider(config.get('provider'))
    prov_timings = dict(provider_timings or {})
    t_disc0 = time.perf_counter()
    scenes = [scene_metadata(item, provider=provider_key) for item in items]
    prov_timings['asset_discovery_sec'] = prov_timings.get('asset_discovery_sec', 0.0) + (time.perf_counter() - t_disc0)

    if any(s['platform'] not in PLATFORMS[config['platform']] for s in scenes):
        raise ValueError('Scene satellite mismatch')
    selected = config['bands']
    for scene in scenes:
        missing = set(selected) - scene['assets'].keys()
        if missing:
            raise ValueError('Selected bands absent from scene: ' + ','.join(sorted(missing)))

    # Preserve public, credential-free transport and SAS signing without changing
    # downstream scientific processing or persisting signed URLs into metadata.
    stac_client.resolve_landsat_public_https_url = lambda href: asset_url(
        {'href': href}, provider=provider_key, collection='landsat-c2-l2', timing_out=prov_timings
    )
    stac_client.resolve_downloadable_asset_url = lambda href, provider=provider_key, collection=None: asset_url(
        {'href': href}, provider=provider, collection=collection, timing_out=prov_timings
    )

    preview = grid.build_target_grid(config['aoi'], pixel_size=config['resolution'])
    if preview['width'] * preview['height'] * (len(selected) * 8 + 64) > 1_500_000_000:
        raise ValueError('Estimated processing memory exceeds 1.5 GB; reduce bands/ROI or increase pixel size')
    del preview

    original_read = grid.read_raster_into_grid

    def read(*args, **kwargs):
        label = Path(str(args[0]).split('?')[0]).name
        log('Reading ' + label + '...')
        started = time.perf_counter()
        result = original_read(*args, **kwargs)
        log(f'Finished {label} in {time.perf_counter() - started:.1f} s')
        return result

    processor.read_raster_into_grid = read
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    feedback = Feedback(log, progress)
    common = dict(
        aoi_geojson=config['aoi'],
        output_dir=str(outdir),
        bands_to_process=selected,
        pixel_size=config['resolution'],
        water_only=config.get('water_only', False),
        feedback=feedback,
        provider_timings=prov_timings,
    )
    if config.get('mode') == 'median':
        outputs = processor.process_temporal_median_composite(
            scenes_meta=scenes, chunk_size=512, **common
        )
        primary = Path(outputs['composite_reflectance'])
    else:
        outputs = processor.process_single_scene(
            scene_meta=scenes[0],
            water_method=config.get('water_method', 'hybrid'),
            water_mode=config.get('water_mode', 'permissive'),
            water_threshold=config.get('threshold'),
            **common,
        )
        primary = Path(outputs['surface_reflectance'])

    # Product-facing metadata identifies this distribution. Original code notices
    # and source attribution remain intact in the bundled legal files.
    with processor.rasterio.open(primary, 'r+') as dataset:
        dataset.update_tags(PROCESSED_BY='Spectral Harbor', PROVIDER=provider_key)
    metadata_path = Path(outputs['metadata_json'])
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    metadata['scaling_applied_by'] = 'Spectral Harbor STAC reader (surface reflectance, already scaled)'
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')

    provenance = dict(
        plugin='Spectral Harbor 1.4.1',
        engine='Spectral Harbor raster-processing pipeline',
        provider=provider_key,
        config=config,
        scenes=[{'id': s['id'], 'platform': s['platform'], 'provider': s['provider']} for s in scenes],
        primary_raster=str(primary),
        primary_filename=primary.name,
        reference_outputs={k: v for k, v in outputs.items() if isinstance(v, str)},
        performance_diagnostics=outputs.get('performance_diagnostics', {}),
        calibration='Sensor-specific normalization including baseline/value-range heuristics',
        native_isolation='Rasterio worker; no QGIS, pyproj or osgeo imported',
        rasterio_version=processor.rasterio.__version__,
        linked_gdal_version=processor.rasterio.__gdal_version__,
        loaded_conflicts=[name for name in ('qgis', 'pyproj', 'osgeo') if name in sys.modules],
        grid_change='rasterio.warp.transform_geom replaces pyproj.Transformer',
    )
    (outdir / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    (outdir / 'primary_raster.txt').write_text(str(primary), encoding='utf-8')
    if progress is not None:
        progress(100)
    return str(primary)
