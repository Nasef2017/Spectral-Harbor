"""STAC contracts and provider abstraction. No QGIS, GDAL, rasterio or pyproj imports."""
import copy
import datetime as dt
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse, quote

ENDPOINT = 'https://earth-search.aws.element84.com/v1'
PLANETARY_COMPUTER_ENDPOINT = 'https://planetarycomputer.microsoft.com/api/stac/v1'
PLANETARY_COMPUTER_SAS_TOKEN_URL = 'https://planetarycomputer.microsoft.com/api/sas/v1/token'

PROVIDER_EARTH_SEARCH = 'earth_search'
PROVIDER_PLANETARY_COMPUTER = 'planetary_computer'

PROVIDERS = {
    'Element 84 Earth Search': PROVIDER_EARTH_SEARCH,
    'Microsoft Planetary Computer': PROVIDER_PLANETARY_COMPUTER,
}

PROVIDER_CONFIGS = {
    PROVIDER_EARTH_SEARCH: {
        'key': PROVIDER_EARTH_SEARCH,
        'label': 'Element 84 Earth Search',
        'endpoint': ENDPOINT,
        's2_collection': 'sentinel-2-c1-l2a',
        'landsat_collection': 'landsat-c2-l2',
        's2_query_platforms': ['sentinel-2a'],
        'requires_signing': False,
    },
    PROVIDER_PLANETARY_COMPUTER: {
        'key': PROVIDER_PLANETARY_COMPUTER,
        'label': 'Microsoft Planetary Computer',
        'endpoint': PLANETARY_COMPUTER_ENDPOINT,
        's2_collection': 'sentinel-2-l2a',
        'landsat_collection': 'landsat-c2-l2',
        's2_query_platforms': ['Sentinel-2A', 'sentinel-2a'],
        'requires_signing': True,
    },
}


class _LandsatAuthCache:
    auth_query = None
    expires_at = 0.0


_LANDSAT_AUTH_CACHE = _LandsatAuthCache()
_SAS_TOKEN_CACHE = {}

PLATFORMS = {
    'Sentinel-2A': ['sentinel-2a'],
    'Landsat-8': ['landsat-8'],
    'Landsat-9': ['landsat-9'],
    'Landsat-8 & 9': ['landsat-8', 'landsat-9'],
}

DISALLOWED_PLATFORMS = {'sentinel-2b', 'sentinel-2c'}


def normalize_provider(provider=None):
    """Normalize provider identifier or UI label into canonical key ('earth_search' or 'planetary_computer')."""
    if provider is None or provider == '' or provider == 0 or provider == '0':
        return PROVIDER_EARTH_SEARCH
    if provider == 1 or provider == '1':
        return PROVIDER_PLANETARY_COMPUTER
    raw = str(provider).strip().lower().replace('-', '_').replace(' ', '_')
    if raw in ('earth_search', 'element_84_earth_search', 'element84', 'earthsearch', 'aws'):
        return PROVIDER_EARTH_SEARCH
    if raw in ('planetary_computer', 'microsoft_planetary_computer', 'mpc', 'pc', 'microsoft'):
        return PROVIDER_PLANETARY_COMPUTER
    raise ValueError(f'Unsupported imagery provider: {provider}')


def provider_label(provider=None):
    """Return human-readable label for a provider key."""
    return PROVIDER_CONFIGS[normalize_provider(provider)]['label']


def default_resolution(platform):
    if platform not in PLATFORMS:
        raise ValueError('Unsupported satellite')
    return 10 if platform == 'Sentinel-2A' else 30


