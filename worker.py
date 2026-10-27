"""Private subprocess entry point. JSON files are the only UI/engine contract."""
import json
import sys
import traceback
from pathlib import Path

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    __package__='spectral_harbor'


def main():
    from .catalog import search
    from .events import event_line
    request=Path(sys.argv[1]); job=json.loads(request.read_text(encoding='utf-8'))
    config=job['config']
    def log(message): print(event_line(message),flush=True)
    last_progress=[-1]
    def progress(percent):
        value=int(percent)
        if value!=last_progress[0]:
            print(event_line(progress=value),flush=True); last_progress[0]=value
    if job['action']=='diagnostics':
        from .engine import configure_remote_reads,REMOTE_OPTIONS
        configure_remote_reads()
        from osgeo import gdal
        import numpy as np
        Path(job['result']).write_text(json.dumps({'gdal':gdal.VersionInfo(),'numpy':np.__version__,
                 'python':sys.version,'remote_options':{k:gdal.GetConfigOption(k) for k in REMOTE_OPTIONS},'loaded_conflicts':[x for x in ('qgis','pyproj','rasterio') if x in sys.modules]}))
    elif job['action']=='search':
        from .engine import build_grid,spatial_ref,configure_remote_reads
        from osgeo import ogr
        configure_remote_reads()
        grid=build_grid(config['aoi'],config['resolution'])
        roi=ogr.CreateGeometryFromJson(json.dumps(config['aoi']))
        roi.AssignSpatialReference(spatial_ref(4326)); roi.TransformTo(spatial_ref(grid['epsg']))
        items=[]
        for item in search(config,log):
            footprint=ogr.CreateGeometryFromJson(json.dumps(item['geometry']))
            footprint.AssignSpatialReference(spatial_ref(4326)); footprint.TransformTo(spatial_ref(grid['epsg']))
            coverage=100*roi.Intersection(footprint).GetArea()/roi.GetArea()
            if coverage+1e-6 >= config.get('coverage',0):
                item['properties']['spectralharbor:roi_coverage']=coverage; items.append(item)
        Path(job['result']).write_text(json.dumps(items),encoding='utf-8')
        log(f'{len(items)} scenes pass ROI coverage filter.')
    elif job['action']=='process':
        from .reference_adapter import process
        path=process(job['items'],config,job['outdir'],log,progress=progress)
        log('Saved '+path)
    else:
        raise ValueError('Unknown worker action')


if __name__=='__main__':
    try: main()
    except Exception as exc:
        # GDAL errors may contain signed URLs. Never persist traceback URLs/tokens.
        import re
        message=re.sub(r'https?://[^\s\"\']+', '[remote asset]', str(exc))
        print('ERROR: '+message,file=sys.stderr,flush=True)
        sys.exit(1)
