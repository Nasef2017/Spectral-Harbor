"""Run the attributed Bathymetrix processing code in its own rasterio-only worker.

The processor/normalization modules are unchanged upstream snapshots. The grid
uses rasterio's native geometry transform instead of the reported pyproj call.
"""
import json
import os
import shutil
import time
import sys
from pathlib import Path
from .catalog import bands,validate,asset_url,PLATFORMS
from .vendor.bathymetrix_stac import processor,stac_client,grid


class Feedback:
    def __init__(self,log,progress): self.log=log; self.progress=progress; self.percent=0
    def pushInfo(self,message): self.log(message)
    def pushWarning(self,message): self.log('WARNING: '+str(message))
    def reportError(self,message,fatalError=False): self.log('ERROR: '+str(message))
    def isCanceled(self): return False  # The controlling QGIS process kills this child.
    def setProgress(self,value):
        self.percent=max(self.percent,min(99,float(value)))
        if self.progress is not None: self.progress(self.percent)


def scene_metadata(item):
    props=item['properties']; platform=props['platform']; assets={}
    if platform not in {'sentinel-2a','landsat-8','landsat-9'}: raise ValueError('Unsupported scene platform')
    s2=platform=='sentinel-2a'
    band_map=stac_client.BAND_ASSET_MAP if s2 else stac_client.LANDSAT_BAND_ASSET_MAP
    aux_map=stac_client.AUX_ASSET_MAP if s2 else stac_client.LANDSAT_AUX_ASSET_MAP
    for code in list(band_map)+list(aux_map):
        asset=stac_client.resolve_asset_for_band(item['assets'],code,platform)
        if asset:
            rb=(asset.get('raster:bands') or [{}])[0]
            assets[code]={'href':asset['href'],'scale':rb.get('scale',.0001 if s2 else .0000275),
                          'offset':rb.get('offset',0 if s2 else -.2),'nodata':rb.get('nodata',0),
                          'data_type':rb.get('data_type','uint16'),
                          'spatial_resolution':rb.get('spatial_resolution',band_map.get(code,{}).get('res',30))}
    acquired=props['datetime']
    return dict(id=item['id'],platform=platform,collection=item.get('collection'),
                satellite='Sentinel-2A' if s2 else ('Landsat-8' if platform=='landsat-8' else 'Landsat-9'),
                datetime=acquired,date=acquired.split('T')[0],cloud_cover=props.get('eo:cloud_cover',0),
                processing_baseline=str(props.get('s2:processing_baseline','N/A')),
                boa_offset_applied=props.get('earthsearch:boa_offset_applied'),assets=assets)


def process(items,config,outdir,log=print,progress=None):
    validate(config)
    if not items or (config.get('mode')=='single' and len(items)!=1): raise ValueError('Invalid scene selection')
    scenes=[scene_metadata(item) for item in items]
    if any(s['platform'] not in PLATFORMS[config['platform']] for s in scenes): raise ValueError('Scene satellite mismatch')
    selected=config['bands']
    for scene in scenes:
        missing=set(selected)-scene['assets'].keys()
        if missing: raise ValueError('Selected bands absent from scene: '+','.join(sorted(missing)))
    # Preserve the public, credential-free transport improvement without changing
    # upstream scientific processing or persisting signed URLs into its metadata.
    stac_client.resolve_landsat_public_https_url=lambda href:asset_url({'href':href})
    preview=grid.build_target_grid(config['aoi'],pixel_size=config['resolution'])
    if preview['width']*preview['height']*(len(selected)*8+64)>1_500_000_000:
        raise ValueError('Estimated processing memory exceeds 1.5 GB; reduce bands/ROI or increase pixel size')
    del preview
    original_read=grid.read_raster_into_grid
    def read(*args,**kwargs):
        label=Path(str(args[0]).split('?')[0]).name
        log('Reading '+label+'...'); started=time.perf_counter()
        result=original_read(*args,**kwargs)
        log(f'Finished {label} in {time.perf_counter()-started:.1f} s')
        return result
    processor.read_raster_into_grid=read
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    feedback=Feedback(log,progress)
    common=dict(aoi_geojson=config['aoi'],output_dir=str(outdir),bands_to_process=selected,
                pixel_size=config['resolution'],water_only=config.get('water_only',False),feedback=feedback)
    if config.get('mode')=='median':
        outputs=processor.process_temporal_median_composite(
            scenes_meta=scenes,chunk_size=512,**common)
        primary=Path(outputs['composite_reflectance'])
    else:
        outputs=processor.process_single_scene(scene_meta=scenes[0],water_method=config.get('water_method','hybrid'),
            water_mode=config.get('water_mode','permissive'),water_threshold=config.get('threshold'),**common)
        primary=Path(outputs['surface_reflectance'])
    # Product-facing metadata identifies this distribution. Original code notices
    # and source attribution remain intact in the bundled legal files.
    with processor.rasterio.open(primary,'r+') as dataset:
        dataset.update_tags(PROCESSED_BY='Spectral Harbor')
    metadata_path=Path(outputs['metadata_json'])
    metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
    metadata['scaling_applied_by']='Spectral Harbor STAC reader (surface reflectance, already scaled)'
    metadata_path.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    # Stable output name for the existing UI/Processing contract, with no second
    # raster encoding. NTFS hard links avoid duplicate data where supported.
    alias=outdir/'reflectance.tif'
    try: os.link(primary,alias)
    except OSError: shutil.copyfile(primary,alias)
    provenance=dict(plugin='Spectral Harbor 1.3.3',engine='Spectral Harbor raster-processing pipeline',
                    config=config,scenes=[{'id':s['id'],'platform':s['platform']} for s in scenes],
                    reference_outputs={k:v for k,v in outputs.items() if isinstance(v,str)},
                    calibration='Sensor-specific normalization including baseline/value-range heuristics',
                    native_isolation='Rasterio worker; no QGIS, pyproj or osgeo imported',
                    rasterio_version=processor.rasterio.__version__,
                    linked_gdal_version=processor.rasterio.__gdal_version__,
                    loaded_conflicts=[name for name in ('qgis','pyproj','osgeo') if name in sys.modules],
                    grid_change='rasterio.warp.transform_geom replaces pyproj.Transformer')
    (outdir/'provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    if progress is not None: progress(100)
    return str(alias)