# Code, canonical STAC asset key, descriptive label, native resolution (metres).
S2 = [
    ('B01', 'coastal', 'Coastal aerosol', 60),
    ('B02', 'blue', 'Blue', 10),
    ('B03', 'green', 'Green', 10),
    ('B04', 'red', 'Red', 10),
    ('B05', 'rededge1', 'Red edge 1', 20),
    ('B06', 'rededge2', 'Red edge 2', 20),
    ('B07', 'rededge3', 'Red edge 3', 20),
    ('B08', 'nir', 'Broad NIR', 10),
    ('B8A', 'nir08', 'Narrow NIR', 20),
    ('B09', 'nir09', 'Water vapour', 60),
    ('B11', 'swir16', 'SWIR 1', 20),
    ('B12', 'swir22', 'SWIR 2', 20),
]
LS = [
    (f'SR_B{i}', key, label, 30)
    for i, key, label in [
        (1, 'coastal', 'Coastal aerosol'),
        (2, 'blue', 'Blue'),
        (3, 'green', 'Green'),
        (4, 'red', 'Red'),
        (5, 'nir08', 'NIR'),
        (6, 'swir16', 'SWIR 1'),
        (7, 'swir22', 'SWIR 2'),
    ]
]

S2_AUX_ALIASES = {
    'scl': ['scl', 'SCL'],
    'aot': ['aot', 'AOT'],
    'wvp': ['wvp', 'WVP'],
    'visual': ['visual', 'TCI'],
    'preview': ['preview', 'rendered_preview', 'thumbnail'],
    'thumbnail': ['thumbnail', 'rendered_preview', 'preview'],
}

LS_AUX_ALIASES = {
    'qa_pixel': ['qa_pixel', 'QA_PIXEL'],
    'qa_radsat': ['qa_radsat', 'QA_RADSAT'],
    'qa_aerosol': ['qa_aerosol', 'SR_QA_AEROSOL'],
    'thumbnail': ['thumbnail', 'rendered_preview'],
    'reduced_resolution_browse': ['reduced_resolution_browse', 'rendered_preview', 'thumbnail'],
}


def bands(platform):
    if platform not in PLATFORMS:
        raise ValueError('Unsupported satellite')
    return S2 if platform == 'Sentinel-2A' else LS


def validate(config):
    normalize_provider(config.get('provider'))
    allowed = {b[0] for b in bands(config['platform'])}
    if not config.get('bands') or not set(config['bands']) <= allowed:
        raise ValueError('Select at least one valid reflectance band')
    start, end = [dt.date.fromisoformat(config[k]) for k in ('start', 'end')]
    if start > end:
        raise ValueError('Start date must precede end date')
    if not 0 <= float(config['cloud']) <= 100:
        raise ValueError('Cloud limit must be 0–100 percent')
    if not 1 <= int(config.get('max_scenes', 30)) <= 200:
        raise ValueError('Scene cap must be 1–200')
    if not 0 <= float(config.get('coverage', 0)) <= 100:
        raise ValueError('Coverage must be 0–100 percent')
    if not 1 <= float(config.get('resolution', 10)) <= 1000:
        raise ValueError('Pixel size must be 1–1000 metres')
    if config.get('mode', 'single') not in ('single', 'median'):
        raise ValueError('Unknown processing mode')
    if config.get('water_method', 'hybrid') not in ('scl', 'qa', 'ndwi', 'mndwi', 'hybrid'):
        raise ValueError('Unknown water method')


def request_json(url, body=None):
    if not str(url).lower().startswith(('http://', 'https://')) or urlparse(url).scheme != 'https':
        raise ValueError('Remote requests require HTTPS')
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=None if body is None else json.dumps(body).encode(),
                headers={'Content-Type': 'application/json', 'User-Agent': 'SpectralHarbor/1.4.0'},
            )
            with urllib.request.urlopen(req, timeout=45) as response:  # nosec B310
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def _parse_baseline_float(baseline_str):
    if not baseline_str or str(baseline_str).strip() in ('N/A', 'None', ''):
        return None
    try:
        clean = str(baseline_str).strip().lstrip('Vv')
        parts = clean.split('.')
        if len(parts) >= 2:
            return float(f'{int(parts[0])}.{parts[1]}')
        return float(parts[0])
    except Exception:
        return None


def _default_s2_offset(props):
    if props.get('earthsearch:boa_offset_applied') is True:
        return 0.0
    pb = _parse_baseline_float(props.get('s2:processing_baseline'))
    if pb is not None:
        return -0.1 if pb >= 4.0 else 0.0
    return -0.1


