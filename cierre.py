"""QGIS plugin: geometric traverse from an ordered UTM coordinate table."""

import html
import math
import os
import webbrowser
import csv
import re
import base64
import io

from qgis.PyQt.QtGui import QGuiApplication, QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QMessageBox, QPushButton,
    QHBoxLayout, QScrollArea,
    QWidget, QVBoxLayout, QTabWidget,
)
from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer


def read_station_csv(path):
    """Read P,E,N,Z total station CSV (headerless or named columns)."""
    with open(path, "r", encoding="utf-8-sig", newline="") as source:
        sample = source.read(4096)
        source.seek(0)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
        rows = [(line, [cell.strip() for cell in row])
                for line, row in enumerate(csv.reader(source, delimiter=delimiter), 1)
                if row and any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("El CSV está vacío.")
    first = rows[0][1]
    if len(first) < 3:
        raise ValueError("Se esperan columnas: punto, Este, Norte y cota opcional.")
    header = False
    try:
        float(first[1])
        float(first[2])
    except ValueError:
        header = True
    if header:
        fields = [re.sub(r"[^A-Z0-9]", "", field.upper()) for field in first]

        def field_index(candidates):
            for index, name in enumerate(fields):
                if name in candidates:
                    return index
            return None
        e_index = field_index({"ESTE", "UTMESTE", "EAST", "EASTING", "X"})
        n_index = field_index({"NORTE", "UTMNORTE", "NORTH", "NORTHING", "Y"})
        id_index = field_index({"PUNTO", "PTO", "ID", "POINT", "NUMERO", "N"})
        z_index = field_index({"COTA", "ELEVACION", "ALTURA", "Z", "HEIGHT"})
        if e_index is None or n_index is None:
            raise ValueError("El encabezado requiere campos Este y Norte identificables.")
        rows = rows[1:]
    else:
        id_index, e_index, n_index = 0, 1, 2
        z_index = 3 if len(first) >= 4 else None
    records = []
    for line, cells in rows:
        if max(e_index, n_index, id_index or 0) >= len(cells):
            raise ValueError("Fila {}: faltan columnas.".format(line))
        point_id = (cells[id_index] if id_index is not None else str(line))
        if not point_id:
            raise ValueError("Fila {}: falta el identificador del punto.".format(line))
        try:
            e, n = float(cells[e_index]), float(cells[n_index])
        except ValueError:
            raise ValueError("Fila {}: Este o Norte no es numérico.".format(line))
        if not (math.isfinite(e) and math.isfinite(n)):
            raise ValueError("Fila {}: coordenada no finita.".format(line))
        if z_index is not None:
            if z_index >= len(cells) or not cells[z_index]:
                raise ValueError("Fila {}: falta la cota.".format(line))
            try:
                z = float(cells[z_index])
            except ValueError:
                raise ValueError("Fila {}: cota no numérica.".format(line))
            if not math.isfinite(z):
                raise ValueError("Fila {}: cota no finita.".format(line))
        else:
            z = None
        records.append((point_id, n, e, z))
    ids = [r[0] for r in records]
    if len(set(ids)) != len(ids):
        closure_id_repeated = (len(records) >= 4 and ids[-1] == ids[0]
                               and records[-1][1:3] == records[0][1:3]
                               and len(set(ids[:-1])) == len(ids) - 1)
        if not closure_id_repeated:
            raise ValueError("Hay identificadores de punto repetidos en el CSV.")
    return records


def read_station_observations(path, allow_missing=False):
    """Read point order and observation codes from native or labeled CSV."""
    records = read_station_csv(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as source:
        sample = source.read(4096)
        source.seek(0)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
        rows = [(line, [cell.strip() for cell in row])
                for line, row in enumerate(csv.reader(source, delimiter=delimiter), 1)
                if row and any(cell.strip() for cell in row)]
    columns = [re.sub(r"[^A-Z0-9]", "", value.upper()) for value in rows[0][1]]
    labeled = "OBS" in columns and "PUNTO" in columns
    data_rows = rows[1:] if labeled else rows
    if not labeled and (len(rows[0][1]) < 5 or not rows[0][1][0].isdigit()):
        if allow_missing and len(rows[0][1]) < 5:
            return []
        raise ValueError(
            "Varios polígonos requiere PUNTO y OBS en el encabezado, o "
            "cinco columnas sin encabezado: punto, Este, Norte, Cota, grupo (PT/PC).")
    if len(data_rows) != len(records):
        raise ValueError("No coinciden las filas del grupo con las coordenadas.")
    obs_index = columns.index("OBS") if labeled else None
    observations = []
    for (line, cells), record in zip(data_rows, records):
        if labeled:
            raw_order = cells[obs_index] if obs_index < len(cells) else None
            group_name = None
        else:
            raw_order = record[0]
            group_name = cells[4] if len(cells) > 4 else None
        observations.append((raw_order, record[0], record[1],
                             record[2], record[3], "Fila {}".format(line),
                             group_name))
    return observations


def normalize_station_observations(observations):
    """Validate (order/code, point, N, E, Z, location[, code]) records."""
    ordered = []
    used_orders = set()
    used_ids = set()
    for observation in observations:
        raw_order, point_id, north, east, elevation, location = observation[:6]
        group_name = observation[6] if len(observation) > 6 else None
        point_id = str(point_id).strip()
        if group_name is None:
            try:
                float(raw_order)
            except (TypeError, ValueError):
                try:
                    float(point_id)
                except ValueError:
                    pass
                else:
                    group_name, raw_order = raw_order, point_id
        try:
            order = float(raw_order)
        except (TypeError, ValueError):
            raise ValueError("{}: el orden debe ser un número entero.".format(location))
        if not math.isfinite(order) or order < 1 or not order.is_integer():
            raise ValueError("{}: el orden debe ser un entero positivo.".format(location))
        order = int(order)
        if order in used_orders:
            raise ValueError("{}: el número de orden {} está duplicado.".format(location, order))
        used_orders.add(order)
        if group_name is None:
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)-(\d+)", point_id)
            if match is None:
                raise ValueError("{}: PUNTO debe tener formato PT-1 o PC-1.".format(location))
            name = match.group(1).upper()
        else:
            name = str(group_name).strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                raise ValueError("{}: el grupo debe ser PT, PC u otra etiqueta válida.".format(location))
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)-(\d+)", point_id)
            if match and match.group(1).upper() != name:
                raise ValueError("{}: PUNTO y grupo tienen prefijos diferentes.".format(location))
        if point_id.upper() in used_ids:
            raise ValueError("{}: PUNTO {} está duplicado.".format(location, point_id))
        used_ids.add(point_id.upper())
        try:
            n, e = float(north), float(east)
            z = None if elevation is None else float(elevation)
        except (TypeError, ValueError):
            raise ValueError("{}: Norte, Este o Cota no es numérico.".format(location))
        if not all(math.isfinite(v) for v in (n, e) + (() if z is None else (z,))):
            raise ValueError("{}: coordenada o cota no finita.".format(location))
        ordered.append((order, name, (point_id, n, e, z)))
    return sorted(ordered)


