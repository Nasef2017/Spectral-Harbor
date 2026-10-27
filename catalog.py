"""STAC contracts. No QGIS, GDAL, rasterio or pyproj imports."""
import datetime as dt
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse, quote

ENDPOINT = 'https://earth-search.aws.element84.com/v1'
class _LandsatAuthCache:
    auth_query = None
    expires_at = 0.0


_LANDSAT_AUTH_CACHE = _LandsatAuthCache()
PLATFORMS = {'Sentinel-2A': ['sentinel-2a'], 'Landsat-8': ['landsat-8'],
             'Landsat-9': ['landsat-9'], 'Landsat-8 & 9': ['landsat-8', 'landsat-9']}


def default_resolution(platform):
    if platform not in PLATFORMS: raise ValueError('Unsupported satellite')
    return 10 if platform=='Sentinel-2A' else 30
# Code, STAC asset, descriptive label, native resolution (metres).
S2 = [('B01','coastal','Coastal aerosol',60), ('B02','blue','Blue',10),
      ('B03','green','Green',10), ('B04','red','Red',10),
      ('B05','rededge1','Red edge 1',20), ('B06','rededge2','Red edge 2',20),
      ('B07','rededge3','Red edge 3',20), ('B08','nir','Broad NIR',10),
      ('B8A','nir08','Narrow NIR',20), ('B09','nir09','Water vapour',60),
      ('B11','swir16','SWIR 1',20), ('B12','swir22','SWIR 2',20)]
LS = [(f'SR_B{i}', key, label, 30) for i,key,label in
      [(1,'coastal','Coastal aerosol'),(2,'blue','Blue'),(3,'green','Green'),
       (4,'red','Red'),(5,'nir08','NIR'),(6,'swir16','SWIR 1'),(7,'swir22','SWIR 2')]]


def bands(platform):
    if platform not in PLATFORMS:
        raise ValueError('Unsupported satellite')
    return S2 if platform == 'Sentinel-2A' else LS


def validate(config):
    allowed = {b[0] for b in bands(config['platform'])}
    if not config.get('bands') or not set(config['bands']) <= allowed:
        raise ValueError('Select at least one valid reflectance band')
    start, end = [dt.date.fromisoformat(config[k]) for k in ('start','end')]
    if start > end:
        raise ValueError('Start date must precede end date')
    if not 0 <= float(config['cloud']) <= 100:
        raise ValueError('Cloud limit must be 0–100 percent')
    if not 1 <= int(config.get('max_scenes',30)) <= 200:
        raise ValueError('Scene cap must be 1–200')
    if not 0 <= float(config.get('coverage',0)) <= 100:
        raise ValueError('Coverage must be 0–100 percent')
    if not 1 <= float(config.get('resolution',10)) <= 1000:
        raise ValueError('Pixel size must be 1–1000 metres')
    if config.get('mode','single') not in ('single','median'):
        raise ValueError('Unknown processing mode')
    if config.get('water_method','hybrid') not in ('scl','qa','ndwi','mndwi','hybrid'):
        raise ValueError('Unknown water method')


def request_json(url, body=None):
    if not str(url).lower().startswith(('http://', 'https://')) or urlparse(url).scheme != 'https':
        raise ValueError('Remote requests require HTTPS')
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                  headers={'Content-Type':'application/json','User-Agent':'SpectralHarbor/1.3.3'})
            with urllib.request.urlopen(req, timeout=45) as response:  # nosec B310
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def search(config, log=print):
    validate(config)
    collection = 'sentinel-2-c1-l2a' if config['platform'] == 'Sentinel-2A' else 'landsat-c2-l2'
    platforms = PLATFORMS[config['platform']]
    body = {'collections':[collection], 'intersects':config['aoi'],
            'datetime':config['start']+'T00:00:00Z/'+config['end']+'T23:59:59.999999Z',
            'query':{'platform':{'in':platforms}, 'eo:cloud_cover':{'lte':config['cloud']}},
            'limit':min(config['max_scenes'],100)}
    url, result, seen, visited = ENDPOINT+'/search', [], set(), set()
    while url and len(result) < config['max_scenes']:
        marker = (url,json.dumps(body,sort_keys=True))
        if marker in visited:
            raise RuntimeError('Repeated STAC pagination link')
        visited.add(marker)
        page = request_json(url,body)
        for feature in page.get('features',[]):
            props = feature['properties']
            if props.get('platform') not in platforms or props.get('eo:cloud_cover',101) > config['cloud']:
                continue
            # Defend against servers ignoring the temporal filter.
            if not config['start'] <= props.get('datetime','')[:10] <= config['end']:
                continue
            if feature['id'] not in seen:
                seen.add(feature['id']); result.append(feature)
            if len(result) == config['max_scenes']:
                break
        link = next((x for x in page.get('links',[]) if x.get('rel') == 'next'),None)
        url = link['href'] if link else None
        if link:
            if urlparse(url).netloc != urlparse(ENDPOINT).netloc:
                raise ValueError('Unexpected STAC pagination host')
            if link.get('method','GET').upper() == 'POST':
                body = {**body,**link.get('body',{})} if link.get('merge') else link.get('body',{})
            else:
                body = None
    log(f'{len(result)} candidate scenes (cap {config["max_scenes"]}); lower scene cloud ranks first.')
    return sorted(result,key=lambda f:(f['properties'].get('eo:cloud_cover',101), f['id']))


def asset_url(asset):
    href = asset['href']
    if href.startswith('s3://usgs-landsat/collection02/'):
        # Use the documented signer; never fabricate blob paths or expose tokens in logs.
        path = href.split('collection02/',1)[1]
        unsigned = 'https://landsateuwest.blob.core.windows.net/landsat-c2/'+path
        # Bathymetrix-style public token caching avoids a signer request per band.
        # Cache briefly and respect the provider's actual expiry when available.
        if _LANDSAT_AUTH_CACHE.expires_at <= time.time() + 30:
            try:
                result = request_json('https://planetarycomputer.microsoft.com/api/sas/v1/token/landsateuwest/landsat-c2')
                auth_val = result.get('token')
                if auth_val:
                    expiry = result.get('msft:expiry') or result.get('expiry')
                    expires = time.time() + 300
                    if expiry:
                        expires = min(expires, dt.datetime.fromisoformat(expiry.replace('Z', '+00:00')).timestamp())
                    _LANDSAT_AUTH_CACHE.auth_query = auth_val
                    _LANDSAT_AUTH_CACHE.expires_at = expires
            except (ValueError, urllib.error.URLError, TimeoutError):
                pass
        if _LANDSAT_AUTH_CACHE.auth_query and _LANDSAT_AUTH_CACHE.expires_at > time.time() + 30:
            return unsigned + '?' + _LANDSAT_AUTH_CACHE.auth_query.lstrip('?')
        return request_json('https://planetarycomputer.microsoft.com/api/sas/v1/sign?href='+quote(unsigned,safe=''))['href']
    if not href.startswith('https://'):
        raise ValueError('Asset has no supported public HTTPS location')
    return href


def calibration(asset):
    rb = asset.get('raster:bands',asset.get('bands',[{}]))[0]
    # Fail closed: baseline heuristics and value-range detection can double-apply offsets.
    if 'scale' not in rb or 'offset' not in rb or 'nodata' not in rb:
        raise ValueError('Reflectance asset lacks explicit scale, offset or nodata metadata')
    return float(rb['scale']), float(rb['offset']), rb['nodata']