def _resolve_raw_asset(assets, candidates):
    for key in candidates:
        if key in assets:
            return assets[key]
    lower_map = {k.lower(): v for k, v in assets.items()}
    for key in candidates:
        if key.lower() in lower_map:
            return lower_map[key.lower()]
    return None


def normalize_feature(feature, provider=None):
    """
    Normalize a STAC Feature from either Element 84 Earth Search or Microsoft Planetary Computer
    so that downstream processing functions receive an identical scene contract:
      - lowercase properties['platform'] ('sentinel-2a', 'landsat-8', 'landsat-9')
      - canonical asset keys ('coastal', 'blue', ..., 'scl' / 'qa_pixel', 'qa_radsat') AND band codes
      - explicit 'raster:bands' scale, offset, nodata, data_type, spatial_resolution metadata
    """
    norm_provider = normalize_provider(
        provider or feature.get('properties', {}).get('spectralharbor:provider')
    )
    item = copy.deepcopy(feature)
    props = item.setdefault('properties', {})
    plat_raw = str(props.get('platform', '')).strip().lower()
    feat_id = str(item.get('id', ''))

    if plat_raw in DISALLOWED_PLATFORMS or 's2b' in feat_id.lower() or 's2c' in feat_id.lower():
        raise ValueError(f'Disallowed platform in scene {feat_id}: {plat_raw}')

    props['platform'] = plat_raw
    props['spectralharbor:provider'] = norm_provider
    raw_assets = item.get('assets', {})
    normalized_assets = dict(raw_assets)

    if plat_raw == 'sentinel-2a':
        default_offset = _default_s2_offset(props)
        for code, canonical_key, _label, res in S2:
            ast = _resolve_raw_asset(raw_assets, [canonical_key, code, code.lower()])
            if ast is not None:
                ast_copy = copy.deepcopy(ast)
                rb_list = ast_copy.get('raster:bands') or ast_copy.get('bands') or [{}]
                rb0 = dict(rb_list[0]) if rb_list else {}
                rb0.setdefault('scale', 0.0001)
                if 'offset' not in rb0 or rb0['offset'] is None:
                    rb0['offset'] = default_offset
                elif rb0['offset'] == 0 and default_offset == -0.1 and props.get('earthsearch:boa_offset_applied') is not True:
                    rb0['offset'] = -0.1
                rb0.setdefault('nodata', 0)
                rb0.setdefault('data_type', 'uint16')
                rb0.setdefault('spatial_resolution', res)
                ast_copy['raster:bands'] = [rb0]
                normalized_assets[canonical_key] = ast_copy
                normalized_assets[code] = ast_copy
        for canonical_aux, aliases in S2_AUX_ALIASES.items():
            ast = _resolve_raw_asset(raw_assets, aliases)
            if ast is not None:
                ast_copy = copy.deepcopy(ast)
                normalized_assets[canonical_aux] = ast_copy
                normalized_assets[canonical_aux.upper()] = ast_copy
    elif plat_raw in ('landsat-8', 'landsat-9'):
        for code, canonical_key, _label, res in LS:
            b_short = code.replace('SR_', '')
            ast = _resolve_raw_asset(raw_assets, [canonical_key, code, code.lower(), b_short, b_short.lower()])
            if ast is not None:
                ast_copy = copy.deepcopy(ast)
                rb_list = ast_copy.get('raster:bands') or ast_copy.get('bands') or [{}]
                rb0 = dict(rb_list[0]) if rb_list else {}
                rb0.setdefault('scale', 0.0000275)
                if 'offset' not in rb0 or rb0['offset'] is None or rb0['offset'] == 0:
                    rb0['offset'] = -0.2
                rb0.setdefault('nodata', 0)
                rb0.setdefault('data_type', 'uint16')
                rb0.setdefault('spatial_resolution', res)
                ast_copy['raster:bands'] = [rb0]
                normalized_assets[canonical_key] = ast_copy
                normalized_assets[code] = ast_copy
        for canonical_aux, aliases in LS_AUX_ALIASES.items():
            ast = _resolve_raw_asset(raw_assets, aliases)
            if ast is not None:
                ast_copy = copy.deepcopy(ast)
                normalized_assets[canonical_aux] = ast_copy
                normalized_assets[canonical_aux.upper()] = ast_copy

    item['assets'] = normalized_assets
    return item


