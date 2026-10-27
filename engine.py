"""GDAL-only processing backend, loaded exclusively by a child Python process.

No QGIS, rasterio, pyproj or shapely imports. Uses the QGIS distribution's GDAL.
"""
import json
import math
import warnings
import time
from pathlib import Path
import numpy as np
from osgeo import gdal, ogr, osr
from .catalog import bands, calibration, asset_url, validate, PLATFORMS
from .vendor.water_mask import create_water_mask

gdal.UseExceptions()
ogr.UseExceptions()
osr.UseExceptions()

REMOTE_OPTIONS={
    'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR',
    'CPL_VSIL_CURL_ALLOWED_EXTENSIONS':'.tif,.TIF',
    'VSI_CACHE':'TRUE', 'VSI_CACHE_SIZE':'8388608',
    'GDAL_HTTP_TIMEOUT':'30', 'GDAL_HTTP_CONNECTTIMEOUT':'15',
    'GDAL_HTTP_MAX_RETRY':'4', 'GDAL_HTTP_RETRY_DELAY':'2',
}


def configure_remote_reads():
    """Called only inside the isolated worker; never changes QGIS's GDAL config."""
    for key,value in REMOTE_OPTIONS.items(): gdal.SetConfigOption(key,value)


def spatial_ref(epsg):
    crs = osr.SpatialReference(); crs.ImportFromEPSG(epsg)
    crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return crs


