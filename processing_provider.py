"""Standard QGIS Processing integration with isolated search/download workers."""
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
from datetime import date,timedelta,datetime
from pathlib import Path
from uuid import uuid4
from qgis.core import (QgsProcessingProvider,QgsProcessingAlgorithm,QgsProcessing,
    QgsProcessingParameterEnum,QgsProcessingParameterString,QgsProcessingParameterFeatureSource,
    QgsProcessingParameterExtent,QgsProcessingParameterNumber,QgsProcessingParameterBoolean,
    QgsProcessingParameterFolderDestination,QgsProcessingOutputRasterLayer,
    QgsProcessingException,QgsCoordinateReferenceSystem,QgsCoordinateTransform,QgsGeometry,
    QgsProcessingContext,QgsProcessingUtils)
from qgis.PyQt.QtGui import QIcon
from .catalog import PLATFORMS,bands,validate,default_resolution
from .events import parse_line
from .help_text import SHORT_HELP,PARAMETER_HELP


def run_worker(job,scratch,feedback):
    candidates=[Path(sys.prefix)/'python.exe',Path(sys.prefix)/'bin/python.exe'] if sys.platform=='win32' else [Path(sys.prefix)/'bin/python3']
    executable=next((p for p in candidates if p.is_file()),None)
    if executable is None: raise QgsProcessingException('Cannot locate the QGIS Python interpreter')
    request=Path(scratch)/'job.json'; request.write_text(json.dumps(job),encoding='utf-8')
    env=dict(os.environ,PYTHONNOUSERSITE='1',PYTHONUNBUFFERED='1',CPL_LOG=str(Path(scratch)/'gdal-private.log'))
    # A private growing log avoids blocking readline() on Windows while still
    # forwarding each complete event as it arrives. Child never touches GUI objects.
    writer=open(Path(scratch)/'worker-stream.log','wb')
    reader=open(Path(scratch)/'worker-stream.log','r',encoding='utf-8',errors='replace')
    pending=''
    start,end=(40,99) if job.get('action')=='process' else (0,35)
    def drain(final=False):
        nonlocal pending
        pending+=reader.read()
        lines=pending.split('\n'); pending=lines.pop()
        if final and pending: lines.append(pending); pending=''
        for line in lines:
            message,percent=parse_line(line.strip())
            if message: feedback.pushInfo(message)
            if percent is not None: feedback.setProgress(start+(end-start)*percent/100)
    child=None
    try:
        child=subprocess.Popen([str(executable),str(Path(__file__).with_name('worker.py')),str(request)],  # nosec B603
             stdout=writer,stderr=subprocess.STDOUT,env=env,
             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        while True:
            drain()
            if feedback.isCanceled():
                child.kill(); child.wait()
                raise QgsProcessingException('Cancelled; any partial run folder is incomplete')
            try:
                child.wait(timeout=.1); break
            except subprocess.TimeoutExpired: continue
        drain(final=True)
        if child.returncode: raise QgsProcessingException('Isolated worker failed; see Processing log')
    finally:
        if child is not None and child.poll() is None: child.kill(); child.wait()
        reader.close(); writer.close()


class DownloadAlgorithm(QgsProcessingAlgorithm):
    def name(self): return 'download_surface_reflectance'
    def displayName(self): return 'Download surface reflectance (Sentinel-2A / Landsat 8–9)'
    def group(self): return 'Satellite imagery'
    def groupId(self): return 'satellite_imagery'
    def createInstance(self): return DownloadAlgorithm()
    def icon(self): return QIcon(str(Path(__file__).with_name('icon.svg')))
    def shortHelpString(self):
        return SHORT_HELP

    def helpUrl(self):
        return (Path(__file__).parent/'docs/HELP.html').resolve().as_uri()

    def initAlgorithm(self,config=None):
        self.addParameter(QgsProcessingParameterEnum('SATELLITE','Satellite',list(PLATFORMS),defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource('ROI','ROI polygon layer (or selected features)',[QgsProcessing.TypeVectorPolygon],optional=True))
        self.addParameter(QgsProcessingParameterExtent('EXTENT','ROI extent (alternative to polygon)',optional=True))
        today=date.today()
        self.addParameter(QgsProcessingParameterString('START','Start date (YYYY-MM-DD, UTC)',defaultValue=(today-timedelta(days=90)).isoformat()))
        self.addParameter(QgsProcessingParameterString('END','End date (YYYY-MM-DD, inclusive UTC)',defaultValue=today.isoformat()))
        for name,label,default,maximum in [('CLOUD','Maximum scene cloud (%)',20,100),('COVERAGE','Minimum ROI footprint coverage (%)',90,100),('RESOLUTION','Output pixel size (m; 0 = satellite default)',0,1000)]:
            param=QgsProcessingParameterNumber(name,label,QgsProcessingParameterNumber.Double,defaultValue=default,minValue=0,maxValue=maximum)
            if name=='RESOLUTION': param.setMetadata({'widget_wrapper':{'class':__package__+'.processing_widgets.SatelliteResolution'}})
            self.addParameter(param)
        self.addParameter(QgsProcessingParameterNumber('MAX_SCENES','Candidate scene cap',QgsProcessingParameterNumber.Integer,defaultValue=30,minValue=1,maxValue=200))
        param=QgsProcessingParameterString('BANDS','Reflectance bands',defaultValue='',optional=True)
        param.setMetadata({'widget_wrapper':{'class':__package__+'.processing_widgets.BandChecklist'}})
        self.addParameter(param)
        self.addParameter(QgsProcessingParameterEnum('MODE','Output mode',['Single scene (lowest scene cloud)','Temporal median'],defaultValue=0))
        self.addParameter(QgsProcessingParameterString('SCENE_IDS','Optional exact scene IDs (comma-separated)',optional=True))
        self.addParameter(QgsProcessingParameterBoolean('WATER_ONLY','Keep water pixels only',defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum('WATER_METHOD','Water method (single scene only)',['hybrid','classification (SCL/QA)','ndwi','mndwi'],defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean('STRICT','Strict hybrid water mask (single scene only)',defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean('OTSU','Automatic Otsu water threshold (single scene only)',defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber('THRESHOLD','Manual water threshold (single scene; Otsu off)',QgsProcessingParameterNumber.Double,defaultValue=0,minValue=-1,maxValue=1))
        self.addParameter(QgsProcessingParameterFolderDestination('OUTPUT','Output directory'))
        self.addParameter(QgsProcessingParameterBoolean('LOAD_OUTPUT','Add reflectance to QGIS map after completion',defaultValue=True))
        self.addOutput(QgsProcessingOutputRasterLayer('REFLECTANCE','Surface reflectance'))
        for parameter in self.parameterDefinitions():
            parameter.setHelp(PARAMETER_HELP.get(parameter.name(),''))

    def build_config(self,parameters,context):
        satellite=list(PLATFORMS)[self.parameterAsEnum(parameters,'SATELLITE',context)]
        source=self.parameterAsSource(parameters,'ROI',context)
        has_extent=parameters.get('EXTENT') not in (None,'')
        if source is not None and has_extent: raise QgsProcessingException('Choose either polygon ROI or extent, not both')
        crs=QgsCoordinateReferenceSystem('EPSG:4326')
        if source is not None:
            geometries=[f.geometry() for f in source.getFeatures() if not f.geometry().isEmpty()]
            if not geometries: raise QgsProcessingException('ROI contains no polygons')
            geometry=QgsGeometry.unaryUnion(geometries)
            geometry.transform(QgsCoordinateTransform(source.sourceCrs(),crs,context.transformContext()))
        elif has_extent:
            geometry=self.parameterAsExtentGeometry(parameters,'EXTENT',context,crs)
        else: raise QgsProcessingException('Provide a polygon ROI or an extent')
        if geometry.isEmpty() or not geometry.isGeosValid(): raise QgsProcessingException('ROI geometry is empty or invalid')
        raw=self.parameterAsString(parameters,'BANDS',context).strip()
        codes=[s.strip() for s in raw.replace(';',',').split(',') if s.strip()] if raw else [b[0] for b in bands(satellite)]
        if len(codes)!=len(set(codes)): raise QgsProcessingException('Duplicate band codes are not allowed')
        method=['hybrid','scl' if satellite=='Sentinel-2A' else 'qa','ndwi','mndwi'][self.parameterAsEnum(parameters,'WATER_METHOD',context)]
        config=dict(platform=satellite,aoi=json.loads(geometry.asJson()),bands=codes,
            start=self.parameterAsString(parameters,'START',context),end=self.parameterAsString(parameters,'END',context),
            cloud=self.parameterAsDouble(parameters,'CLOUD',context),coverage=self.parameterAsDouble(parameters,'COVERAGE',context),
            resolution=self.parameterAsDouble(parameters,'RESOLUTION',context) or default_resolution(satellite),max_scenes=self.parameterAsInt(parameters,'MAX_SCENES',context),
            mode='single' if self.parameterAsEnum(parameters,'MODE',context)==0 else 'median',
            water_only=self.parameterAsBool(parameters,'WATER_ONLY',context),water_method=method,
            water_mode='strict' if self.parameterAsBool(parameters,'STRICT',context) else 'permissive',
            threshold=None if self.parameterAsBool(parameters,'OTSU',context) else self.parameterAsDouble(parameters,'THRESHOLD',context))
        try: validate(config)
        except ValueError as exc: raise QgsProcessingException(str(exc)) from exc
        return config

    def processAlgorithm(self,parameters,context,feedback):
        config=self.build_config(parameters,context)
        folder=Path(self.parameterAsString(parameters,'OUTPUT',context))
        if str(folder)=='.': raise QgsProcessingException('Choose an output directory')
        if folder.exists() and not folder.is_dir(): raise QgsProcessingException('Output must be a directory')
        folder.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='spectral_harbor_processing_') as scratch:
            result=Path(scratch)/'scenes.json'
            feedback.pushInfo('Searching Earth Search and checking ROI footprint coverage…')
            run_worker({'action':'search','config':config,'result':str(result)},scratch,feedback)
            items=json.loads(result.read_text(encoding='utf-8'))
            requested={s.strip() for s in self.parameterAsString(parameters,'SCENE_IDS',context).split(',') if s.strip()}
            if requested:
                items=[item for item in items if item['id'] in requested]
                missing=requested-{item['id'] for item in items}
                if missing: raise QgsProcessingException('Scene IDs not found within filtered/capped search: '+', '.join(sorted(missing)))
            if not items: raise QgsProcessingException('No scenes pass search and ROI coverage filters')
            if config['mode']=='single':
                if len(requested)>1: raise QgsProcessingException('Single mode accepts at most one exact scene ID')
                items=items[:1]
            feedback.setProgress(40)
            run=folder/('SpectralHarbor_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:6])
            feedback.pushInfo(f'Processing {len(items)} scene(s) into {run}')
            run_worker({'action':'process','config':config,'items':items,'outdir':str(run)},scratch,feedback)
        primary_file = run / 'primary_raster.txt'
        if primary_file.is_file():
            raster = Path(primary_file.read_text(encoding='utf-8').strip())
        else:
            candidates = [p for p in run.glob('*.tif') if p.name not in ('valid_count.tif', 'temporal_std.tif', 'water_occurrence.tif', 'water_mask.tif', 'reflectance.tif')]
            raster = candidates[0] if candidates else (run / 'reflectance.tif')
        if not raster.is_file() or not (run/'provenance.json').is_file(): raise QgsProcessingException('Worker did not complete outputs')
        if self.parameterAsBool(parameters,'LOAD_OUTPUT',context) and context.project() is not None:
            # Let Processing load the raster on completion on the main thread using descriptive filename.
            layer_title = raster.stem
            details=QgsProcessingContext.LayerDetails(
                layer_title,context.project(),'REFLECTANCE',
                QgsProcessingUtils.LayerHint.Raster)
            context.addLayerToLoadOnCompletion(str(raster),details)
        feedback.setProgress(100)
        return {'OUTPUT':str(run),'REFLECTANCE':str(raster)}


class Provider(QgsProcessingProvider):
    def id(self): return 'spectralharbor'
    def name(self): return 'Spectral Harbor'
    def longName(self): return 'Spectral Harbor — satellite surface reflectance'
    def icon(self): return QIcon(str(Path(__file__).with_name('icon.svg')))
    def loadAlgorithms(self): self.addAlgorithm(DownloadAlgorithm())