def search(config, log=print, timing_out=None):
    validate(config)
    provider_key = normalize_provider(config.get('provider'))
    p_cfg = PROVIDER_CONFIGS[provider_key]
    endpoint = p_cfg['endpoint']
    is_s2 = config['platform'] == 'Sentinel-2A'
    collection = p_cfg['s2_collection'] if is_s2 else p_cfg['landsat_collection']
    canonical_platforms = PLATFORMS[config['platform']]
    query_platforms = p_cfg['s2_query_platforms'] if is_s2 else canonical_platforms

    body = {
        'collections': [collection],
        'intersects': config['aoi'],
        'datetime': config['start'] + 'T00:00:00Z/' + config['end'] + 'T23:59:59.999999Z',
        'query': {'platform': {'in': query_platforms}, 'eo:cloud_cover': {'lte': config['cloud']}},
        'limit': min(config['max_scenes'], 100),
    }
    url, result, seen, visited = endpoint + '/search', [], set(), set()
    t_search_total = 0.0
    t_discover_total = 0.0

    while url and len(result) < config['max_scenes']:
        marker = (url, json.dumps(body, sort_keys=True))
        if marker in visited:
            raise RuntimeError('Repeated STAC pagination link')
        visited.add(marker)

        t0 = time.perf_counter()
        page = request_json(url, body)
        t_search_total += time.perf_counter() - t0

        t1 = time.perf_counter()
        for feature in page.get('features', []):
            props = feature.get('properties', {})
            plat_lower = str(props.get('platform', '')).strip().lower()
            feat_id = str(feature.get('id', ''))
            if plat_lower in DISALLOWED_PLATFORMS or 's2b' in feat_id.lower() or 's2c' in feat_id.lower():
                continue
            if plat_lower not in canonical_platforms or props.get('eo:cloud_cover', 101) > config['cloud']:
                continue
            # Defend against servers ignoring the temporal filter.
            if not config['start'] <= props.get('datetime', '')[:10] <= config['end']:
                continue
            if feat_id not in seen:
                seen.add(feat_id)
                result.append(normalize_feature(feature, provider=provider_key))
            if len(result) == config['max_scenes']:
                break
        t_discover_total += time.perf_counter() - t1

        link = next((x for x in page.get('links', []) if x.get('rel') == 'next'), None)
        url = link['href'] if link else None
        if link:
            if urlparse(url).netloc != urlparse(endpoint).netloc:
                raise ValueError('Unexpected STAC pagination host')
            if link.get('method', 'GET').upper() == 'POST':
                body = {**body, **link.get('body', {})} if link.get('merge') else link.get('body', {})
            else:
                body = None

    if timing_out is not None and isinstance(timing_out, dict):
        timing_out['provider'] = provider_key
        timing_out['stac_search_sec'] = round(timing_out.get('stac_search_sec', 0.0) + t_search_total, 6)
        timing_out['asset_discovery_sec'] = round(timing_out.get('asset_discovery_sec', 0.0) + t_discover_total, 6)

    log(f'[{p_cfg["label"]}] {len(result)} candidate scenes (cap {config["max_scenes"]}); lower scene cloud ranks first.')
    return sorted(result, key=lambda f: (f['properties'].get('eo:cloud_cover', 101), f['id']))