def read_station_groups(path):
    """Group the complete polygon blocks of a station CSV."""
    return group_station_observations(read_station_observations(path))


def group_station_observations(observations):
    """Keep whole polygon groups separate in the observed point sequence."""
    ordered = normalize_station_observations(observations)
    for _, name, record in ordered:
        if name not in ("PT", "PC"):
            raise ValueError(
                "Punto {}: OBS '{}' no coincide con el patrón PT/PC. "
                "Edite la observación antes de calcular.".format(record[0], name))
    groups = {}
    completed = set()
    previous = None
    for _, name, record in sorted(ordered):
        if name != previous:
            if name in completed:
                raise ValueError(
                    "Punto {}: OBS '{}' reaparece fuera de su bloque PT/PC. "
                    "Revise la secuencia antes de calcular.".format(record[0], name))
            if previous is not None:
                completed.add(previous)
            previous = name
        groups.setdefault(name, []).append(record)
    if len(groups) < 2:
        raise ValueError("Se requieren al menos dos grupos de polígonos para este modo.")
    for name, group in groups.items():
        if len(group) < 3:
            raise ValueError("El polígono {} tiene menos de tres puntos.".format(name))
    return groups


def validate_code_interpretation(observations):
    """Require one observation pattern for a single polygon."""
    if not observations:
        return
    ordered = normalize_station_observations(observations)
    first_code, first_point = ordered[0][1], ordered[0][2][0]
    for _, name, record in ordered[1:]:
        if name != first_code:
            raise ValueError(
                "Punto {}: OBS '{}' no coincide con '{}' del punto {}. "
                "Edite la secuencia o las observaciones antes de calcular.".format(
                    record[0], name, first_code, first_point))


def choose_points(records, selection):
    """Keep the supplied perimeter order, optionally selecting IDs and ranges."""
    if not selection.strip():
        return list(records)
    by_id = {str(row[0]): row for row in records}
    result = []
    for item in selection.split(","):
        item = item.strip()
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", item)
        if match:
            first, last = map(int, match.groups())
            if abs(last - first) > len(records):
                raise ValueError("Rango demasiado largo: {}.".format(item))
            ids = [str(i) for i in range(first, last + (1 if last >= first else -1),
                                         1 if last >= first else -1)]
        else:
            ids = [item]
        for point_id in ids:
            if point_id not in by_id:
                raise ValueError("No existe el punto {} en el CSV.".format(point_id))
            result.append(by_id[point_id])
    if len({r[0] for r in result}) != len(result):
        raise ValueError("La secuencia incluye puntos duplicados. El cierre se añade por separado.")
    return result


def prepare_perimeter(records, add_closing_segment):
    """Return closed records and the observed last-to-first gap."""
    if len(records) < 3:
        raise ValueError("Se necesitan al menos tres puntos del perímetro.")
    gap = math.hypot(float(records[-1][1]) - float(records[0][1]),
                     float(records[-1][2]) - float(records[0][2]))
    if gap > 1e-8 and add_closing_segment:
        return list(records) + [records[0]], gap
    return list(records), gap


def analyze_perimeter(records):
    """Measure preclosure components and optional elevations for a polygon."""
    if len(records) < 2:
        raise ValueError("Se necesitan al menos dos puntos del contorno.")
    points = []
    for index, row in enumerate(records, 1):
        try:
            n, e = float(row[1]), float(row[2])
            z = None if len(row) < 4 or row[3] is None else float(row[3])
        except (TypeError, ValueError):
            raise ValueError("Punto {}: coordenada o cota no numérica.".format(index))
        if not all(math.isfinite(value) for value in (n, e) + (() if z is None else (z,))):
            raise ValueError("Punto {}: valor no finito.".format(index))
        points.append((str(row[0]), n, e, z))
    distances = []
    slope_distances = []
    for a, b in zip(points, points[1:]):
        horizontal = math.hypot(b[1] - a[1], b[2] - a[2])
        if horizontal <= 1e-10:
            raise ValueError("Tramo {}-{}: distancia horizontal cero.".format(a[0], b[0]))
        distances.append(horizontal)
        if a[3] is not None and b[3] is not None:
            slope_distances.append(math.hypot(horizontal, b[3] - a[3]))
    dn = points[-1][1] - points[0][1]
    de = points[-1][2] - points[0][2]
    elevations = [point[3] for point in points if point[3] is not None]
    return {
        "horizontal": math.fsum(distances),
        "slope": math.fsum(slope_distances) if len(slope_distances) == len(distances) else None,
        "gap": math.hypot(dn, de), "closure_dn": dn, "closure_de": de,
        "net_dz": points[-1][3] - points[0][3]
        if points[0][3] is not None and points[-1][3] is not None else None,
        "min_z": min(elevations, default=None),
        "max_z": max(elevations, default=None),
    }


def _bearing(dn, de):
    ns = "N" if dn >= 0 else "S"
    ew = "E" if de >= 0 else "W"
    seconds = round(math.degrees(math.atan2(abs(de), abs(dn))) * 3600)
    degrees, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return "{}{:02d}°{:02d}'{:02d}\"{}".format(
        ns, degrees, minutes, seconds, ew)


def _orientation(a, b, c):
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _on_segment(a, b, p, tolerance):
    return (abs(_orientation(a, b, p)) <= tolerance
            and min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9
            and min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9)


def _intersects(a, b, c, d):
    scale = max(1.0, *(abs(v) for p in (a, b, c, d) for v in p))
    tolerance = 1e-12 * scale * scale
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if ((o1 > tolerance and o2 < -tolerance)
            or (o1 < -tolerance and o2 > tolerance)) and (
            (o3 > tolerance and o4 < -tolerance)
            or (o3 < -tolerance and o4 > tolerance)):
        return True
    return any((_on_segment(a, b, c, tolerance),
                _on_segment(a, b, d, tolerance),
                _on_segment(c, d, a, tolerance),
                _on_segment(c, d, b, tolerance)))


