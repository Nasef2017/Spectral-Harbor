"""Qt6 QGIS interface; native raster libraries remain in the child process."""
import json
import sys
import tempfile
from pathlib import Path
from qgis.PyQt.QtCore import QDate, QProcess, QProcessEnvironment, Qt, QUrl
from qgis.PyQt.QtGui import QAction, QIcon, QColor, QFont, QDesktopServices
from qgis.PyQt.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QHBoxLayout,QComboBox,
    QListWidget,QListWidgetItem,QAbstractItemView,QPushButton,QLineEdit,QDateEdit,
    QDoubleSpinBox,QSpinBox,QCheckBox,QFileDialog,QPlainTextEdit,QMessageBox,QLabel,QScrollArea,QWidget,QProgressBar)
from qgis.core import (QgsGeometry,QgsCoordinateReferenceSystem,QgsCoordinateTransform,
    QgsProject,QgsRasterLayer,QgsWkbTypes,QgsRectangle,QgsApplication)
from qgis.gui import QgsMapTool, QgsRubberBand
from .catalog import PLATFORMS, bands, validate
from .events import parse_line
from .help_text import ABOUT_TEXT,PARAMETER_HELP
from . import __version__


class DrawTool(QgsMapTool):
    def __init__(self,canvas,polygon,callback):
        super().__init__(canvas); self.canvas=canvas; self.polygon=polygon
        self.callback=callback; self.points=[]
        self.rubber=QgsRubberBand(canvas,QgsWkbTypes.GeometryType.PolygonGeometry)
        self.rubber.setColor(QColor(21,176,165,100)); self.rubber.setWidth(2)

    def canvasReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.RightButton:
            if self.polygon and len(self.points)>=3: self.finish(self.points)
            return
        self.points.append(self.toMapCoordinates(event.pos()))
        if not self.polygon and len(self.points)==2:
            rect=QgsRectangle(self.points[0],self.points[1]); self.finish(None,rect)

    def canvasMoveEvent(self,event):
        if not self.points: return
        point=self.toMapCoordinates(event.pos())
        geom=(QgsGeometry.fromPolygonXY([self.points+[point,self.points[0]]]) if self.polygon
              else QgsGeometry.fromRect(QgsRectangle(self.points[0],point)))
        self.rubber.setToGeometry(geom,None)

    def finish(self,points,rect=None):
        geom=QgsGeometry.fromRect(rect) if rect else QgsGeometry.fromPolygonXY([points+[points[0]]])
        self.rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        self.callback(geom,self.canvas.mapSettings().destinationCrs())

    def deactivate(self):
        self.rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry); super().deactivate()


