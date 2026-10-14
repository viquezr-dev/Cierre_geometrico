# Cierre geométrico

QGIS plugin for projected metric coordinates and measured closed traverses. Produces HTML reports, drawings and downloadable CSV tables.

Author: **Raúl Víquez** · viquezr@gmail.com

[Project page](https://viquezr-dev.github.io/Cierre_geometrico/) · [Repository](https://github.com/viquezr-dev/Cierre_geometrico)

## Installation

The `Cierre_geometrico` folder must contain `__init__.py`, `cierre.py`, `metadata.txt` and `icon.svg`. Install the folder in your QGIS profile's Python plugins directory or use a ZIP containing that folder. Minimum QGIS version: 3.22.

For an existing installation, replace `cierre.py`, update the metadata and restart QGIS. The blue dialog style is embedded in Python; no additional QSS file is required. The initial dialog width remains 570 pixels when the screen permits.

## Input sources and CRS

Every analysis mode accepts a QGIS table or an external CSV. CSV delimiters may be commas, semicolons or tabs. Field names are case insensitive. Header-based columns can appear in any order; additional columns are ignored. Ambiguous aliases are rejected.

Select the matching projected CRS in metres. Any metric projected CRS can be used, including UTM zones outside Panama. The plugin does not reproject attributes, infer the CRS, apply a ground-to-grid factor or correct meridian convergence. Observations and control orientation must already be compatible with the selected grid.

## Coordinate modes

Required fields:

| Field | Alias | QGIS type | Requirement |
| --- | --- | --- | --- |
| PUNTO | EST | Integer | Each identifier 1 through N, exactly once |
| ESTE | X | Real/double | Finite value in every row |
| NORTE | Y | Real/double | Finite value in every row |
| COTA | Z | Integer, real/double | All populated, or all empty with explicit opt-in |
| OBS | — | Text for multiple polygons | Ignored for one polygon; required for multiple polygons |

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

Choose **Cierre observado: ángulos y distancias**. This additional mode analyzes one closed traverse per report and writes a separate `cierre_observado.html` by default. It does not use or change the coordinate modes' reconstruction calculations.

### Observation format

```csv
EST,ANGULO,DISTANCIA,COTA
1,90.0000,45.230,100.000
2,90.0050,38.150,100.250
3,89.9980,52.100,100.100
4,90.0000,48.000,100.050
```

- `EST`: integer 1 through N; `PUNTO` is also accepted.
- `ANGULO`: measured **interior angle at that station**, in decimal degrees, strictly between 0 and 360. Reflex interior angles are permitted.
- `DISTANCIA`: observed **horizontal distance from that station to the next**, in metres and greater than zero. The last row contains the measured N-to-1 side.
- `COTA`: elevation of that station, numeric throughout or entirely empty with explicit opt-in. `Z` is also accepted.

QGIS numeric fields can be integer or real/double; EST must be integer. Without a header, exactly these four columns in this order are required. Additional columns are ignored only when a header identifies the required fields. OBS is not used in this mode.

**90.0050 means 90.005 degrees, not 90°00′50″.** Packed DMS, grads, horizontal circle readings, exterior or zenith angles and inclined instrument distances are not interpreted automatically. Reduce those observations before import. This implementation starts with measured interior angles, rather than importing backsight/foresight circle readings directly.

Supply all N interior angles, including the angle at station 1. Do not repeat station 1 as an additional observation row. Confirm that the last side was actually measured back toward the starting control; a distance inferred from copied closing coordinates is not an independent observation.

### Control and orientation

The **Control y ajuste** tab appears only for this mode. Enter:

1. Known initial northing and easting in metres.
2. Azimuth of side 1–2 in decimal degrees, measured clockwise from grid north, from 0 inclusive to 360 exclusive.
3. Clockwise or counterclockwise traversal order.

Controls are not assumed. Row i supplies the interior angle at station i; the angle at the next station determines the turn from the current side to the next. The fixed initial azimuth determines the first side. The final side is projected from the last station to a calculated arrival **1′**. The unadjusted endpoint is never forced to the initial control.

### Calculation stages

The angular misclosure is `sum(observed interior angles) − (N−2)×180°`, reported with its sign in arcseconds.

The first calculation propagates the observed angles without adjustment. For each side, `ΔN = D cos(A)` and `ΔE = D sin(A)`, without intermediate distance or bearing rounding. The linear closure components are the sums of projections; `e = sqrt(ΔN²+ΔE²)`, `P = sum(observed horizontal distances)`, relative error is `e/P` and linear precision is `1:(P/e)`.

An optional uniform angular correction distributes the opposite angular misclosure equally over all interior angles. Azimuths and projections are recomputed, while the initial azimuth remains fixed. A separate report section shows the resulting linear closure **after angular adjustment and before Bowditch**.

Optional Bowditch corrections are `C_Ni = −ΔN_total×Di/P` and `C_Ei = −ΔE_total×Di/P`. Only adjusted coordinates are balanced back to the control. Original observations and preadjustment indicators are preserved. The adjusted endpoint is set to the control solely to remove machine arithmetic residue.

### Criteria and adjustments

Both adjustments are initially unchecked. No acceptance standard is assigned automatically.

- Maximum angular error: positive arcseconds; blank means not evaluated.
- Minimum linear precision: enter the positive denominator N of 1:N; blank means not evaluated.
- Angular adjustment requires an angular criterion and is applied only when that criterion is met.
- Bowditch requires angular adjustment, a minimum linear precision, an accepted angular closure and an accepted linear closure after the angular adjustment.

If criteria are exceeded, the report and completion dialog warn and preserve the relevant unadjusted results. If criteria are missing, the report does not claim combined compliance. Passing these checks alone does not certify cadastral approval.

The adjusted area is presented only after Bowditch and validation of the resulting polygon. Invalid adjusted geometry receives a warning and no area. An unadjusted arrival gap is not bridged by an artificial side to calculate an area.

The assumed angular weights are equal. This is not a least-squares network adjustment. Initial control coordinates and azimuth are held fixed; their uncertainty is not estimated.

### Elevations and exports

Elevation statistics and calculated inclined lengths are included when all station elevations are available. The final side uses the known starting elevation. This four-column format does not contain an independently observed return elevation, so **vertical closure is not evaluated**. Elevations are not adjusted.

The HTML includes original observations, angular corrections, azimuths, projections, linear corrections, original computed coordinates, coordinates after angular adjustment, and Bowditch coordinates when applicable. Download buttons provide the observations, computed coordinates and exact three-column delivery tables `ESTACION,DISTANCIA,RUMBOS`.

Coordinate CSV exports use integer `EST` row numbers and a separate `ETIQUETA` column to identify 1′ or the adjusted return. The last row in an unadjusted export is a return observation, not another boundary vertex. Adjusted delivery distances and bearings are derived from the adjusted projections and are kept separate from the observed delivery.

## Drawings and areas

All table headers and cells are centred. Areas are shown on two lines: square metres above, whole hectares plus remaining square metres below. For example:

**32,925.393 m²**  
**(3 ha + 2,925.393 m²)**

The observed traverse is drawn as an open polyline ending at 1′; no artificial closing side is drawn. An adjusted valid polygon gets a separate closed drawing and area label. Drawings use equal scale on both axes and start at 100%, with zoom from 25% to 400%. Omitted labels are counted; vertices remain available in the drawing and tables.

## Example report

The included observation example is illustrative. The demonstration HTML uses hypothetical control N=9000 m, E=6000 m, initial azimuth=0° and clockwise order, without adjustment or acceptance criteria. Those controls are not surveyed control supplied by the user.

For the displayed decimal-degree observations, angular misclosure is +10.8″, horizontal perimeter is 183.480 m and unadjusted linear misclosure is approximately 12.006 m. Do not treat this example as an accepted survey.

## Validation and references

```bash
python -m flake8 Cierre_geometrico --max-line-length=120
```

Engine checks cover known clockwise and counterclockwise loops, angular and linear correction gates, malformed observations, CSV and QGIS-like attribute input, separate report stages and original-coordinate regression results. QGIS runtime interaction must be checked on an actual installation.

Method references:

- [FAO: closed traverse surveying and the interior-angle sum](https://www.fao.org/fishery/static/FAO_Training/FAO_Training/General/x6707e/x6707e07.htm).
- [Trimble: traverse calculation and angular adjustment before distance adjustment](https://help.fieldsystems.trimble.com/trimble-access/2022.00/en/Cogo-traverse.htm).