def calculate(records):
    """Return ordered segments and geometry; optional fourth field is cota."""
    if len(records) < 4:
        raise ValueError("Se requieren tres vértices y una fila final de cierre.")
    points = []
    for row_number, record in enumerate(records, 1):
        try:
            n, e = float(record[1]), float(record[2])
        except (IndexError, TypeError, ValueError):
            raise ValueError("Fila {}: Norte o Este no es numérico.".format(row_number))
        if not (math.isfinite(n) and math.isfinite(e)):
            raise ValueError("Fila {}: coordenada no finita.".format(row_number))
        points.append((e, n))
    # Excel puede dejar un residuo binario diminuto al sumar proyecciones
    # corregidas (p. ej. 9000.000000000002 en vez de 9000).
    if math.hypot(points[0][0] - points[-1][0],
                  points[0][1] - points[-1][1]) > 1e-8:
        raise ValueError("El último registro debe repetir Norte y Este del primero (tolerancia: 1e-8 m).")
    points[-1] = points[0]
    if len(set(points[:-1])) != len(points) - 1:
        raise ValueError("Hay vértices repetidos antes de la fila final de cierre.")
    count = len(points) - 1
    for i in range(count):
        a, b = points[i], points[i + 1]
        if a == b:
            raise ValueError("El tramo {} tiene longitud cero.".format(i + 1))
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue
            if _intersects(a, b, points[j], points[j + 1]):
                raise ValueError("Los tramos {} y {} se cruzan o se tocan.".format(i + 1, j + 1))
    origin_x, origin_y = points[0]
    area2 = math.fsum(
        (points[i][0] - origin_x) * (points[i + 1][1] - origin_y)
        - (points[i + 1][0] - origin_x) * (points[i][1] - origin_y)
        for i in range(count))
    if abs(area2) < 1e-10:
        raise ValueError("El área del polígono es nula.")
    segments = []
    for i in range(count):
        de = points[i + 1][0] - points[i][0]
        dn = points[i + 1][1] - points[i][1]
        end_label = records[0][0] if i == count - 1 else records[i + 1][0]
        segments.append((str(records[i][0]) + "-" + str(end_label),
                         records[i][0], end_label,
                         points[i][1], points[i][0], points[i + 1][1],
                         points[i + 1][0], dn, de, math.hypot(dn, de),
                         _bearing(dn, de)))
    # El libro convierte cada tramo UTM en distancia a 0.001 m y rumbo a
    # segundos enteros. Reconstruimos esas proyecciones, tal como las usa
    # la rutina de cierre de la hoja DATOS.
    projections = []
    for segment in segments:
        dn, de = segment[7], segment[8]
        distance = round(segment[9], 3)
        angle_seconds = round(math.degrees(math.atan2(abs(de), abs(dn))) * 3600)
        angle = math.radians(angle_seconds / 3600)
        projected_n = math.copysign(distance * math.cos(angle), dn) if dn else 0.0
        projected_e = math.copysign(distance * math.sin(angle), de) if de else 0.0
        projections.append((distance, projected_n, projected_e))
    perimeter = math.fsum(item[0] for item in projections)
    st = math.fsum(item[1] for item in projections)
    sg = math.fsum(item[2] for item in projections)
    closure_error = math.hypot(st, sg)
    corrected = []
    for distance, north, east in projections:
        corrected.append((north - st * distance / perimeter,
                          east - sg * distance / perimeter))
    corrected_st = math.fsum(item[0] for item in corrected)
    corrected_sg = math.fsum(item[1] for item in corrected)
    corrected_points = [points[0]]
    for north, east in corrected:
        prior_e, prior_n = corrected_points[-1]
        corrected_points.append((prior_e + east, prior_n + north))
    corrected_area2 = math.fsum(
        (corrected_points[i][0] - origin_x) *
        (corrected_points[i + 1][1] - origin_y)
        - (corrected_points[i + 1][0] - origin_x) *
        (corrected_points[i][1] - origin_y)
        for i in range(count))
    summary = {
        "st": st, "sg": sg, "corrected_st": corrected_st,
        "corrected_sg": corrected_sg, "closure_error": closure_error,
        "relative_error": closure_error / perimeter,
        "precision": perimeter / closure_error if closure_error else math.inf,
        "corrected_area": abs(corrected_area2) / 2,
        "raw_area": abs(area2) / 2,
    }
    return segments, perimeter, summary


