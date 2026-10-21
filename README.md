# Cierre geométrico

QGIS plugin for projected metric coordinates and measured closed traverses. Produces HTML reports, drawings and downloadable CSV tables.

Author: **Raúl Víquez** · viquezr@gmail.com

[Project page](https://viquezr-dev.github.io/Cierre_geometrico/) · [Repository](https://github.com/viquezr-dev/Cierre_geometrico)

## Installation

The `Cierre_geometrico` folder must contain `__init__.py`, `cierre.py`, `metadata.txt` and `icon.svg`. Install the folder in your QGIS profile's Python plugins directory or use a ZIP containing that folder. Minimum QGIS version: 3.22.

For an existing installation, replace `cierre.py`, update the metadata and restart QGIS. The blue dialog style is embedded in Python; no additional QSS file is required. The initial dialog is 600 × 620 pixels when the screen permits; it adapts to the available screen. Tab titles use 180-pixel widths. In **Datos de entrada**, the input controls and empty-elevation checkbox remain fixed; only the field-help legend scrolls. **Análisis y cierre** contains the calculation parameters.

## Coordinates, bearings and measured angles: different meanings

| Quantity | Meaning | Example | Required reference |
| --- | --- | --- | --- |
| Coordinates | Position of a point, not an observed direction | Northing and easting of point 2 | A coordinate reference system |
| Quadrant bearing (`RUMBO`) | Direction of one side relative to north or south, toward east or west | `N30°00′00″E` for side 1–2 | A common north–south reference |
| Interior angle (`ANGULO`) | Angle inside the polygon between the backsight to the previous vertex and the foresight to the next | `90°00′15″` at vertex 2 | The two sides meeting at that vertex |
| Azimuth | Direction clockwise from north, from 0° inclusive to 360° exclusive | `30°00′00″` for the same NE side | A common north reference |
| Horizontal distance | Horizontal length of a side | `100.000` metres | Start and end of that side |
| Elevation (`COTA`) | Height of a vertex relative to the elevation reference used in the survey | `100.250` metres | A consistent vertical reference |

A bearing contains an angular value, but **a side bearing is not an interior angle**. Rotating the whole polygon changes its bearings while leaving its interior angles unchanged. At vertex 2, the interior angle is between directions **2→1** and **2→3**, not simply the difference between the forward directions 1→2 and 2→3.

A total station's horizontal circle reading is **not automatically a bearing, azimuth or interior angle**. Identify the instrument's orientation, backsight, foresight and angle convention before importing it. This plugin does not reduce raw circle readings into interior angles. Vertical and zenith angles must not be entered in `ANGULO`.

| Select this data type | Supply | What the calculation establishes |
| --- | --- | --- |
| **Coordenadas** | Ordered boundary coordinates | Side distances, bearings, areas and reconstruction rounding residuals |
| **Ángulos y distancias** | Observed interior angles and horizontal side distances | Angular and linear traverse closure, with optional gated adjustments |
| **Rumbos y distancias** | Side bearings and horizontal distances | Linear closure of the supplied directions/distances; no independently observed angular closure |

Copying the starting coordinate to the end closes a drawing; it does not measure survey closure. Likewise, bearings derived from an already adjusted coordinate table or a plan are not new independent field observations. In that case, the bearing calculation checks the consistency of the supplied table.

## Input sources and CRS

The **Tipo de datos** combo has three choices: **Coordenadas**, **Ángulos y distancias** and **Rumbos y distancias**. Choose the origin separately: **Tabla en QGIS** or **Archivo CSV**. Selecting a table or browsing for a CSV activates that origin; the marked origin determines which resource is processed. Every analysis mode accepts either source. CSV delimiters may be commas, semicolons or tabs. Field names are case insensitive. Header-based columns can appear in any order; additional columns are ignored. Ambiguous aliases are rejected.

Select the matching projected CRS in metres. Bearings mode may instead use a local origin: leave both initial northing and easting blank to use N=0, E=0 without georeferencing. Its closure and area do not require an absolute position. If coordinates are supplied, provide both and select their metric projected CRS. Any metric projected CRS can be used, including UTM zones outside Panama. The plugin does not reproject attributes, infer the CRS, apply a ground-to-grid factor or correct meridian convergence. Observations and control orientation must already be compatible with the selected grid.

## Coordinate modes

Required fields:

| Field | Alias | QGIS type | Meaning, units and requirement |
| --- | --- | --- | --- |
| PUNTO | EST | Integer | Vertex identifier; each integer 1 through N, exactly once |
| ESTE | X | Real/double | Easting in metres; finite value in every row |
| NORTE | Y | Real/double | Northing in metres; finite value in every row |
| COTA | Z | Integer, real/double | Vertex elevation in metres; field required, all populated or all empty with explicit opt-in |
| OBS | — | Text for multiple polygons | Group code; ignored for one polygon, required for multiple polygons |

Example with headers (column order may differ when headers identify the fields):

```csv
PUNTO,ESTE,NORTE,COTA
1,625000.000,975000.000,100.000
2,625100.000,975000.000,100.250
3,625100.000,975100.000,100.100
4,625000.000,975100.000,100.050
```

In QGIS, `PUNTO` must be an integer field, not text such as `1-2`. The coordinate fields must be real/double; elevation may also be integer. Do not include radiated detail points among boundary vertices.

Without a CSV header, supply `point,east,north,elevation[,OBS]`. Points are sorted numerically. Partially populated elevations, duplicate or missing point numbers, invalid coordinates, repeated vertices, zero-length sides and self-intersecting contours are rejected.

### One polygon

All records are interpreted as boundary vertices; remove radiated detail points first. OBS does not filter or validate their purpose in this mode. If needed, the first coordinate is copied internally to complete the contour for area calculations.

### Multiple polygons

OBS groups must occupy continuous blocks. Supported codes are `PT`, `PC` and numeric suffixes, with an optional underscore: `PT1`, `PC1`, `PT_2`, `PC_12`. The pattern is `(?:PT|PC)(?:[0-9]+|_[0-9]+)?`. More than two groups are supported.

Codes such as VERT, DET and LOTE_A are rejected. A group cannot reappear after another group, and every polygon needs at least three vertices. Point numbering remains global from 1 to N. Transfer legs between groups are excluded. Each contour has its own CSV and report section; combined area assumes the polygons do not overlap. Inter-group overlap is not checked.

### Reconstruction residuals

The original coordinate-based method is retained: derive distances rounded to 0.001 m and bearings rounded to whole seconds, reconstruct projections and distribute the reconstruction residual proportionally by side length.

These indicators are labelled **rounding residuals**, including when the input already repeats its first coordinate. They do not establish measured traverse closure or survey precision. The adjusted reconstruction area and original-coordinate area remain available, with their signed difference.

## Observed closure from angles and distances

Choose **Ángulos y distancias** in **Tipo de datos**, then select the QGIS or CSV origin. This additional mode analyzes one closed traverse per report and writes a separate `cierre_observado.html` by default. It does not use or change the coordinate modes' reconstruction calculations.

### Observation format

```csv
EST,ANGULO,DISTANCIA,COTA
1,90.0000,45.230,100.000
2,90.0050,38.150,100.250
3,89.9980,52.100,100.100
4,90.0000,48.000,100.050
```

| Field | Accepted aliases | QGIS type | Meaning, units and requirement |
| --- | --- | --- | --- |
| EST | PUNTO | Integer | Station/vertex identifier; unique consecutive integers 1…N |
| ANGULO | ANG, ANG_H, H_ANGLE, ANGULO_HORIZONTAL | Integer/real/double for decimal degrees, or text for DMS | Interior angle at this vertex, strictly between 0° and 360°; required at every station |
| DISTANCIA | DIST, DH, DIST_H, DISTANCIA_HORIZONTAL | Integer/real/double | Positive finite horizontal length in metres, from this station to the next; the last row is N→1 |
| COTA | Z | Integer/real/double | Elevation in metres of this station; optional column, all populated or all empty with explicit opt-in |
| OBS | — | Not used to group this mode | Optional; does not separate traverses |

Reflex interior angles are supported. Headerless observation CSV accepts 3 to 5 columns in this order: `EST,ANGULO,DISTANCIA[,COTA[,OBS]]`. Use headers to specify aliases or additional columns reliably. One closed traverse is analyzed per report.

Accepted angular encodings:

| Input | Interpretation |
| --- | --- |
| `90.0050` | 90.005 decimal degrees = 90°00′18″ |
| `90°00′50″` | Explicit degrees, minutes and seconds = approximately 90.013888889 decimal degrees |
| `90:00:50` | The same explicit DMS angle |

Minutes and seconds must be less than 60. **Packed DMS (`D.MMSS`) is not supported.** Grads, exterior angles, horizontal circle readings, zenith angles and instrument slope distances are not converted automatically. The HTML presents angles and azimuths in **degrees, minutes and seconds**, with four decimal places in arcseconds; calculations retain full precision and the original-observation CSV exports numeric decimal degrees.

Supply all N interior angles, including the angle at station 1. Do not repeat station 1 as an additional observation row. Confirm that the last side was actually measured back toward the starting control; a distance inferred from copied closing coordinates is not an independent observation.

### Control and orientation

In **Análisis y cierre**, the observation-parameter group appears for this mode. Enter:

1. Known initial northing and easting in metres.
2. Azimuth of side 1–2 in decimal degrees or explicit DMS, measured clockwise from grid north, from 0 inclusive to 360 exclusive.
3. Clockwise or counterclockwise traversal order.

Controls are not assumed. Row i supplies the interior angle at station i; the angle at the next station determines the turn from the current side to the next. The fixed initial azimuth determines the first side. The final side is projected from the last station to a calculated arrival **1′**. The unadjusted endpoint is never forced to the initial control.

### Calculation stages

The angular misclosure is `sum(observed interior angles) − (N−2)×180°`, reported with its sign in arcseconds.

The first calculation propagates the observed angles without adjustment. For each side, `ΔN = D cos(A)` and `ΔE = D sin(A)`, without intermediate distance or bearing rounding. The linear closure components are the sums of projections; `e = sqrt(ΔN²+ΔE²)`, `P = sum(observed horizontal distances)`, relative error is `e/P` and linear precision is `1:(P/e)`.

An optional uniform angular correction distributes the opposite angular misclosure equally over all interior angles. Azimuths and projections are recomputed, while the initial azimuth remains fixed. A separate report section shows the resulting linear closure **after angular adjustment and before Bowditch**.

Optional Bowditch corrections are `C_Ni = −ΔN_total×Di/P` and `C_Ei = −ΔE_total×Di/P`. Only adjusted coordinates are balanced back to the control. Original observations and preadjustment indicators are preserved. The adjusted endpoint is set to the control solely to remove machine arithmetic residue.

### Criteria and adjustments

Both adjustments are initially unchecked. No acceptance standard is assigned automatically.

- **Evaluar los criterios configurados (a√n y 1:N)**: enables criterion evaluation. If unchecked, the numerical closures are reported without acceptance evaluation.
- **Coeficiente angular a (″)**: project-defined positive coefficient in arcseconds. The maximum angular error is `a × sqrt(number of interior angles)`. The initial value is 10″; it is not automatically inferred from the instrument specification or a legal standard.
- **Tolerancia lineal**: minimum accepted denominator N in `1:N`; the initial value is 10,000. It is a configurable project criterion, not an assigned cadastral standard.
- **Aplicar ajuste angular uniforme**: selects equal-weight angular balancing and activates criterion evaluation.
- **Aplicar Bowditch después del ajuste angular**: selects linear balancing and activates the required angular adjustment and evaluation.
- **Confirmo el último tramo observado hacia EST 1**: independent confirmation that the return side was actually measured; required to generate this report.
- Angular adjustment requires an angular criterion and is applied only when that criterion is met.
- Bowditch requires angular adjustment, a minimum linear precision, an accepted angular closure and an accepted linear closure after the angular adjustment.

If criteria are exceeded, the report and completion dialog warn and preserve the relevant unadjusted results. If criteria are missing, the report does not claim combined compliance. Passing these checks alone does not certify cadastral approval.

The adjusted area is presented only after Bowditch and validation of the resulting polygon. Invalid adjusted geometry receives a warning and no area. An unadjusted arrival gap is not bridged by an artificial side to calculate an area.

The assumed angular weights are equal. This is not a least-squares network adjustment. Initial control coordinates and azimuth are held fixed; their uncertainty is not estimated.

### Elevations and exports

Elevation statistics and calculated inclined lengths are included when all station elevations are available. The final side uses the known starting elevation. This four-column format does not contain an independently observed return elevation, so **vertical closure is not evaluated**. Elevations are not adjusted.

The HTML includes original observations, angular corrections, azimuths, projections, linear corrections, original computed coordinates, coordinates after angular adjustment, and Bowditch coordinates when applicable. Download buttons provide the observations, computed coordinates and exact three-column delivery tables `ESTACION,DISTANCIA,RUMBOS`.

Coordinate CSV exports use integer `EST` row numbers and a separate `ETIQUETA` column to identify 1′ or the adjusted return. The last row in an unadjusted coordinate export is the calculated arrival at the starting control, not another boundary vertex. Adjusted delivery distances and bearings are derived from the adjusted projections and are kept separate from the observed delivery.

## Closure from bearings and distances

Choose **Rumbos y distancias**, then **Tabla en QGIS** or **Archivo CSV**. The default report filename is `cierre_rumbos_distancias.html`. This mode does not require interior angles, an initial azimuth or a clockwise/counterclockwise selection: each side supplies its own direction.

### Table format

| Field | Accepted aliases | QGIS type | Meaning, units and requirement |
| --- | --- | --- | --- |
| ESTACION | PUNTO, EST | **Text** | Side identifier: `1-2`, `2-3`, …, `N-1`; unique origins 1…N |
| DISTANCIA | DIST, DH | Integer/real/double | Positive finite **horizontal** distance in metres for that side |
| RUMBO | RUMBOS | **Text** | Quadrant direction, for example `N81°34′59″E`; required for every side |
| COTA | Z | Integer/real/double | Elevation in metres of the **origin** of the side; optional column |

The point/station field differs by mode: **integer vertex IDs for coordinates or angles; text side IDs for bearings**. Use a hyphen, not a decimal point: `2-3`, not `2.3`. The sequence must be `1-2 → 2-3 → … → N-1`; `1-2, 2-1, 3-4` is rejected. The plugin sorts by the integer origin station. At least three sides are required, including the measured final return side. Do not append a copied coordinate or an invented closing side.

Bearing CSV requires headers. Example (illustrative four-side loop):

```csv
ESTACION,DISTANCIA,RUMBO
1-2,100.000,N00°00′00″E
2-3,100.000,N90°00′00″E
3-4,100.000,S00°00′00″E
4-1,99.999,N90°00′00″W
```

The quadrant angle must be between 0° and 90° inclusive. The prefix is N or S; the suffix is E, W or O (Spanish oeste). Explicit DMS and decimal-degree quadrant angles are accepted. Bare azimuths such as `270` are not bearings: use a quadrant expression instead. Minutes and seconds must be less than 60. The observation CSV download preserves the supplied bearing text. For ASCII seconds quotes in comma-separated CSV, follow CSV quoting rules: `"N81°34'59""E"`.

### Parameters and criteria

| Parameter/control | Meaning |
| --- | --- |
| Norte inicial, Este inicial | Initial coordinates in metres. Provide both, or leave both blank for a local N=0/E=0 origin. With real coordinates, select the matching projected metric CRS. |
| Precisión mínima | Project minimum denominator N of `1:N`; initial value 10,000, configurable. |
| Evaluar precisión lineal indicada | Evaluate linear closure against the selected minimum; if unchecked, no acceptance is determined. |
| Aplicar Bowditch si cumple la precisión indicada | Optional adjustment; activates criterion evaluation and applies only if that criterion passes. |
| Confirmo que N-1 es el último tramo medido hacia el inicio | Independent user confirmation of the observed return side; required. |
| Permitir COTA completamente vacía | Required when elevation is omitted or empty throughout. Partial elevations are rejected. |

Each bearing is converted to azimuth A and projected using `ΔN = D cos(A)` and `ΔE = D sin(A)`. Their sums give the arrival difference from the starting control. The report includes `P`, closure components, `e`, relative error `e/P` and linear precision `1:(P/e)`. These are computed before adjustment, with no added closing side or intermediate rounding.

Bowditch distributes the opposite closure components proportionally to each side's length. The adjusted endpoint returns to the start; the original closure is preserved. Area is reported only when Bowditch was applied and the adjusted contour is valid. An adjusted algebraic zero is not the observed survey error. No angular adjustment or independently observed angular closure is claimed in this mode.

All bearings must use a consistent north reference. Magnetic or true-north bearings are not automatically converted to grid north. An incorrect direction/reference can still produce misleading results even if the linear closure passes.

### Report and exports

The **summary appears at the beginning**: final adjusted area in m² and hectares plus remaining m², observed perimeter, original linear closure, relative error, precision, criterion status and Bowditch status. If no valid adjustment was made, the summary explains why no final area is available.

The report also includes the original bearings/distances, azimuths in DMS, projections **before Bowditch**, correction columns `C_N` and `C_E`, computed arrival 1′, adjusted coordinates when available, elevations and calculated slope distances, the open unadjusted traverse and the adjusted polygon with its area label. Download buttons provide original observations, computed coordinates and separate observed/adjusted delivery tables with exactly `ESTACION,DISTANCIA,RUMBOS`.

Do not treat a linear closure computed from bearings reconstructed from a plan or previously adjusted coordinates as an independent assessment of field measurement precision.

## Drawings and areas

All table headers and cells are centred. Areas are shown on two lines: square metres above, whole hectares plus remaining square metres below. For example:

**32,925.393 m²**  
**(3 ha + 2,925.393 m²)**

The observed traverse is drawn as an open polyline ending at 1′; no artificial closing side is drawn. An adjusted valid polygon gets a separate closed drawing and area label. Drawings use equal scale on both axes and start at 100%, with zoom from 25% to 400%. Omitted labels are counted; vertices remain available in the drawing and tables.

## Illustrative angle-distance calculation

The following calculation is illustrative. It uses hypothetical control N=9000 m, E=6000 m, initial azimuth=0° and clockwise order, without adjustment or acceptance criteria. Those controls are not surveyed control supplied by the user.

For the displayed decimal-degree observations, angular misclosure is +10.8″, horizontal perimeter is 183.480 m and unadjusted linear misclosure is approximately 12.006 m. Do not treat this example as an accepted survey.

## Validation and references

```bash
python -m flake8 Cierre_geometrico --max-line-length=120
```

Engine checks cover known clockwise and counterclockwise loops, angular and linear correction gates, malformed observations, CSV and QGIS-like attribute input, separate report stages and original-coordinate regression results. QGIS runtime interaction must be checked on an actual installation.

Method references:

- [FAO: closed traverse surveying and the interior-angle sum](https://www.fao.org/fishery/static/FAO_Training/FAO_Training/General/x6707e/x6707e07.htm).
- [Trimble: traverse calculation and angular adjustment before distance adjustment](https://help.fieldsystems.trimble.com/trimble-access/2022.00/en/Cogo-traverse.htm).