def _fetch_cached_sas_token(token_path):
    """Fetch and cache a short-lived Planetary Computer SAS token for a collection or storage container."""
    now = time.time()
    cached = _SAS_TOKEN_CACHE.get(token_path)
    if cached and cached['expires_at'] > now + 30:
        return cached['token']

    try:
        result = request_json(f'{PLANETARY_COMPUTER_SAS_TOKEN_URL}/{token_path}')
        auth_val = result.get('token', '').lstrip('?')
        if auth_val:
            expiry = result.get('msft:expiry') or result.get('expiry')
            expires = now + 300
            if expiry:
                expires = min(expires, dt.datetime.fromisoformat(expiry.replace('Z', '+00:00')).timestamp())
            _SAS_TOKEN_CACHE[token_path] = {'token': auth_val, 'expires_at': expires}
            if 'landsat' in token_path:
                _LANDSAT_AUTH_CACHE.auth_query = auth_val
                _LANDSAT_AUTH_CACHE.expires_at = expires
            return auth_val
    except (ValueError, urllib.error.URLError, TimeoutError):
        pass
    return None


def sign_planetary_computer_url(unsigned_url, collection_id=None, timing_out=None):
    """Sign a Microsoft Planetary Computer Azure Blob Storage URL using cached collection SAS tokens."""
    t0 = time.perf_counter()
    try:
        if not unsigned_url or not isinstance(unsigned_url, str):
            return unsigned_url
        # Prevent double-signing if SAS signature already exists
        if '?' in unsigned_url and ('sig=' in unsigned_url or 'sv=' in unsigned_url):
            return unsigned_url
        if '.blob.core.windows.net' not in unsigned_url:
            return unsigned_url

        token_candidates = []
        if collection_id:
            token_candidates.append(collection_id)
        if 'sentinel2' in unsigned_url or 'sentinel-2' in unsigned_url:
            token_candidates.append('sentinel-2-l2a')
        if 'landsat' in unsigned_url:
            token_candidates.extend(['landsat-c2-l2', 'landsateuwest/landsat-c2'])
        if not token_candidates:
            token_candidates.append('sentinel-2-l2a')

        for candidate in token_candidates:
            token = _fetch_cached_sas_token(candidate)
            if token:
                sep = '&' if '?' in unsigned_url else '?'
                return f'{unsigned_url}{sep}{token}'

        return request_json(
            'https://planetarycomputer.microsoft.com/api/sas/v1/sign?href=' + quote(unsigned_url, safe='')
        )['href']
    finally:
        if timing_out is not None and isinstance(timing_out, dict):
            timing_out['url_signing_sec'] = round(
                timing_out.get('url_signing_sec', 0.0) + (time.perf_counter() - t0), 6
            )


def asset_url(asset, provider=None, collection=None, timing_out=None):
    href = asset['href'] if isinstance(asset, dict) else str(asset)
    if href.startswith('s3://usgs-landsat/'):
        t0 = time.perf_counter()
        if 'collection02/' in href:
            path = href.split('collection02/', 1)[1]
        else:
            path = href.split('s3://usgs-landsat/', 1)[1]
        unsigned = 'https://landsateuwest.blob.core.windows.net/landsat-c2/' + path
        if _LANDSAT_AUTH_CACHE.auth_query and _LANDSAT_AUTH_CACHE.expires_at > time.time() + 30:
            if timing_out is not None and isinstance(timing_out, dict):
                timing_out['url_signing_sec'] = round(
                    timing_out.get('url_signing_sec', 0.0) + (time.perf_counter() - t0), 6
                )
            return unsigned + '?' + _LANDSAT_AUTH_CACHE.auth_query.lstrip('?')
        return sign_planetary_computer_url(unsigned, collection_id='landsateuwest/landsat-c2', timing_out=timing_out)

    if '.blob.core.windows.net' in href:
        return sign_planetary_computer_url(href, collection_id=collection, timing_out=timing_out)

    if not href.startswith('https://'):
        raise ValueError('Asset has no supported public HTTPS location')
    return href


def calibration(asset):
    rb = asset.get('raster:bands', asset.get('bands', [{}]))[0]
    # Fail closed: baseline heuristics and value-range detection can double-apply offsets.
    if 'scale' not in rb or 'offset' not in rb or 'nodata' not in rb:
        raise ValueError('Reflectance asset lacks explicit scale, offset or nodata metadata')
    return float(rb['scale']), float(rb['offset']), rb['nodata']
