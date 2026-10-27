"""User-facing help shared by Processing and the bundled offline help page."""
ABOUT_TEXT = '''Spectral Harbor is a standalone QGIS 4 plugin for downloading selected surface-reflectance bands from Sentinel-2A Level-2A and Landsat 8/9 Collection 2 Level-2 through Earth Search.

Choose the satellite, area, dates, scene-cloud limit and bands. Create a single-scene image or a temporal median. Output pixels default to 10 m for Sentinel-2A and 30 m for Landsat; Sentinel bands with native 20/60 m resolution are resampled to the chosen grid.

Spectral Harbor handles image reading, sensor-specific reflectance normalization, quality layers and temporal compositing in a separate processing task. It runs independently and does not require another QGIS plugin.

Results are Float32 GeoTIFFs with -9999 nodata, native processing metadata, and descriptive Float32 filenames (e.g. S2A_YYYYMMDD_<SceneID>_BOA_Float32.tif) that are loaded into QGIS with matching layer titles. Median runs also create the original count, standard-deviation and water-occurrence outputs. Existing water options follow the upstream single-scene/composite behavior.

Developed by Mohamed Aly Nasef. License: GPL-3.0-or-later, with third-party notices included in the distribution. Satellite data and access services retain their separate terms. Official stable release; see the bundled guide, references, notices and validation report.'''

SHORT_HELP = '''<h3>Download selected surface-reflectance bands</h3>
<p><b>Sentinel-2A Level-2A</b> (12 bands, no B10) and <b>Landsat 8/9 Collection 2 Level-2</b> (7 SR bands), through Earth Search.</p>
<h4>Run the tool</h4>
<ol><li>Choose a satellite and a <b>polygon ROI OR an extent</b>. With a polygon layer, “Selected features only” uses only the highlighted polygons; otherwise all layer features are used.</li>
<li>Set inclusive UTC dates, maximum <b>scene-wide cloud %</b> and minimum ROI <b>footprint coverage %</b>. These do not guarantee clear pixels in your ROI.</li>
<li>Check the bands, choose single scene or temporal median, and choose an output <b>directory</b>.</li>
<li>Run. <b>Add reflectance to QGIS map after completion</b> is on by default.</li></ol>
<h4>Pixel size and bands</h4>
<p>Defaults: <b>Sentinel-2A 10 m; Landsat 8/9 30 m</b>. Zero means the satellite default. The dialog follows satellite changes until you enter a manual size. Saved/manual overrides are retained. A common 10 m output does not create new detail in native 20/60 m Sentinel bands.</p>
<p>The checklist changes to 12 or 7 bands. In Batch/Modeler, enter comma-separated codes such as <code>B02,B03,B04,B08</code> or <code>SR_B2,SR_B3,SR_B4,SR_B5</code>; blank means all bands for that satellite.</p>
<h4>Scene selection</h4>
<p><b>Single scene:</b> lowest scene-cloud candidate passing the filters. <b>Temporal median:</b> all passing candidates, unless exact scene IDs restrict them. The candidate cap is applied before ROI coverage filtering; this is not an exhaustive best-scene search. Use the Raster menu scene browser to preview and manually select results.</p>
<h4>Outputs and quality</h4>
<p>Outputs are saved with descriptive filenames (e.g. <code>S2A_YYYYMMDD_&lt;SceneID&gt;_BOA_Float32.tif</code>) with <b>-9999 nodata</b> and added to QGIS using their descriptive title. Native product names, metadata and masks are retained. Count/std/occurrence rasters are <b>median outputs</b>, not single-scene outputs. <code>provenance.json</code> marks completion.</p>
<p>Processing produces surface reflectance on the chosen output grid. Single-scene full-ROI output can retain cloudy pixels when water-only is off. Median uses the original QA rules. Water method/strictness/Otsu/manual-threshold controls apply to <b>single scenes</b>; median retains the upstream hybrid/consensus policy. A water-mask file can be saved without applying it to reflectance.</p>
<p>Only selected reflectance bands are read. Include green/NIR or green/SWIR1 when the chosen spectral water method needs them. Search and raster processing run in separate child processes; cancel stops the active worker. Partial run folders are incomplete.</p>
<p><b>Standalone:</b> no other QGIS plugin or cloud account is required. Licensing and third-party notices are included in the distribution. Open the offline help for the full guide, limits, dependencies and citations.</p>'''

PARAMETER_HELP = {
    'SATELLITE':'Sentinel-2A only (not 2B/2C), Landsat-8, Landsat-9, or the combined Landsat 8/9 collection. The band list and automatic pixel size follow the satellite.',
    'ROI':'Use a polygon layer, or only its highlighted features when Selected features only is checked. Leave the extent empty. Without the checkbox, all polygon features are united as the ROI.',
    'EXTENT':'Alternative to a polygon ROI. Use the extent selector for the canvas, a layer or a drawn rectangle. Supply only one ROI source.',
    'START':'Inclusive UTC start date in YYYY-MM-DD format.',
    'END':'Inclusive UTC end date in YYYY-MM-DD format; must not precede the start date.',
    'CLOUD':'Maximum cloud percentage for the entire source scene, not a measured cloud percentage inside your ROI.',
    'COVERAGE':'Minimum percentage of ROI covered by a scene footprint. Coverage does not mean valid, cloud-free or water pixels.',
    'RESOLUTION':'Output pixel size in metres: zero = 10 m for Sentinel-2A or 30 m for Landsat. The dialog follows satellite defaults until a size is manually entered. Native 20/60 m Sentinel bands are resampled to this common grid.',
    'MAX_SCENES':'Maximum candidates returned before ROI coverage filtering (1–200; default 30). Narrow the dates or increase this cap if scenes are missing.',
    'BANDS':'Export only the checked reflectance bands. Batch/Modeler: comma-separated satellite-specific codes; blank = all. If using spectral water methods, include their required green/NIR or green/SWIR1 bands.',
    'MODE':'Single scene chooses the lowest scene-cloud passing candidate. Median combines all passing candidates or the exact scene-ID subset, using the chunked temporal-composite processor.',
    'SCENE_IDS':'Optional exact STAC item IDs, separated by commas. Every ID must occur in the filtered/capped search. Single mode accepts at most one ID.',
    'WATER_ONLY':'Apply the original water-only mask to reflectance. Off retains the original full-ROI processing behavior. A water-mask output may still be created without masking reflectance.',
    'WATER_METHOD':'Single-scene method only: hybrid, classification (SCL for Sentinel / QA for Landsat), NDWI or MNDWI. Median uses the upstream hybrid/permissive calculation and consensus policy.',
    'STRICT':'Single-scene hybrid only: require both the classification water flag and the spectral water test. Off uses the upstream permissive rule.',
    'OTSU':'Single-scene spectral/hybrid water test only: estimate its threshold automatically with Otsu. Off uses the manual threshold.',
    'THRESHOLD':'Single-scene index threshold between -1 and 1, used only when automatic Otsu is off. It does not alter the upstream median water policy.',
    'OUTPUT':'Destination directory, not a raster filename. A unique SpectralHarbor run subfolder keeps the native products and metadata.',
    'LOAD_OUTPUT':'Add the descriptive reflectance raster to the QGIS map and Layers panel after success (default on). No map loading occurs in a headless context without a project.',
}
