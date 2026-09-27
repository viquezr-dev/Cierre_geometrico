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

The plugin checks numeric and finite coordinates, duplicate identifiers in direct CSV input, zero length segments, and geometric crossings where relevant. At least two points are needed for a route and at least three distinct vertices for a polygon.

## Analysis modes

- **Automatic:** Use a polygon report when the ordered points form a valid polygon. If the perimeter crosses itself, use a route report instead.
- **Survey route (1 to N):** Keep the observed route open. Report total horizontal distance, the northing and easting differences between its last and first points, and their separation. When the last point is a repeat observation of the first control point, compare the observed closure against the configurable maximum error and minimum precision; the report states whether it meets both limits. Elevations add level differences and computed slope distances. This mode does not invent a closing leg or apply coordinate adjustment.
- **Closed polygon:** If the last coordinate does not exactly repeat the first, append the first coordinate internally to complete the polygon. Reject repeated interior vertices, intersecting sides, zero area, and other invalid geometry. Compute bearings, distances, original and adjusted areas, and complementary measures. The displayed Bowditch projections reconstruct distances rounded to 0.001 m and bearings rounded to whole seconds, as in the reference spreadsheet. Their small residual reflects this reconstruction and must not be interpreted as an observed survey closure.

The HTML report contains the calculation tables, a point drawing, and a CSV download button. Distances and areas refer to the UTM grid; the computed slope distance uses coordinate elevations and is not an instrument observation.

## Code style

Run `flake8` from this plugin folder. The included `.flake8` sets a 120 character line limit for the embedded HTML templates.

## Author

Raúl Víquez

Contact: viquezr@gmail.com