def build_grid(aoi, resolution):
    geom = ogr.CreateGeometryFromJson(json.dumps(aoi))
    if geom is None or geom.IsEmpty() or not geom.IsValid() or geom.GetGeometryName() not in ('POLYGON','MULTIPOLYGON'):
        raise ValueError('ROI must be a valid nonempty Polygon or MultiPolygon')
    xmin,xmax,ymin,ymax = geom.GetEnvelope()
    if not (-180 <= xmin < xmax <= 180 and -80 <= ymin < ymax <= 84) or xmax-xmin > 6:
        raise ValueError('Use a local ROI within one UTM zone; split dateline, wide or polar ROIs')
    lon,lat=(xmin+xmax)/2,(ymin+ymax)/2
    epsg=(32600 if lat >= 0 else 32700)+min(60,max(1,int((lon+180)//6)+1))
    crs=spatial_ref(epsg)
    projected=geom.Clone(); projected.AssignSpatialReference(spatial_ref(4326))
    projected.Segmentize(0.01)
    projected.TransformTo(crs)
    x0,x1,y0,y1=projected.GetEnvelope(); r=float(resolution)
    x0,y0=math.floor(x0/r)*r,math.floor(y0/r)*r
    x1,y1=math.ceil(x1/r)*r,math.ceil(y1/r)*r
    width,height=round((x1-x0)/r),round((y1-y0)/r)
    if width*height > 25_000_000:
        raise ValueError('ROI exceeds 25 million output pixels; reduce ROI or increase pixel size')
    grid={'bounds':(x0,y0,x1,y1),'width':width,'height':height,'crs':crs.ExportToWkt(),
          'transform':(x0,r,0,y1,0,-r),'epsg':epsg}
    vectors=ogr.GetDriverByName('Memory').CreateDataSource('')
    layer=vectors.CreateLayer('roi',crs,ogr.wkbMultiPolygon)
    feature=ogr.Feature(layer.GetLayerDefn()); feature.SetGeometry(projected); layer.CreateFeature(feature)
    mask=gdal.GetDriverByName('MEM').Create('',width,height,1,gdal.GDT_Byte)
    mask.SetGeoTransform(grid['transform']); mask.SetProjection(grid['crs'])
    gdal.RasterizeLayer(mask,[1],layer,burn_values=[1])
    grid['mask']=mask.ReadAsArray().astype(bool)
    return grid


def warp(asset,grid,reflectance=True, local=False,progress=None):
    href=asset['href'] if local else asset_url(asset)
    if not local:
        href='/vsicurl/'+href
    nodata=calibration(asset)[2] if reflectance else None
    # Match Bathymetrix: bilinear reflectance, nearest-neighbour categorical QA.
    options=dict(format='MEM',dstSRS=grid['crs'],outputBounds=grid['bounds'],
                 width=grid['width'],height=grid['height'],resampleAlg='bilinear' if reflectance else 'near',
                 dstNodata=float('nan'),outputType=gdal.GDT_Float32,multithread=False)
    if progress is not None:
        options['callback']=lambda done,message,data: (progress(float(done)*100) or 1)
    if nodata is not None:
        options['srcNodata']=nodata
    ds=gdal.Warp('',href,**options)
    if ds is None:
        raise RuntimeError('Unable to read required asset')
    array=ds.ReadAsArray().astype('float32'); ds=None
    if reflectance:
        scale,offset,_=calibration(asset)
        array=array*np.float32(scale)+np.float32(offset)
    array[~grid['mask']]=np.nan
    return array


def scene_arrays(item,config,grid,log=print,local=False,progress=None):
    mapping={b[0]:b[1] for b in bands(config['platform'])}
    selected=[mapping[b] for b in config['bands']]
    required=set(selected)
    s2=config['platform']=='Sentinel-2A'
    nir='nir' if s2 else 'nir08'
    if config.get('water_only'):
        method=config.get('water_method','hybrid')
        if method in ('ndwi','hybrid'): required.update(['green',nir])
        if method=='mndwi': required.update(['green','swir16'])
    assets=item['assets']
    for key in required | ({'scl'} if s2 else {'qa_pixel','qa_radsat'}):
        if key not in assets:
            raise ValueError(f'{item["id"]} missing required asset {key}')
    # Follow the reference reader order: classification first, then spectral bands.
    keys=(['scl'] if s2 else ['qa_pixel','qa_radsat'])+[b[1] for b in bands(config['platform']) if b[1] in required]
    read={}
    for index,key in enumerate(keys):
        start=time.perf_counter()
        log(f'[{index+1}/{len(keys)}] Reading {key} for {item["id"]}...')
        def advance(percent):
            if progress is not None: progress(100*(index+percent/100)/len(keys))
        read[key]=warp(assets[key],grid,key in required,local,advance)
        log(f'Finished {key} in {time.perf_counter()-start:.1f} s')
        advance(100)
    arrays={key:read[key] for key in required}
    if s2:
        aux=read['scl']
        valid=np.isfinite(aux) & ~np.isin(aux,[0,1,3,8,9,10,11])
        kwargs={'scl':np.nan_to_num(aux,nan=0).astype('uint8')}
    else:
        qa=read['qa_pixel']; sat=read['qa_radsat']
        q=np.nan_to_num(qa,nan=1).astype('uint16')
        s=np.nan_to_num(sat,nan=65535).astype('uint16')
        valid=np.isfinite(qa)&np.isfinite(sat)&((q & 63)==0)&((s & (127|256|2048))==0)
        kwargs={'qa_pixel':q,'qa_radsat':s}
    valid &= grid['mask']
    for a in arrays.values(): valid &= np.isfinite(a)
    base_valid=valid.copy()
    water=np.zeros(valid.shape,bool)
    if config.get('water_only'):
        water,_,_=create_water_mask(**kwargs,green_refl=arrays.get('green'),nir_refl=arrays.get(nir),
                 swir1_refl=arrays.get('swir16'),valid_mask=valid,method=config.get('water_method','hybrid'),
                 mode=config.get('water_mode','permissive'),threshold=config.get('threshold'),use_otsu=True)
        valid &= water
    stack=np.stack([arrays[k] for k in selected]); stack[:,~valid]=np.nan
    log('Prepared '+item['id'])
    return stack,water,base_valid


def write_raster(path,array,grid,names,dtype=gdal.GDT_Float32,nodata=float('nan')):
    if array.ndim==2: array=array[None,...]
    ds=gdal.GetDriverByName('GTiff').Create(str(path),grid['width'],grid['height'],len(array),dtype,
                       ['TILED=YES','COMPRESS=DEFLATE','BIGTIFF=IF_SAFER'])
    ds.SetGeoTransform(grid['transform']); ds.SetProjection(grid['crs'])
    for i,a in enumerate(array,1):
        band=ds.GetRasterBand(i); band.WriteArray(a); band.SetNoDataValue(nodata)
        band.SetDescription(names[i-1])
    ds.FlushCache(); ds=None


def process(items,config,outdir,log=print,local=False,progress=None):
    validate(config)
    if not items: raise ValueError('No scenes selected')
    if config.get('mode','single')=='single' and len(items)!=1:
        raise ValueError('Single-scene mode requires exactly one scene')
    grid=build_grid(config['aoi'],config['resolution'])
    log(f'Target grid: {grid["width"]} x {grid["height"]}; EPSG:{grid["epsg"]}; pixel size {config["resolution"]} m')
    # Hard memory guard covers input stack, QA arrays and median temporary copies.
    estimate=grid['width']*grid['height']*(len(config['bands'])*len(items)*12+128)
    if estimate > 1_500_000_000:
        raise ValueError('Estimated working memory exceeds 1.5 GB; reduce scenes, bands or ROI')
    if any(i['properties'].get('platform') not in PLATFORMS[config['platform']] for i in items):
        raise ValueError('Selected scene platform does not match satellite')
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    stacks=[]; waters=[]; valids=[]
    for index,item in enumerate(items):
        def advance(percent):
            if progress is not None: progress(5+80*(index+percent/100)/len(items))
        stack,water,valid=scene_arrays(item,config,grid,log,local,advance)
        stacks.append(stack); waters.append(water); valids.append(valid)
    log('Preparing Float32 reflectance and quality outputs...')
    if len(stacks)==1:
        result=stacks[0]
        count=np.isfinite(result[0]).astype('uint16')
        std=np.where(np.isfinite(result),np.float32(0),np.float32(np.nan))
    else:
        cube=np.stack(stacks)
        count=np.sum(np.isfinite(cube[:,0]),axis=0).astype('uint16')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',RuntimeWarning)
            result=np.nanmedian(cube,axis=0).astype('float32')
            std=np.nanstd(cube,axis=0).astype('float32')
    if progress is not None: progress(90)
    mode_str = "WaterOnly" if config.get('water_only') else "BOA_Float32"
    if len(stacks) == 1:
        clean_id = items[0]['id'].replace('/', '_')
        plat = items[0].get('properties', {}).get('platform', 'satellite').upper()
        clean_base = clean_id if clean_id.upper().startswith((plat, 'S2A', 'S2B', 'LC08', 'LC09')) else f"{plat}_{clean_id}"
        refl_name = f"{clean_base}_{mode_str}.tif"
    else:
        plat_str = config['platform'].replace(' ', '_').replace('&', 'and')
        s_date = config['start'].replace('-', '')
        e_date = config['end'].replace('-', '')
        refl_name = f"{plat_str}_{s_date}_{e_date}_Median_Composite_{mode_str}.tif"
    primary_raster = outdir / refl_name
    write_raster(primary_raster,result,grid,config['bands'])
    count[~grid['mask']]=65535
    write_raster(outdir/'valid_count.tif',count,grid,['valid_observation_count'],gdal.GDT_UInt16,65535)
    write_raster(outdir/'temporal_std.tif',std,grid,config['bands'])
    if config.get('water_only'):
        # Denominator is quality-valid observations before water filtering.
        denominator=np.sum(valids,axis=0)
        occurrence=np.full(count.shape,np.nan,dtype='float32')
        np.divide(np.sum(waters,axis=0),denominator,out=occurrence,where=denominator>0)
        occurrence[~grid['mask']]=np.nan
        write_raster(outdir/'water_occurrence.tif',occurrence,grid,['water_fraction'])
    # Keep signed URLs and credentials out of persistent provenance.
    clean=[]
    for item in items:
        clean.append({'id':item['id'],'collection':item.get('collection'),'properties':item['properties'],
                      'assets':{k:{'href':v['href'].split('?')[0],'raster:bands':v.get('raster:bands')}
                                for k,v in item['assets'].items() if k in {b[1] for b in bands(config['platform'])}}})
    provenance={'plugin':'Spectral Harbor 1.3.4','config':config,'scenes':clean,
                'epsg':grid['epsg'],'gdal':gdal.VersionInfo(),'resampling':{'reflectance':'bilinear','qa':'nearest'},
                'notes':'No cross-sensor harmonization; median of per-scene scaled, quality-masked observations.'}
    (outdir/'provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    (outdir/'primary_raster.txt').write_text(str(primary_raster),encoding='utf-8')
    if progress is not None: progress(100)
    return str(primary_raster)