class Dialog(QDialog):
    def __init__(self,iface):
        super().__init__(iface.mainWindow()); self.iface=iface
        self.setFont(QFont('Segoe UI',9))
        self.setWindowTitle('Spectral Harbor • Surface reflectance'); self.resize(740,850)
        self.temp=tempfile.TemporaryDirectory(prefix='spectral_harbor_')
        self.items=[]; self.aoi=None; self.draw_tool=None; self.old_tool=None
        self.process=QProcess(self); self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_log)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        layout=QVBoxLayout(self); layout.addWidget(QLabel('<h2>Spectral Harbor</h2>Surface reflectance, ready for your ROI'))
        help_row=QHBoxLayout(); self.help_button=QPushButton('User guide'); self.about_button=QPushButton('About Spectral Harbor')
        help_row.addStretch(); help_row.addWidget(self.help_button); help_row.addWidget(self.about_button); layout.addLayout(help_row)
        self.help_button.clicked.connect(self.open_help); self.about_button.clicked.connect(self.show_about)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); content=QWidget()
        form=QFormLayout(content); scroll.setWidget(content); layout.addWidget(scroll,3)
        self.platform=QComboBox(); self.platform.addItems(PLATFORMS); form.addRow('Satellite',self.platform)
        self.roi_mode=QComboBox(); self.roi_mode.addItems(['Canvas extent','Active polygon layer','Selected polygons','Draw rectangle','Draw polygon','WGS84 bounds'])
        form.addRow('ROI source',self.roi_mode)
        self.bbox=QLineEdit(); self.bbox.setPlaceholderText('west,south,east,north (longitude,latitude)'); form.addRow('WGS84 bounds',self.bbox)
        self.capture=QPushButton('Capture ROI'); form.addRow(self.capture)
        self.roi_status=QLabel('Capture an ROI before searching.'); form.addRow(self.roi_status)
        self.start=QDateEdit(QDate.currentDate().addMonths(-3)); self.end=QDateEdit(QDate.currentDate())
        for widget in (self.start,self.end): widget.setCalendarPopup(True); widget.setDisplayFormat('yyyy-MM-dd')
        form.addRow('Start date (UTC)',self.start); form.addRow('End date (UTC, inclusive)',self.end)
        self.cloud=QDoubleSpinBox(); self.cloud.setRange(0,100); self.cloud.setValue(20); form.addRow('Maximum scene cloud %',self.cloud)
        self.coverage=QDoubleSpinBox(); self.coverage.setRange(0,100); self.coverage.setValue(90); form.addRow('Minimum ROI coverage %',self.coverage)
        self.cap=QSpinBox(); self.cap.setRange(1,200); self.cap.setValue(30); form.addRow('Candidate scene cap',self.cap)
        self.resolution=QDoubleSpinBox(); self.resolution.setRange(1,1000); self.resolution.setValue(10); form.addRow('Output pixel size (m)',self.resolution)
        self.band_list=QListWidget(); self.band_list.setMaximumHeight(150); form.addRow('Reflectance bands',self.band_list)
        self.mode=QComboBox(); self.mode.addItems(['Single scene','Temporal median']); form.addRow('Output mode',self.mode)
        self.water=QCheckBox('Keep water pixels only'); form.addRow(self.water)
        self.method=QComboBox(); self.method.addItems(['hybrid','scl','ndwi','mndwi']); form.addRow('Water method (single scene)',self.method)
        self.strict=QCheckBox('Strict hybrid (QA water AND spectral water)'); form.addRow(self.strict)
        self.auto_threshold=QCheckBox('Automatic Otsu threshold'); self.auto_threshold.setChecked(True); form.addRow(self.auto_threshold)
        self.threshold=QDoubleSpinBox(); self.threshold.setRange(-1,1); self.threshold.setDecimals(3); form.addRow('Manual index threshold',self.threshold)
        self.directory=QLineEdit(); browse=QPushButton('Choose folder'); row=QHBoxLayout(); row.addWidget(self.directory); row.addWidget(browse); form.addRow('Output directory',row)
        buttons=QHBoxLayout(); self.search_button=QPushButton('Search scenes'); self.run_button=QPushButton('Download selected'); self.cancel=QPushButton('Cancel job')
        for b in (self.search_button,self.run_button,self.cancel): buttons.addWidget(b)
        layout.addLayout(buttons)
        self.scenes=QListWidget(); self.scenes.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.scenes.setMaximumHeight(100)
        layout.addWidget(QLabel('Select one scene, or multiple scenes for median (Ctrl/Shift).')); layout.addWidget(self.scenes)
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(100); layout.addWidget(self.log)
        self.progress=QProgressBar(); self.progress.setRange(0,100); layout.addWidget(self.progress)
        self.platform.currentTextChanged.connect(self.satellite_changed)
        self.scenes.itemSelectionChanged.connect(self.availability)
        self.capture.clicked.connect(self.capture_roi); self.search_button.clicked.connect(self.search)
        self.run_button.clicked.connect(self.download); self.cancel.clicked.connect(self.cancel_job)
        browse.clicked.connect(self.choose_directory)
        for name,widget in [('SATELLITE',self.platform),('ROI',self.roi_mode),('START',self.start),('END',self.end),
            ('CLOUD',self.cloud),('COVERAGE',self.coverage),('MAX_SCENES',self.cap),('RESOLUTION',self.resolution),
            ('BANDS',self.band_list),('WATER_ONLY',self.water),('WATER_METHOD',self.method),('STRICT',self.strict),
            ('OTSU',self.auto_threshold),('THRESHOLD',self.threshold),('OUTPUT',self.directory)]:
            widget.setToolTip(PARAMETER_HELP[name])
        self.mode.setToolTip('Single scene: select exactly one result. Median: select the scenes to combine using Ctrl/Shift. Processing Toolbox offers automatic candidate selection instead.')
        self.mode.currentIndexChanged.connect(self.update_water_controls)
        self.method.currentTextChanged.connect(self.update_water_controls)
        self.auto_threshold.toggled.connect(self.update_water_controls)
        self.satellite_changed(); self.busy(False)

    def open_help(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(__file__).parent/'docs/HELP.html')))

    def show_about(self):
        from html import escape
        body=escape(ABOUT_TEXT).replace('\n\n','</p><p>')
        QMessageBox.about(self,'About Spectral Harbor',f'<h3>Spectral Harbor {__version__}</h3><p>{body}</p>')

    def update_water_controls(self,*args):
        if self.process.state()!=QProcess.ProcessState.NotRunning: return
        single=self.mode.currentIndex()==0
        spectral=self.method.currentText() in ('hybrid','ndwi','mndwi')
        self.method.setEnabled(single)
        self.strict.setEnabled(single and self.method.currentText()=='hybrid')
        self.auto_threshold.setEnabled(single and spectral)
        self.threshold.setEnabled(single and spectral and not self.auto_threshold.isChecked())

    def satellite_changed(self):
        self.items=[]; self.scenes.clear(); self.band_list.clear()
        for code,key,label,res in bands(self.platform.currentText()):
            item=QListWidgetItem(f'{code} · {label} · {res} m'); item.setData(Qt.ItemDataRole.UserRole,(code,key))
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked)
            self.band_list.addItem(item)
        self.resolution.setValue(10 if self.platform.currentText()=='Sentinel-2A' else 30)
        self.method.clear(); self.method.addItems(['hybrid','scl' if self.platform.currentText()=='Sentinel-2A' else 'qa','ndwi','mndwi'])

    def availability(self):
        chosen=[self.items[self.scenes.row(x)] for x in self.scenes.selectedItems()]
        for i in range(self.band_list.count()):
            item=self.band_list.item(i); code,key=item.data(Qt.ItemDataRole.UserRole)
            available=all(key in scene['assets'] for scene in chosen)
            flags=item.flags()
            item.setFlags(flags|Qt.ItemFlag.ItemIsEnabled if available else flags & ~Qt.ItemFlag.ItemIsEnabled)
            if not available: item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip('Present in selected scene metadata' if available else 'Missing in one or more selected scenes')

    def choose_directory(self):
        path=QFileDialog.getExistingDirectory(self,'Output directory',self.directory.text())
        if path: self.directory.setText(path)

    def set_roi(self,geom,crs):
        try:
            transform=QgsCoordinateTransform(crs,QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance())
            geom=QgsGeometry(geom); geom.transform(transform)
            if geom.isEmpty() or not geom.isGeosValid(): raise ValueError('Invalid ROI geometry')
            self.aoi=json.loads(geom.asJson()); self.items=[]; self.scenes.clear()
            self.roi_status.setText('ROI captured in WGS84.'); self.show(); self.raise_()
        except Exception as exc: self.error(str(exc))
        finally: self.restore_tool()

    def restore_tool(self):
        if self.draw_tool:
            self.iface.mapCanvas().unsetMapTool(self.draw_tool)
            if self.old_tool: self.iface.mapCanvas().setMapTool(self.old_tool)
            self.draw_tool=None

    def capture_roi(self):
        try:
            mode=self.roi_mode.currentText(); canvas=self.iface.mapCanvas()
            if mode.startswith('Draw'):
                self.old_tool=canvas.mapTool()
                self.draw_tool=DrawTool(canvas,mode=='Draw polygon',self.set_roi)
                canvas.setMapTool(self.draw_tool); self.hide()
                self.iface.messageBar().pushInfo('Spectral Harbor','Draw ROI. Polygon: right click to finish. Reopen dialog to cancel drawing.')
                return
            if mode=='Canvas extent':
                self.set_roi(QgsGeometry.fromRect(canvas.extent()),canvas.mapSettings().destinationCrs())
            elif mode=='WGS84 bounds':
                west,south,east,north=map(float,self.bbox.text().split(','))
                if west>=east or south>=north: raise ValueError('Bounds must be west,south,east,north')
                self.set_roi(QgsGeometry.fromRect(QgsRectangle(west,south,east,north)),QgsCoordinateReferenceSystem('EPSG:4326'))
            else:
                layer=self.iface.activeLayer()
                if layer is None or not hasattr(layer,'geometryType') or layer.geometryType()!=QgsWkbTypes.GeometryType.PolygonGeometry:
                    raise ValueError('Activate a polygon vector layer')
                features=layer.selectedFeatures() if mode=='Selected polygons' else layer.getFeatures()
                geoms=[f.geometry() for f in features if not f.geometry().isEmpty()]
                if not geoms: raise ValueError('No polygons in the chosen ROI source')
                self.set_roi(QgsGeometry.unaryUnion(geoms),layer.crs())
        except Exception as exc: self.error(str(exc))

    def config(self):
        if self.aoi is None: raise ValueError('Capture an ROI first')
        codes=[self.band_list.item(i).data(Qt.ItemDataRole.UserRole)[0] for i in range(self.band_list.count())
               if self.band_list.item(i).checkState()==Qt.CheckState.Checked]
        c={'platform':self.platform.currentText(),'aoi':self.aoi,'bands':codes,
           'start':self.start.date().toString('yyyy-MM-dd'),'end':self.end.date().toString('yyyy-MM-dd'),
           'cloud':self.cloud.value(),'coverage':self.coverage.value(),'max_scenes':self.cap.value(),
           'resolution':self.resolution.value(),'mode':'single' if self.mode.currentIndex()==0 else 'median',
           'water_only':self.water.isChecked(),'water_method':self.method.currentText(),
           'water_mode':'strict' if self.strict.isChecked() else 'permissive',
           'threshold':None if self.auto_threshold.isChecked() else self.threshold.value()}
        validate(c); return c

    def launch(self,job):
        # QGIS desktop executable is not a Python interpreter. Discover only the
        # Python belonging to this QGIS environment, never a random PATH Python.
        candidates=[Path(sys.prefix)/'python.exe',Path(sys.prefix)/'bin/python.exe']
        if sys.platform!='win32': candidates=[Path(sys.prefix)/'bin/python3']
        executable=next((p for p in candidates if p.is_file()),None)
        if executable is None: raise RuntimeError('Cannot locate the QGIS Python interpreter; see troubleshooting guide')
        self.job=job; self._log_buffer=''; self.progress.setValue(0)
        request=Path(self.temp.name)/'job.json'
        request.write_text(json.dumps(job),encoding='utf-8')
        env=QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONUNBUFFERED','1'); env.insert('PYTHONNOUSERSITE','1')
        env.insert('CPL_LOG',str(Path(self.temp.name)/'gdal-private.log'))
        self.process.setProcessEnvironment(env); self.busy(True)
        self.process.start(str(executable),[str(Path(__file__).with_name('worker.py')),str(request)])

    def search(self):
        try:
            self.search_config=self.config(); self.items=[]; self.scenes.clear()
            self.launch({'action':'search','config':self.search_config,'result':str(Path(self.temp.name)/'scenes.json')})
        except Exception as exc: self.error(str(exc))

    def download(self):
        try:
            config=self.config()
            if not hasattr(self,'search_config'): raise ValueError('Search scenes before downloading')
            if any(config[k]!=self.search_config[k] for k in ('platform','aoi','start','end','cloud','coverage','max_scenes')):
                raise ValueError('Search settings changed; search again before downloading')
            chosen=[self.items[self.scenes.row(x)] for x in self.scenes.selectedItems()]
            if not chosen: raise ValueError('Select scene(s) from search results')
            if config['mode']=='single' and len(chosen)!=1: raise ValueError('Select exactly one scene for single-scene mode')
            folder=Path(self.directory.text())
            if not self.directory.text() or not folder.is_dir(): raise ValueError('Choose an existing output directory')
            from datetime import datetime
            from uuid import uuid4
            run=folder/('SpectralHarbor_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:6])
            self.launch({'action':'process','config':config,'items':chosen,'outdir':str(run)})
        except Exception as exc: self.error(str(exc))

    def busy(self,value):
        for widget in (self.platform,self.roi_mode,self.capture,self.start,self.end,self.cloud,self.coverage,
                       self.cap,self.resolution,self.band_list,self.mode,self.water,self.method,self.strict,
                       self.auto_threshold,self.threshold,self.directory,self.scenes,self.search_button,self.run_button,self.bbox):
            widget.setEnabled(not value)
        self.cancel.setEnabled(value)
        if not value: self.update_water_controls()

    def cancel_job(self):
        self.cancelled=True; self.process.kill()

    def read_log(self):
        text=bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')
        lines=(getattr(self,'_log_buffer','')+text).split('\n'); self._log_buffer=lines.pop()
        for line in lines:
            message,percent=parse_line(line.strip())
            if message: self.log.appendPlainText(message)
            if percent is not None: self.progress.setValue(int(percent))

    def process_error(self,error):
        if error==QProcess.ProcessError.FailedToStart:
            self.busy(False); self.error('Worker could not start. Check QGIS Python dependencies.')

    def finished(self,code,status):
        self.read_log(); self.busy(False)
        if getattr(self,'cancelled',False):
            self.cancelled=False; self.log.appendPlainText('Cancelled. Partial run directory may be removed.'); return
        if code!=0 or status!=QProcess.ExitStatus.NormalExit:
            self.error('Worker failed; see log. QGIS remains available. Partial results are not complete.'); return
        self.progress.setValue(100)
        try:
            if self.job['action']=='search':
                self.items=json.loads(Path(self.job['result']).read_text(encoding='utf-8'))
                for scene in self.items:
                    p=scene['properties']; self.scenes.addItem(f'{scene["id"]} · cloud {p["eo:cloud_cover"]:.1f}% · ROI {p["spectralharbor:roi_coverage"]:.1f}%')
                if self.items: self.scenes.item(0).setSelected(True)
            else:
                primary_file = Path(self.job['outdir']) / 'primary_raster.txt'
                if primary_file.is_file():
                    path = Path(primary_file.read_text(encoding='utf-8').strip())
                else:
                    candidates = [p for p in Path(self.job['outdir']).glob('*.tif') if p.name not in ('valid_count.tif', 'temporal_std.tif', 'water_occurrence.tif', 'water_mask.tif', 'reflectance.tif')]
                    path = candidates[0] if candidates else (Path(self.job['outdir']) / 'reflectance.tif')
                layer=QgsRasterLayer(str(path), path.stem)
                if not layer.isValid(): raise ValueError('Output raster is invalid')
                QgsProject.instance().addMapLayer(layer)
                self.log.appendPlainText('Complete: '+str(path.name))
        except Exception as exc: self.error(str(exc))

    def error(self,message): QMessageBox.warning(self,'Spectral Harbor',message)

    def closeEvent(self,event):
        if self.process.state()!=QProcess.ProcessState.NotRunning:
            self.error('Cancel the active job before closing.'); event.ignore(); return
        self.restore_tool(); event.accept()

    def shutdown(self):
        self.process.kill(); self.process.waitForFinished(3000); self.restore_tool()
        self.temp.cleanup(); self.deleteLater()


class Plugin:
    def __init__(self,iface): self.iface=iface; self.dialog=None; self.provider=None

    def initGui(self):
        from .processing_provider import Provider
        self.provider=Provider()
        if not QgsApplication.processingRegistry().addProvider(self.provider):
            raise RuntimeError('Unable to register Spectral Harbor Processing provider')
        self.action=QAction(QIcon(str(Path(__file__).with_name('icon.svg'))),'Spectral Harbor',self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToRasterMenu('Spectral Harbor',self.action); self.iface.addToolBarIcon(self.action)

    def run(self):
        if self.dialog is None: self.dialog=Dialog(self.iface)
        self.dialog.restore_tool(); self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()

    def unload(self):
        if self.dialog: self.dialog.shutdown(); self.dialog=None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider); self.provider=None
        self.iface.removePluginRasterMenu('Spectral Harbor',self.action); self.iface.removeToolBarIcon(self.action)
        self.action.deleteLater()
