"""Spectral Harbor — standalone QGIS surface reflectance acquisition."""
__version__ = '1.3.3'


def classFactory(iface):
    from .plugin import Plugin
    return Plugin(iface)