def _polygon_svg(segments, area):
    """Plano independiente, escalado en proporción a metros UTM."""
    points = [(segment[4], segment[3]) for segment in segments]
    point_ids = [str(i) for i in range(1, len(points) + 1)]
    east = [p[0] for p in points]
    north = [p[1] for p in points]
    min_e, max_e = min(east), max(east)
    min_n, max_n = min(north), max(north)
    width, height = 900, 560
    left, top, right, bottom = 76, 50, 55, 68
    span_e = max_e - min_e
    span_n = max_n - min_n
    scale = min((width - left - right) / span_e,
                (height - top - bottom) / span_n)
    drawn_w, drawn_h = span_e * scale, span_n * scale
    offset_x = left + ((width - left - right) - drawn_w) / 2

    def xy(point):
        return (offset_x + (point[0] - min_e) * scale,
                height - bottom - ((point[1] - min_n) * scale) -
                ((height - top - bottom) - drawn_h) / 2)

    coords = [xy(point) for point in points]
    path = " ".join("{:.2f},{:.2f}".format(*point) for point in coords)
    circles = []
    labels = []
    occupied = []
    for index, ((x, y), (e, n)) in enumerate(zip(coords, points), 1):
        point_id = str(point_ids[index - 1])
        title = html.escape("Punto {} · E {:.3f} m · N {:.3f} m".format(
            point_id, e, n), quote=True)
        circles.append(
            '<g class="vertex"><circle cx="{:.2f}" cy="{:.2f}" r="5">'
            '<title>{}</title></circle></g>'.format(x, y, title))
        label_width = 9 + 8 * len(point_id)
        # Buscar una posición legible sin tapar otros números ni vértices.
        for dx, dy in ((10, -10), (10, 17), (-10 - label_width, -10),
                       (-10 - label_width, 17), (13, 4),
                       (-13 - label_width, 4)):
            lx, ly = x + dx, y + dy
            box = (lx - 2, ly - 13, lx + label_width, ly + 3)
            if (box[0] < left or box[2] > width - right
                    or box[1] < top or box[3] > height - bottom):
                continue
            if any(box[0] < b[2] and box[2] > b[0]
                   and box[1] < b[3] and box[3] > b[1] for b in occupied):
                continue
            if any(box[0] - 2 < px < box[2] + 2
                   and box[1] - 2 < py < box[3] + 2
                   for point_index, (px, py) in enumerate(coords, 1)
                   if point_index != index):
                continue
            labels.append('<text class="point-label" x="{:.2f}" y="{:.2f}">{}</text>'.format(
                lx, ly, html.escape(point_id)))
            occupied.append(box)
            break
    ticks = []
    for fraction in (0, .25, .5, .75, 1):
        x = xy((min_e + span_e * fraction, min_n))[0]
        y = xy((min_e, min_n + span_n * fraction))[1]
        ticks.append('<line x1="{0:.2f}" x2="{0:.2f}" y1="{1}" y2="{2}" '
                     'class="grid"/>'.format(x, top, height - bottom))
        ticks.append('<line x1="{0}" x2="{1}" y1="{2:.2f}" y2="{2:.2f}" '
                     'class="grid"/>'.format(left, width - right, y))
        ticks.append('<text class="tick" x="{:.2f}" y="{}" '
                     'text-anchor="middle">{:.1f}</text>'.format(
                         x, height - bottom + 21, min_e + span_e * fraction))
        ticks.append('<text class="tick" x="{}" y="{:.2f}" '
                     'text-anchor="end">{:.1f}</text>'.format(
                         left - 9, y + 4, min_n + span_n * fraction))
    shape = ('<polygon points="{}" fill="#32b7ad" fill-opacity=".18" '
             'stroke="#087f83" stroke-width="3" stroke-linejoin="round"/>'.format(path))
    area_label = ('<g class="area-label"><rect x="330" y="10" width="240" '
                  'height="28" rx="7"/><text x="450" y="29" '
                  'text-anchor="middle">Área: {:,.3f} m²</text></g>'.format(area))
    return ('<svg viewBox="0 0 900 560" role="img" '
            'aria-label="Plano del polígono con puntos numerados y ejes UTM">'
            '<rect x="0" y="0" width="900" height="560" fill="#f8fbfd"/>'
            '<g>{}</g>{}{}{}{}'
            '<text x="450" y="547" text-anchor="middle" class="axis">Este (m)</text>'
            '<text transform="translate(17 270) rotate(-90)" text-anchor="middle" '
            'class="axis">Norte (m)</text>'
            '<text x="834" y="40" class="axis" text-anchor="middle">N ↑</text>'
            '</svg>').format("".join(ticks), shape, "".join(circles),
                             "".join(labels), area_label)


def _delivery_table(segments, filename="estaciones_distancias_rumbos.csv"):
    """Provide an exact three-column CSV and its matching HTML table."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(("ESTACION", "DISTANCIA", "RUMBOS"))
    rows = []
    for segment in segments:
        station = segment[0]
        distance = segment[9]
        bearing = segment[10]
        distance_text = "{:.3f}".format(distance)
        writer.writerow((station, distance_text, bearing))
        rows.append('<tr><td>{}</td><td class="num">{}</td><td>{}</td></tr>'.format(
            html.escape(station), distance_text, html.escape(bearing)))
    data = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
    uri = "data:text/csv;charset=utf-8;base64," + base64.b64encode(data).decode("ascii")
    return ('<section><h2>Tabla para entrega</h2>'
            '<p><a class="download" download="{}" '
            'href="{}">Descargar CSV</a></p>'
            '<div style="overflow-x:auto"><table><thead><tr><th>ESTACION</th>'
            '<th>DISTANCIA</th><th>RUMBOS</th></tr></thead><tbody>{}</tbody></table></div>'
            '<small>Distancias horizontales en metros a tres decimales; rumbos al segundo. '
            'Al abrir el CSV en Excel, importe ESTACION como texto para evitar '
            'conversiones automáticas a fecha.</small></section>').format(
                html.escape(filename, quote=True), uri, "".join(rows))


def build_html(segments, perimeter, summary, layer, crs, author="",
               closing_gap=0.0, added_close=False,
               pre_summary=None, full_summary=None, delivery_filename=None):
    def esc(value):
        return html.escape(str(value), quote=True)

    def fmt(value):
        return "{:,.3f}".format(value)
    rows = []
    for s in segments:
        rows.append("<tr><td>{}</td><td>{}</td><td>{}</td>".format(
            esc(s[0]), esc(s[1]), esc(s[2]))
            + "".join("<td class='num'>{}</td>".format(fmt(v)) for v in s[3:10])
            + "<td>{}</td></tr>".format(esc(s[10])))

    def fmt_small(value):
        return "{:.13f}".format(value)
    precision = fmt(summary["precision"]) if math.isfinite(summary["precision"]) else "∞"
    shortest = min(segments, key=lambda segment: segment[9])
    longest = max(segments, key=lambda segment: segment[9])
    area_difference = summary["corrected_area"] - summary["raw_area"]
    area_difference_text = ("0.000" if abs(area_difference) < 0.0005
                            else "{:+,.3f}".format(area_difference))
    author_html = "<span>Autor: {}</span>".format(esc(author.strip())) if author.strip() else ""
    chart = _polygon_svg(segments, summary["corrected_area"])
    delivery_table = _delivery_table(
        segments, filename=delivery_filename or "estaciones_distancias_rumbos.csv")
    close_notice = (
        '<div class="notice">Se repitió internamente la primera coordenada para '
        'formar el lado final {} de {:.3f} m. Este lado permite calcular el área; '
        'su longitud no es un error topográfico de cierre. La precisión de la tabla '
        'de proyecciones procede del redondeo de rumbos y distancias.</div>'.format(
            esc(segments[-1][0]), closing_gap) if added_close else "")
    pre_section = ""
    if pre_summary is not None:
        pre_section = (
            '<section><h2>Componentes del cierre en planta</h2>'
            '<p>ΔN (último registrado − primero): <b>{dn:+,.3f} m</b> · '
            'ΔE (último registrado − primero): <b>{de:+,.3f} m</b>.</p>'
            '<p>Distancia horizontal acumulada P = <b>{p:,.3f} m</b>; '
            'separación e = √(ΔN² + ΔE²) = <b>{gap:,.3f} m</b>.</p>'
            '<small>Estos componentes describen los extremos tal como aparecen '
            'en el archivo, antes de añadir la primera coordenada. Cuando el '
            'último registro es otro vértice del contorno, e representa el lado '
            'faltante, no el error de cierre de una observación repetida. '
            'Solo si el último registro reobserva el control inicial, la precisión '
            'topográfica se expresa como 1:(P/e).</small>'
            '</section>').format(dn=pre_summary["closure_dn"],
                                 de=pre_summary["closure_de"],
                                 p=pre_summary["horizontal"],
                                 gap=pre_summary["gap"])
    vertical_section = ""
    slope_card = ""
    if full_summary is not None and full_summary["slope"] is not None:
        slope_card = ('<div class="card">Distancia inclinada calculada: '
                      '<b>{:,.3f} m</b></div>').format(full_summary["slope"])
        vertical_section = (
            '<section><h2>Desniveles y cotas</h2><p>Cota mínima: '
            '<b>{min_z:,.3f} m</b> · Cota máxima: <b>{max_z:,.3f} m</b> · '
            'Desnivel del primer al último punto registrado: '
            '<b>{dz:+,.3f} m</b>.</p><small>Distancia inclinada por tramo '
            '= √(distancia horizontal² + desnivel²). Se calcula desde las cotas '
            'del archivo; no sustituye una distancia inclinada observada '
            'directamente por el instrumento.</small></section>').format(
                min_z=full_summary["min_z"], max_z=full_summary["max_z"],
                dz=pre_summary["net_dz"])
    return """<!doctype html><html lang="es"><meta charset="utf-8">
