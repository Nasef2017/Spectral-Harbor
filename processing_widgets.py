"""Satellite-dependent band checklist in the standard Processing dialog."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QListWidget,QListWidgetItem,QLineEdit,QDoubleSpinBox
from qgis.gui import QgsProcessingGui
from processing.gui.wrappers import WidgetWrapper
from .catalog import PLATFORMS,bands,default_resolution


class SatelliteResolution(WidgetWrapper):
    def createWidget(self,**kwargs):
        self.manual=False; self.updating=False
        widget=QDoubleSpinBox(); widget.setRange(0,1000); widget.setDecimals(2)
        widget.setSpecialValueText('Automatic satellite default')
        widget.valueChanged.connect(self.changed)
        return widget

    def changed(self,value):
        if not self.updating: self.manual=value!=0
        self.widgetValueHasChanged.emit(self)

    def postInitialize(self,wrappers):
        if self.dialogType!=QgsProcessingGui.WidgetType.Standard: return
        self.satellite=next(w for w in wrappers if w.parameterDefinition().name()=='SATELLITE')
        self.satellite.widgetValueHasChanged.connect(self.satellite_changed)
        self.satellite_changed()

    def satellite_changed(self,*args):
        if not self.manual:
            self.updating=True
            self.widget.setValue(default_resolution(list(PLATFORMS)[self.satellite.widgetValue() or 0]))
            self.updating=False

    def setValue(self,value):
        self.widget.setValue(float(value or 0))
        self.manual=bool(value)

    def value(self): return self.widget.value()


class BandChecklist(WidgetWrapper):
    def createWidget(self,**kwargs):
        self.satellite=None
        if self.dialogType!=QgsProcessingGui.WidgetType.Standard:
            widget=QLineEdit(); widget.setPlaceholderText('Comma-separated band codes; blank = all satellite bands')
            widget.textChanged.connect(lambda _:self.widgetValueHasChanged.emit(self))
            return widget
        widget=QListWidget(); widget.setMinimumHeight(170)
        self.populate(widget,0)
        widget.itemChanged.connect(lambda _:self.widgetValueHasChanged.emit(self))
        return widget

    def populate(self,widget,index):
        widget.blockSignals(True); widget.clear()
        for code,key,label,res in bands(list(PLATFORMS)[int(index)]):
            item=QListWidgetItem(f'{code} · {label} · {res} m')
            item.setData(Qt.ItemDataRole.UserRole,code)
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked); widget.addItem(item)
        widget.blockSignals(False)

    def postInitialize(self,wrappers):
        if self.dialogType!=QgsProcessingGui.WidgetType.Standard: return
        self.satellite=next(w for w in wrappers if w.parameterDefinition().name()=='SATELLITE')
        self.satellite.widgetValueHasChanged.connect(self.satellite_changed)
        self.satellite_changed()

    def satellite_changed(self,*args):
        self.populate(self.widget,self.satellite.widgetValue() or 0)
        self.widgetValueHasChanged.emit(self)

    def setValue(self,value):
        value='' if value is None else str(value)
        if isinstance(self.widget,QLineEdit): self.widget.setText(value); return
        chosen=set(value.replace(';',',').split(',')) if value.strip() else None
        for i in range(self.widget.count()):
            item=self.widget.item(i)
            item.setCheckState(Qt.CheckState.Checked if chosen is None or item.data(Qt.ItemDataRole.UserRole) in chosen else Qt.CheckState.Unchecked)

    def value(self):
        if isinstance(self.widget,QLineEdit): return self.widget.text()
        codes=[self.widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.widget.count())
               if self.widget.item(i).checkState()==Qt.CheckState.Checked]
        # Empty string is reserved for 'all' in batch/modeler, so distinguish no UI selection.
        return ','.join(codes) if codes else '__NO_BANDS__'
