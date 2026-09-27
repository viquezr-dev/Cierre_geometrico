# Cierre geométrico

A QGIS plugin for analyzing ordered UTM coordinates from a QGIS table or a total station CSV. It produces an HTML report with bearings, distances, closure indicators, optional elevations, and an SVG drawing. The report includes a downloadable `ESTACION,DISTANCIA,RUMBOS` CSV table.

Project page: https://viquezr-dev.github.io/Cierre_geometrico/

Repository: https://github.com/viquezr-dev/Cierre_geometrico

## Installation

Place the complete `Cierre_geometrico` folder in your QGIS profile's Python plugins directory, or install a ZIP containing that folder through **Plugins → Manage and Install Plugins → Install from ZIP**. The folder must include `__init__.py`, `cierre.py`, `metadata.txt`, and `icon.svg`.

## Input

- **QGIS table:** Load a delimited text table without geometry. Select the Northing and Easting fields, an optional elevation field, and an optional numeric ordering field. With no ordering field, the table's feature order is used.
- **Total station CSV:** Supply point ID, Easting, Northing, and optional elevation, either with recognized column headers or without headers in that order. Comma, semicolon, and tab separators are accepted. Points are processed in their recorded order.
- Select the UTM CRS that matches the input coordinates. The plugin does not reproject values or automatically determine the UTM zone.
- An optional point sequence can select and reorder the perimeter from a larger dataset.

The plugin checks numeric and finite coordinates, duplicate identifiers in direct CSV input, zero length segments, polygon crossings, and observation code consistency. At least three distinct vertices are required for each polygon.

## Analysis modes

- **Closed polygon:** If the last coordinate does not exactly repeat the first, append the first coordinate internally to complete the polygon. Reject repeated interior vertices, intersecting sides, zero area, and other invalid geometry. Compute bearings, distances, original and adjusted areas, and complementary measures. The displayed Bowditch projections reconstruct distances rounded to 0.001 m and bearings rounded to whole seconds, as in the reference spreadsheet. Their small residual reflects this reconstruction and must not be interpreted as an observed survey closure.
- **Multiple polygons by PT/PC group:** The native, headerless station export contains `point number, Easting, Northing, elevation, group`, for example `1,5000.123,2000.456,100.789,PT`. Point numbers provide the measurement order and the final code identifies the polygon. Use this CSV directly, or load a QGIS table with `PUNTO` (numeric point number) and `OBS` (PT/PC group). The earlier layout with `PUNTO` values such as `PT-1` and `PC-1` and a numeric `OBS` order is also supported. Each group must occupy one continuous block; a repeated group or a foreign code such as `VERT` or `DET` stops the calculation and names the offending point. The plugin analyzes each polygon separately in one HTML report, excluding travel legs between groups. Each polygon gets its own drawing and three-column CSV download. The last-to-first side must be an actual property boundary; its length is not an observed closure error. The displayed sum of areas assumes the polygons do not overlap.

The single polygon mode requires a consistent observation code throughout the selected points. If, for example, a `VERT` block contains a `DET` point, processing stops and identifies that point. Edit or prepare the input before retrying; the plugin does not silently discard a radiated point or turn it into another polygon.

The HTML report contains the calculation tables, a point drawing, and a CSV download button. Distances and areas refer to the UTM grid; the computed slope distance uses coordinate elevations and is not an instrument observation.

## Code style

Run `flake8` from this plugin folder. The included `.flake8` sets a 120 character line limit for the embedded HTML templates.

## Author

Raúl Víquez

Contact: viquezr@gmail.com