<title>Cierre geométrico</title><style>
body{{font:15px system-ui,Arial;margin:0;background:#f2f6f9;color:#183348}}
main{{max-width:1180px;margin:2rem auto;padding:0 1.25rem 2rem}}
header{{background:#163b57;color:white;padding:1.5rem 2rem;border-radius:12px}}
h1{{margin:.2rem 0;font-size:1.7rem}}h2{{margin:0 0 .9rem;color:#163b57}}
.meta{{display:flex;gap:1.5rem;flex-wrap:wrap;opacity:.9}}
.summary{{display:flex;gap:1rem;flex-wrap:wrap;margin:1.2rem 0}}
.card{{background:white;border-left:4px solid #19a99d;padding:1rem 1.3rem;
 border-radius:8px;box-shadow:0 2px 10px #163b5712;flex:1;min-width:170px}}
.card b{{display:block;margin-top:.4rem;font-size:1.2rem;color:#087f83}}
.notice{{background:#fff6e3;border-left:4px solid #ce8f23;padding:1rem 1.2rem;
 margin:1rem 0;border-radius:5px;line-height:1.5}}
section{{background:white;padding:1.35rem;margin-top:1.2rem;border-radius:10px;
 box-shadow:0 2px 10px #163b5712}}.chart{{overflow:auto;text-align:center}}
svg{{width:100%;max-width:900px;height:auto;border:1px solid #dce8ed;border-radius:8px}}
svg .grid{{stroke:#dfeaf0;stroke-width:1}}svg .axis{{fill:#31566b;font:bold 14px system-ui}}
svg .tick{{fill:#567186;font:11px system-ui}}svg .vertex circle{{fill:#0b6675;stroke:white;stroke-width:1.5}}
svg .point-label{{fill:#173c54;font:bold 12px system-ui;paint-order:stroke;
 stroke:white;stroke-width:3px;stroke-linejoin:round}}
svg .area-label rect{{fill:#e9f5f3;stroke:#087f83;stroke-width:1}}
svg .area-label{{pointer-events:none}}
svg .area-label text{{fill:#163b57;font:bold 13px system-ui}}
table{{border-collapse:collapse;width:100%;margin-top:.7rem}}
th,td{{border:1px solid #ccd9df;padding:.55rem;text-align:center;white-space:nowrap}}
th{{background:#163b57;color:white;text-align:center;vertical-align:middle}}
.num{{text-align:center;font-variant-numeric:tabular-nums}}
tr:nth-child(even){{background:#f5f8fa}}small{{display:block;margin-top:1rem;line-height:1.5}}
.download{{display:inline-block;background:#087f83;color:white;text-decoration:none;
 padding:.7rem 1.1rem;border-radius:6px;font-weight:bold}}
.download:hover{{background:#096b73}}
@media print{{body{{background:white}}main{{margin:0;max-width:none}}
 section,.card{{box-shadow:none;border:1px solid #dce8ed}}}}
</style><main><header><h1>Cierre geométrico de polígono</h1>
<div class="meta"><span>Tabla: {layer}</span><span>CRS: {crs}</span>{author}</div></header><div class="summary">
<div class="card">Tramos: <b>{count}</b></div>
<div class="card">Perímetro: <b>{perimeter} m</b></div>
<div class="card">Área por coordenadas corregidas: <b>{area} m²</b> ({hectares} ha)</div>
{slope_card}</div>
{close_notice}
{pre_section}
{vertical_section}
<section><h2>Proyecciones y cierre</h2><div style="overflow-x:auto"><table><thead>
<tr><th>Indicador</th><th>Resultado</th></tr></thead><tbody>
<tr><td>St (N-S)</td><td class="num">{st} m</td></tr>
<tr><td>Sg (E-W)</td><td class="num">{sg} m</td></tr>
<tr><td>Error relativo</td><td class="num">{relative_error}</td></tr>
<tr><td>Error de cierre</td><td class="num">{closure_error} m</td></tr>
<tr><td>Precisión</td><td class="num">1/{precision}</td></tr>
<tr><td>Área por coordenadas corregidas</td><td class="num">{area} m²</td></tr>
</tbody></table></div><small>La corrección proporcional por distancia hace que las sumas de
proyecciones corregidas St y Sg sean cero, salvo residuos numéricos; por eso no se
repiten como indicadores. Este residuo es efecto de redondear datos derivados de
coordenadas y no representa la precisión topográfica del levantamiento.</small></section>
<section><h2>Medidas complementarias</h2><div style="overflow-x:auto"><table><thead>
<tr><th>Indicador</th><th>Resultado</th></tr></thead><tbody>
<tr><td>Tramo más corto</td><td class="num">{shortest_station}: {shortest_distance} m</td></tr>
<tr><td>Tramo más largo</td><td class="num">{longest_station}: {longest_distance} m</td></tr>
<tr><td>Área por coordenadas UTM originales</td><td class="num">{raw_area} m²</td></tr>
<tr><td>Diferencia de área (ajustada − original)</td><td class="num">{area_difference} m²</td></tr>
</tbody></table></div><small>La diferencia conserva su signo: un valor positivo indica que el
área ajustada es mayor. Las áreas se calculan con precisión completa y se presentan
redondeadas a tres decimales.</small></section>
<section><h2>Plano del polígono</h2><div class="chart">{chart}</div>
<small>Vista de las coordenadas UTM originales, con proporción igual en ambos ejes.
El área del recuadro procede de las proyecciones corregidas. Para evitar números
superpuestos se muestran solo las etiquetas que caben; pase el cursor sobre
cualquier punto para ver su número y coordenadas.</small></section>
{delivery_table}
<section><h2>Detalle de tramos</h2><div style="overflow-x:auto">
<table><thead><tr><th>Estación</th><th>Punto inicial</th><th>Punto final</th>
<th>N inicial (m)</th><th>E inicial (m)</th><th>N final (m)</th><th>E final (m)</th>
<th>ΔN (m)</th><th>ΔE (m)</th><th>Distancia (m)</th><th>Rumbo</th></tr></thead>
<tbody>{rows}</tbody></table></div><small>Las proyecciones se reconstruyen con distancia redondeada a 0.001 m
y rumbo redondeado al segundo, y se corrigen en proporción a la longitud (Bowditch).
El error mostrado procede de ese redondeo; las coordenadas UTM de entrada ya cierran.
Área plana de cuadrícula UTM.</small></section></main>
</html>""".format(layer=esc(layer), crs=esc(crs), author=author_html,
                  close_notice=close_notice,
                  pre_section=pre_section, vertical_section=vertical_section,
                  slope_card=slope_card,
                  chart=chart, delivery_table=delivery_table,
                  count=len(segments),
                  perimeter=fmt(perimeter), area=fmt(summary["corrected_area"]),
                  hectares=fmt(summary["corrected_area"] / 10000),
                  st=fmt_small(summary["st"]), sg=fmt_small(summary["sg"]),
                  relative_error=fmt_small(summary["relative_error"]),
                  closure_error=fmt_small(summary["closure_error"]),
                  shortest_station=esc(shortest[0]),
                  shortest_distance=fmt(shortest[9]),
                  longest_station=esc(longest[0]),
                  longest_distance=fmt(longest[9]),
                  raw_area=fmt(summary["raw_area"]),
                  area_difference=area_difference_text,
                  precision=precision, rows="".join(rows))


def analyze_station_groups(groups):
    """Analyze each observed perimeter without connecting different groups."""
    polygons = []
    for name, records in groups.items():
        pre_summary = analyze_perimeter(records)
        closed, gap = prepare_perimeter(records, True)
        try:
            segments, perimeter, summary = calculate(closed)
        except ValueError as exc:
            raise ValueError("Polígono {}: {}".format(name, exc))
        full_summary = analyze_perimeter(closed)
        polygons.append((name, records, segments, perimeter, summary,
                         gap, pre_summary, full_summary))
    return polygons


def build_multi_html(polygons, source, crs, author=""):
    """Present independent polygon reports in one HTML file."""
    sections = []
    stylesheet = None
    for name, records, segments, perimeter, summary, gap, pre, full in polygons:
        single = build_html(
            segments, perimeter, summary, "{} · {}".format(source, name),
            crs, author, gap, gap > 1e-8, pre, full,
            "estaciones_{}_distancias_rumbos.csv".format(name))
        if stylesheet is None:
            stylesheet = single.split("<style>", 1)[1].split("</style>", 1)[0]
        content = single.split("<main>", 1)[1].rsplit("</main>", 1)[0]
        sections.append(
            '<article id="poligono-{}"><h2>Polígono {} · {} puntos</h2>{}</article>'.format(
                html.escape(name, quote=True), html.escape(name), len(records), content))
    total = math.fsum(item[4]["raw_area"] for item in polygons)
    navigation = " · ".join(
        '<a href="#poligono-{0}">{0}</a>'.format(html.escape(item[0], quote=True))
        for item in polygons)
    intro = (
        '<header><h1>Finca: varios polígonos</h1><div class="meta">'
        '<span>Archivo: {}</span><span>CRS: {}</span>{}</div></header>'
        '<section><h2>Resumen de contornos</h2><p>{} polígonos: {}.</p>'
        '<p>Suma aritmética de áreas UTM originales: <b>{:,.3f} m²</b>. '
        'La suma representa el área conjunta solo cuando los contornos no se superponen.</p>'
        '<small>Los puntos se ordenan por el número de punto del instrumento '
        'o por OBS numérico en el formato anterior. El código PT/PC identifica '
        'cada contorno. '
        'Se excluyen los traslados entre grupos. El lado final añadido une el '
        'último vértice con el primero de cada contorno y no constituye una '
        'reobservación de cierre topográfico.</small></section>').format(
            html.escape(source), html.escape(crs),
            '<span>Autor: {}</span>'.format(html.escape(author.strip()))
            if author.strip() else "", len(polygons), navigation, total)
    return ('<!doctype html><html lang="es"><meta charset="utf-8">'
            '<title>Cierre geométrico · varios polígonos</title><style>{}'
            'article{{margin-top:2.4rem}}article>h2{{padding:1rem;color:#087f83}}'
            '</style><main>{}{}</main></html>').format(
                stylesheet, intro, "".join(sections))


class CierreGeometrico:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        self.action = QAction(QIcon(os.path.join(os.path.dirname(__file__), "icon.svg")),
                              "Cierre geométrico", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("Cierre geométrico", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginMenu("Cierre geométrico", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle("Cierre geométrico desde coordenadas UTM")
        dialog.setMinimumWidth(430)
        dialog.setStyleSheet("""
            QDialog { background: #f1f6f8; color: #173b52; font-size: 13px; }
            QLabel#heading { background: #163b57; color: white; font-size: 19px;
                             font-weight: bold; padding: 11px; border-radius: 8px; }
            QLabel#help { color: #4c6879; padding: 5px 3px; }
            QGroupBox { background: white; border: 1px solid #d7e4ea;
                        border-radius: 8px; margin-top: 10px; padding: 8px 10px 6px;
                        font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px;
                               padding: 0 6px; color: #087f83; }
            QComboBox, QLineEdit { background: white; color: #173b52;
                border: 1px solid #b9ced9; border-radius: 5px;
                padding: 5px; min-height: 20px; }
            QComboBox:focus, QLineEdit:focus { border: 2px solid #19a99d; }
            QPushButton { background: #e3ebef; color: #163b57; border: 0;
                          border-radius: 6px; padding: 9px 17px; font-weight: bold; }
            QPushButton:hover { background: #cbdfe4; }
            QPushButton#generate { background: #087f83; color: white; }
            QPushButton#generate:hover { background: #096b73; }
            QTabWidget::pane { border: 1px solid #d7e4ea;
                               background: #f1f6f8; border-radius: 5px; }
            QTabBar::tab { background: #dce9ee; color: #173b52;
                           padding: 9px 16px; margin-right: 3px; }
            QTabBar::tab:selected { background: #087f83; color: white; }
        """)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(5)
        layout.setContentsMargins(10, 10, 10, 10)
        heading = QLabel("CIERRE GEOMÉTRICO  ·  COORDENADAS UTM")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        author_row = QHBoxLayout()
        author_row.addWidget(QLabel("Autor del informe"))
        author_box = QLineEdit()
        author_box.setPlaceholderText("Nombre (opcional)")
        author_box.setMaxLength(120)
        author_row.addWidget(author_box, 1)
        layout.addLayout(author_row)

        tabs = QTabWidget(dialog)
        layout.addWidget(tabs, 1)

        def add_tab(title):
            page = QScrollArea(tabs)
            page.setWidgetResizable(True)
            page.setFrameShape(QScrollArea.NoFrame)
            page.setMinimumHeight(180)
            content = QWidget(page)
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(6, 5, 9, 5)
            content_layout.setSpacing(5)
            page.setWidget(content)
            tabs.addTab(page, title)
            return content_layout

        data_layout = add_tab("Datos de entrada")
        analysis_layout = add_tab("Análisis y cierre")
        inputs = QGroupBox("Datos de entrada")
        form = QFormLayout()
        form.setVerticalSpacing(4)
        inputs.setLayout(form)
        data_layout.addWidget(inputs)
        source_box = QComboBox()
        source_box.addItem("Tabla cargada en QGIS", "layer")
        source_box.addItem("CSV directo de estación total", "station_csv")
        form.addRow("Origen", source_box)
        mode_box = QComboBox()
        mode_box.addItem("Polígono cerrado y cálculo de área", "polygon")
        mode_box.addItem("Varios polígonos por grupo PT/PC", "multi")
        analysis_group = QGroupBox("Configuración del análisis")
        analysis_form = QFormLayout(analysis_group)
        analysis_form.setVerticalSpacing(4)
        analysis_layout.addWidget(analysis_group)
        analysis_form.addRow("Tipo de informe", mode_box)
        csv_path_box = QLineEdit()
        csv_path_box.setPlaceholderText("Punto, Este, Norte, Cota, Grupo (PT/PC)")
        csv_browse = QPushButton("Examinar…")
        csv_row = QHBoxLayout()
        csv_row.addWidget(csv_path_box)
        csv_row.addWidget(csv_browse)
        form.addRow("Archivo CSV", csv_row)

        def browse_station_csv():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Seleccionar CSV de estación total", "", "CSV (*.csv *.txt);;Todos (*)")
            if path:
                csv_path_box.setText(path)

        csv_browse.clicked.connect(browse_station_csv)
        layer_box = QComboBox()
        layers = [layer for layer in QgsProject.instance().mapLayers().values()
                  if isinstance(layer, QgsVectorLayer)]
        for layer in sorted(layers, key=lambda item: item.name().casefold()):
            layer_box.addItem(layer.name(), layer.id())
        form.addRow("Tabla de coordenadas", layer_box)
        north_box, east_box, order_box = QComboBox(), QComboBox(), QComboBox()
        elevation_box = QComboBox()
        form.addRow("Norte (m)", north_box)
        form.addRow("Este (m)", east_box)
        form.addRow("Cota (m, opcional)", elevation_box)
        form.addRow("Orden numérico (opcional)", order_box)
        crs_box = QComboBox()
        crs_box.addItem("Seleccione zona UTM de las coordenadas", "")
        for code in (32616, 32617, 32618, 32716, 32717, 32718):
            crs_box.addItem("EPSG:{} (WGS 84 / UTM {}{})".format(
                code, code % 100, "N" if code < 32700 else "S"), "EPSG:{}".format(code))
        form.addRow("CRS de las coordenadas", crs_box)
        sequence_box = QLineEdit()
        sequence_box.setPlaceholderText("Ejemplo: 1-12,18,17,16,34-40; vacío = todas las filas")
        analysis_form.addRow("Secuencia de puntos", sequence_box)
        closure_help = QLabel(
            "Para un polígono, se repite internamente el primer punto si falta "
            "al final del archivo.")
        closure_help.setWordWrap(True)
        analysis_form.addRow("Cierre", closure_help)
        sequence_help = QLabel(
            "Un polígono admite un solo patrón en OBS. Para varios polígonos, "
            "use bloques PT/PC sin códigos ajenos. Si aparece VERT o DET "
            "entre los puntos, edite la tabla antes de calcular. "
            "No se calcula área si el trazado se cruza.")
        sequence_help.setWordWrap(True)
        sequence_help.setObjectName("help")
        analysis_layout.addWidget(sequence_help)

        def update_source():
            direct = source_box.currentData() == "station_csv"
            csv_path_box.setEnabled(direct)
            csv_browse.setEnabled(direct)
            for box in (layer_box, north_box, east_box, elevation_box, order_box):
                box.setEnabled(not direct)

        def update_mode():
            multiple = mode_box.currentData() == "multi"
            sequence_box.setEnabled(not multiple)

        source_box.currentIndexChanged.connect(update_source)
        mode_box.currentIndexChanged.connect(update_mode)
        update_source()
        update_mode()
        data_layout.addStretch()
        analysis_layout.addStretch()

        def update_fields():
            selected = QgsProject.instance().mapLayer(layer_box.currentData())
            for box in (north_box, east_box, elevation_box, order_box):
                box.clear()
            elevation_box.addItem("Sin cota", "")
            order_box.addItem("Orden original de filas", "")
            if selected is None:
                return
            names = [field.name() for field in selected.fields()]
            for box in (north_box, east_box, elevation_box, order_box):
                for name in names:
                    box.addItem(name, name)
            for box, patterns in ((north_box, ("NORTE", "NORTH", "NORTHING", "Y")),
                                  (east_box, ("ESTE", "EAST", "EASTING", "X"))):
                for index, name in enumerate(names):
                    if name.upper().replace("UTM", "").strip(" _") in patterns:
                        box.setCurrentIndex(index)
                        break
            for index, name in enumerate(names):
                if name.upper() in ("COTA", "ELEVACION", "ALTURA", "Z"):
                    elevation_box.setCurrentIndex(index + 1)
                    break
            for index, name in enumerate(names):
                if name.upper().strip() == "OBS":
                    first_feature = next(selected.getFeatures(), None)
                    if first_feature is not None:
                        try:
                            float(first_feature[name])
                        except (ValueError, TypeError):
                            pass  # OBS can contain PT/PC in instrument exports.
                        else:
                            order_box.setCurrentIndex(index + 1)
                    break
            index = crs_box.findData(selected.crs().authid())
            if index >= 0:
                crs_box.setCurrentIndex(index)

        layer_box.currentIndexChanged.connect(update_fields)
        update_fields()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        generate = QPushButton("Validar y generar HTML")
        generate.setObjectName("generate")
        buttons.addButton(generate, QDialogButtonBox.AcceptRole)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        screen = dialog.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            dialog.setMaximumHeight(max(350, available.height() - 90))
            dialog.resize(min(530, available.width() - 70),
                          min(560, available.height() - 90))
        else:
            dialog.resize(510, 520)

        def generate_report():
            if not crs_box.currentData():
                QMessageBox.warning(dialog, "CRS", "Seleccione la zona UTM de los datos.")
                return
            crs = QgsCoordinateReferenceSystem(crs_box.currentData())
            records = []
            try:
                selected_mode = mode_box.currentData()
                direct = source_box.currentData() == "station_csv"
                if direct:
                    csv_path = csv_path_box.text().strip()
                    if not csv_path:
                        raise ValueError("Seleccione el CSV de la estación total.")
                    records = read_station_csv(csv_path)
                    source_name = os.path.basename(csv_path)
                    if selected_mode == "multi":
                        observations = read_station_observations(csv_path)
                    else:
                        validate_code_interpretation(
                            read_station_observations(csv_path, allow_missing=True))
                else:
                    layer = QgsProject.instance().mapLayer(layer_box.currentData())
                    if layer is None:
                        raise ValueError("Seleccione una tabla cargada en QGIS.")
                    if north_box.currentData() == east_box.currentData():
                        raise ValueError("Norte y Este deben ser campos distintos.")
                    fields = {field.name().upper().strip(): field.name()
                              for field in layer.fields()}
                    if selected_mode == "multi":
                        if "PUNTO" not in fields or "OBS" not in fields:
                            raise ValueError("La tabla necesita los campos PUNTO y OBS "
                                             "para varios polígonos.")
                    if "PUNTO" in fields and "OBS" in fields:
                        observations = []
                        for feature in layer.getFeatures():
                            observations.append((
                                feature[fields["OBS"]], feature[fields["PUNTO"]],
                                feature[north_box.currentData()],
                                feature[east_box.currentData()],
                                feature[elevation_box.currentData()]
                                if elevation_box.currentData() else None,
                                "Registro {}".format(feature.id())))
                        if selected_mode == "polygon":
                            validate_code_interpretation(observations)
                    if selected_mode == "polygon":
                        for feature in layer.getFeatures():
                            order = (feature[order_box.currentData()]
                                     if order_box.currentData() else len(records))
                            if order_box.currentData():
                                try:
                                    order = float(order)
                                except (ValueError, TypeError):
                                    raise ValueError(
                                        "El campo de orden debe ser numérico. "
                                        "Use PUNTO numérico u OBS numérico; "
                                        "PT/PC identifica el grupo.")
                                if not math.isfinite(order):
                                    raise ValueError("El campo de orden contiene valores no finitos.")
                            records.append((str(feature.id()), feature[north_box.currentData()],
                                            feature[east_box.currentData()],
                                            feature[elevation_box.currentData()]
                                            if elevation_box.currentData() else None, order))
                        if order_box.currentData():
                            orders = [item[4] for item in records]
                            if len(orders) != len(set(orders)):
                                raise ValueError("El campo de orden contiene duplicados.")
                            records.sort(key=lambda item: item[4])
                        records = [row[:4] for row in records]
                    source_name = layer.name()
                if selected_mode == "multi":
                    polygons = analyze_station_groups(
                        group_station_observations(observations))
                else:
                    if sequence_box.text().strip():
                        records = choose_points(records, sequence_box.text())
                    pre_summary = analyze_perimeter(records)
                    closed_records, closing_gap = prepare_perimeter(records, True)
                    added_close = closing_gap > 1e-8
                    segments, perimeter, summary = calculate(closed_records)
                    full_summary = analyze_perimeter(closed_records)
            except (ValueError, TypeError, OSError) as exc:
                QMessageBox.warning(dialog, "Datos inválidos", str(exc))
                return
            path, _ = QFileDialog.getSaveFileName(dialog, "Guardar informe", "cierre_geometrico.html",
                                                  "HTML (*.html)")
            if not path:
                return
            if not path.lower().endswith(".html"):
                path += ".html"
            try:
                with open(path, "w", encoding="utf-8") as report:
                    if selected_mode == "multi":
                        report.write(build_multi_html(
                            polygons, source_name, crs.authid(), author_box.text()))
                    else:
                        report.write(build_html(segments, perimeter, summary,
                                                source_name, crs.authid(), author_box.text(),
                                                closing_gap, added_close,
                                                pre_summary, full_summary))
            except OSError as exc:
                QMessageBox.critical(dialog, "Error de escritura", str(exc))
                return
            webbrowser.open("file://" + os.path.abspath(path))
            QMessageBox.information(dialog, "Informe generado", path)

        generate.clicked.connect(generate_report)
        dialog.exec_()
