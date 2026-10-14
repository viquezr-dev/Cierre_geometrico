# -*- coding: utf-8 -*-
# Desarrollado por Raul Viquez, viquezr@gmail.com

"""QGIS plugin: geometric traverse from ordered projected coordinates."""

import html
import math
import os
import webbrowser
import csv
import re
import base64
import io

from qgis.PyQt.QtGui import QGuiApplication, QIcon
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QAction, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QRadioButton, QButtonGroup,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QMessageBox, QPushButton,
    QHBoxLayout, QScrollArea,
    QWidget, QVBoxLayout, QTabWidget, QDoubleSpinBox,
)
from qgis.core import (QgsCoordinateReferenceSystem, QgsProject, QgsUnitTypes,
                       QgsVectorLayer)
from qgis.gui import QgsProjectionSelectionWidget


def input_format_help(kind):
    """Explain the active measurement fields in the data entry tab."""
    common = ("<p>Se usan los datos del origen marcado: tabla QGIS o archivo CSV. "
              "Se ignoran columnas adicionales... "
              "Si COTA falta o está completamente vacía, marque la casilla para calcular en planta; "
              "si solo faltan algunas cotas, debe completarlas.</p>")
    if kind == "angles":
        return (
            "<b>Ángulos y distancias: campos de entrada</b><br>"
            "<b>EST / PUNTO:</b> entero, estación del vértice; secuencia única 1 a N.<br>"
            "<b>ANGULO:</b> ángulo interior observado en ese vértice, mayor que 0° y menor que 360°. "
            "Acepta grados decimales numéricos o texto DMS (90°00′15″).<br>"
            "<b>DISTANCIA:</b> horizontal en metros, numérica y positiva, desde EST hacia la siguiente estación. "
            "La fila N contiene la distancia medida hacia EST 1.<br>"
            "<b>COTA / Z:</b> elevación del vértice en metros; numérica y opcional.<br>"
            "<b>OBS:</b> opcional; no separa grupos en este modo."
            "<p><b>Ejemplo decimal:</b> EST,ANGULO,DISTANCIA,COTA → 1,90.005,45.230,100.000. "
            "90.005° equivale a 90°00′18″; no es formato D.MMSS.</p>"
            "<p>En <b>Análisis y cierre</b>, indique Norte y Este iniciales, azimut 1-2 desde el norte "
            "en sentido horario y sentido del recorrido. Se requieren al menos tres vértices "
            "y todos los ángulos interiores, incluido EST 1. No use ángulos de círculo o cenitales "
            "ni distancias inclinadas directamente.</p>" + common)
    if kind == "bearings":
        return (
            "<b>Rumbos y distancias: campos de entrada</b><br>"
            "<b>ESTACION / PUNTO / EST:</b> texto que identifica el tramo: 1-2, 2-3, …, N-1. "
            "Use guion, no punto; el último tramo medido regresa a EST 1.<br>"
            "<b>RUMBO / RUMBOS:</b> texto cuadrantal, por ejemplo N81°34′59″E. "
            "El ángulo está entre 0° y 90°; use N/S y E/W (también O para oeste).<br>"
            "<b>DISTANCIA / DIST / DH:</b> horizontal en metros, numérica y positiva, del tramo indicado.<br>"
            "<b>COTA / Z:</b> elevación del punto de origen del tramo, en metros; numérica y opcional."
            "<p><b>Ejemplo:</b> PUNTO,DISTANCIA,RUMBO → 1-2,7.39,N81°34′59″E. "
            "El CSV debe tener encabezados; en QGIS, PUNTO y RUMBO deben ser campos de texto.</p>"
            "<p>En <b>Análisis y cierre</b>, Norte y Este iniciales ubican el dibujo; ambas casillas "
            "vacías usan un origen local 0,0. No requiere azimut inicial ni sentido del recorrido. "
            "Calcula cierre lineal; no evalúa cierre angular. Bowditch es opcional y requiere cumplir "
            "la precisión mínima indicada.</p>" + common)
    return (
        "<b>Coordenadas: campos de entrada</b><br>"
        "<b>PUNTO / EST:</b> entero, identificador del vértice; secuencia única 1 a N.<br>"
        "<b>ESTE / X:</b> coordenada horizontal Este en metros, tipo real/doble.<br>"
        "<b>NORTE / Y:</b> coordenada horizontal Norte en metros, tipo real/doble.<br>"
        "<b>COTA / Z:</b> elevación del vértice en metros, entero/real/doble; incluya el campo.<br>"
        "<b>OBS:</b> se ignora para un polígono; texto obligatorio para varios, "
        "en bloques PT, PC, PT1, PC1 o PT_2."
        "<p><b>Ejemplo:</b> PUNTO,ESTE,NORTE,COTA → 1,625897.370,975306.346,100.000. "
        "CSV sin encabezado: punto, este, norte, cota y OBS opcional. "
        "El mapa y las tablas siguen el orden numérico de PUNTO.</p>"
        "<p>Seleccione el CRS proyectado en metros de estas coordenadas. "
        "El contorno repite internamente el primer punto si falta al final; "
        "los residuos derivados de rumbos/distancias redondeados no son cierre observado en campo.</p>"
        + common.replace("Si COTA falta o está", "Si COTA está"))


def resolve_input_fields(names, require_obs=False):
    """Resolve canonical field names and station aliases without guessing."""
    aliases = {"PUNTO": ("PUNTO", "EST"), "ESTE": ("ESTE", "X"),
               "NORTE": ("NORTE", "Y"), "COTA": ("COTA", "Z"),
               "OBS": ("OBS",)}
    fields = {}
    for canonical, alternatives in aliases.items():
        matches = [name for name in names if str(name).strip().upper() in alternatives]
        if len(matches) > 1:
            raise ValueError("Campos ambiguos para {}: {}. Deje una sola columna.".format(
                canonical, ", ".join(matches)))
        if matches:
            fields[canonical] = matches[0]
        elif canonical != "OBS":
            raise ValueError("Falta el campo {} ({}).".format(canonical, " o ".join(alternatives)))
        elif require_obs:
            raise ValueError("El campo OBS es obligatorio para varios polígonos.")
    return fields


def parse_point_number(value, location):
    """Accept only integral point identifiers, not decimal-looking numbers."""
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value).strip()):
        raise ValueError("{}: PUNTO debe ser un entero positivo (1 a N).".format(location))
    return int(str(value).strip())


def validate_cota_completeness(records, allow_all_empty=False):
    """Accept an entirely empty COTA field only through explicit opt-in."""
    missing = [record[0] for record in records if record[3] is None]
    if not missing:
        return
    if len(missing) != len(records):
        raise ValueError("PUNTO {}: COTA está vacía, pero otros puntos tienen cota. "
                         "Complete las cotas faltantes antes de calcular.".format(missing[0]))
    if not allow_all_empty:
        raise ValueError("La columna COTA está vacía en todos los puntos. "
                         "Marque 'Permitir COTA completamente vacía' para calcular "
                         "solo en planta.")


def parse_coordinate(value, field, point, location, optional=False):
    """Report the exact invalid coordinate or elevation value."""
    raw = str(value).strip()
    if optional and (value is None or raw.upper() in ("", "NULL", "NONE")):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = None
    if number is None or not math.isfinite(number):
        shown = "NULL" if value is None or raw.upper() in ("", "NULL", "NONE") else repr(value)[:80]
        raise ValueError("PUNTO {} ({}): {} = {}. Se requiere un valor numérico "
                         "finito; revise el dato de entrada.".format(
                             point, location, field, shown))
    return number


def read_station_observations(path, require_obs=False):
    """Read CSV fields by name; unlabeled rows use P,E,N,Z[,OBS]."""
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
    try:
        float(first[0])
        labeled = False
    except ValueError:
        labeled = True
    if labeled:
        fields = resolve_input_fields(first, require_obs=require_obs)
        indices = {name: first.index(field) for name, field in fields.items()}
        rows = rows[1:]
    else:
        if len(first) not in (4, 5):
            raise ValueError("Sin encabezado se requieren cuatro o cinco columnas: "
                             "PUNTO/EST, ESTE/X, NORTE/Y, COTA/Z y OBS opcional.")
        indices = {"PUNTO": 0, "ESTE": 1, "NORTE": 2, "COTA": 3}
        if len(first) == 5:
            indices["OBS"] = 4
        elif require_obs:
            raise ValueError("El campo OBS es obligatorio para varios polígonos.")
    if not rows:
        raise ValueError("El CSV no contiene puntos.")
    observations = []
    for line, cells in rows:
        location = "Fila {}".format(line)
        if len(cells) != len(first):
            raise ValueError("{}: número de columnas distinto del formato de entrada.".format(location))
        point = str(parse_point_number(cells[indices["PUNTO"]], location))
        n = parse_coordinate(cells[indices["NORTE"]], "NORTE/Y", point, location)
        e = parse_coordinate(cells[indices["ESTE"]], "ESTE/X", point, location)
        z = parse_coordinate(cells[indices["COTA"]], "COTA/Z", point, location, optional=True)
        code = cells[indices["OBS"]] if "OBS" in indices else None
        observations.append((point, point, n, e, z, location, code))
    return observations


def normalize_station_observations(observations, ignore_obs=False):
    """Validate numbered records; OBS is ignored for a single polygon."""
    ordered = []
    for observation in observations:
        raw_order, point_id, north, east, elevation, location = observation[:6]
        order = parse_point_number(raw_order, location)
        if parse_point_number(point_id, location) != order:
            raise ValueError("{}: PUNTO y número de orden no coinciden.".format(location))
        raw_code = observation[6] if len(observation) > 6 else None
        name = str(raw_code).strip().upper() if raw_code is not None else ""
        if ignore_obs:
            name = ""
        elif not name or name in ("NULL", "NONE") or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError("{}: OBS debe ser un código de texto no vacío.".format(location))
        n = parse_coordinate(north, "NORTE", order, location)
        e = parse_coordinate(east, "ESTE", order, location)
        z = parse_coordinate(elevation, "COTA", order, location, optional=True)
        ordered.append((order, name, (str(order), n, e, z)))
    by_number = {item[0]: item for item in ordered}
    if len(by_number) != len(ordered):
        raise ValueError("PUNTO duplicado: la numeración debe ser de 1 a N.")
    for expected in range(1, len(ordered) + 1):
        if expected not in by_number:
            raise ValueError("Falta PUNTO {}: la numeración debe ser consecutiva de 1 a N.".format(expected))
    return [by_number[number] for number in range(1, len(ordered) + 1)]


def group_station_observations(observations):
    """Keep whole polygon groups separate in the observed point sequence."""
    ordered = normalize_station_observations(observations)
    for _, name, record in ordered:
        if not re.fullmatch(r"(?:PT|PC)(?:[0-9]+|_[0-9]+)?", name):
            raise ValueError(
                "Punto {}: OBS '{}' debe ser PT o PC, con sufijo numérico opcional (PT1, PC1, PT_2). "
                "Edite la observación antes de calcular.".format(record[0], name))
    groups = {}
    completed = set()
    previous = None
    for _, name, record in sorted(ordered):
        if name != previous:
            if name in completed:
                raise ValueError(
                    "Punto {}: OBS '{}' reaparece fuera de su bloque de polígono. "
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
        "cota_count": len(elevations),
        "cota_total": len(points),
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
    # Geometric tolerance must depend on side extent, not absolute UTM origin.
    origin = a
    a, b, c, d = tuple((point[0] - origin[0], point[1] - origin[1]) for point in (a, b, c, d))
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


# =====================================================================
# === NUEVO === Cálculo real de cierre desde observaciones de campo ===
# =====================================================================


def parse_angle_deg(value, location, field):
    """Accept finite decimal degrees or explicit DMS with valid components."""
    raw = str(value).strip() if value is not None else ""
    if not raw or raw.upper() in ("NULL", "NONE"):
        raise ValueError("{}: {} está vacío.".format(location, field))
    try:
        angle = float(raw)
    except ValueError:
        angle = None
    if angle is not None:
        if not math.isfinite(angle):
            raise ValueError("{}: {} debe ser finito.".format(location, field))
        return angle
    sign = -1 if raw.startswith("-") else 1
    body = raw[1:].strip() if raw.startswith(("-", "+")) else raw
    normalized = re.sub(r"[°ºdD:'′mM\"″sS]", " ", body)
    parts = normalized.split()
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        numbers = []
    if len(numbers) == 1 and re.search(r"[°ºdD]", body) and math.isfinite(numbers[0]) and numbers[0] >= 0:
        return sign * numbers[0]
    if (len(numbers) not in (2, 3) or not all(math.isfinite(number) for number in numbers)
            or numbers[0] < 0 or not numbers[0].is_integer()
            or not 0 <= numbers[1] < 60
            or len(numbers) == 3 and (not numbers[1].is_integer() or not 0 <= numbers[2] < 60)):
        raise ValueError("{}: {} = {} no es DMS válido. Use 90°00'50\" o 90:00:50; "
                         "minutos y segundos deben ser menores de 60.".format(location, field, repr(value)[:60]))
    return sign * (numbers[0] + numbers[1] / 60 + (numbers[2] / 3600 if len(numbers) == 3 else 0))


def read_observation_csv(path):
    """Read observations: EST/PUNTO, ANGULO, DISTANCIA, [COTA], [OBS]."""
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
        raise ValueError("El CSV de observaciones está vacío.")
    first = rows[0][1]
    try:
        float(first[0])
        labeled = False
    except ValueError:
        labeled = True
    aliases = {
        "PUNTO": ("PUNTO", "EST"),
        "ANGULO": ("ANGULO", "ANG", "ANG_H", "H_ANGLE", "ANGULO_HORIZONTAL"),
        "DISTANCIA": ("DISTANCIA", "DIST", "DH", "DIST_H", "DISTANCIA_HORIZONTAL"),
        "COTA": ("COTA", "Z"),
        "OBS": ("OBS",),
    }
    if labeled:
        fields = {}
        for canonical, options in aliases.items():
            matches = [name for name in first
                       if str(name).strip().upper() in options]
            if len(matches) > 1:
                raise ValueError("Campos ambiguos para {}: {}".format(
                    canonical, ", ".join(matches)))
            if matches:
                fields[canonical] = matches[0]
            elif canonical not in ("COTA", "OBS"):
                raise ValueError("Falta el campo {} ({}).".format(
                    canonical, " o ".join(options)))
        indices = {name: first.index(field) for name, field in fields.items()}
        rows = rows[1:]
    else:
        if len(first) not in (3, 4, 5):
            raise ValueError("Sin encabezado se requieren 3 a 5 columnas: "
                             "EST, ANGULO, DISTANCIA, COTA opcional, OBS opcional.")
        indices = {"PUNTO": 0, "ANGULO": 1, "DISTANCIA": 2}
        if len(first) >= 4:
            indices["COTA"] = 3
        if len(first) >= 5:
            indices["OBS"] = 4
    if not rows:
        raise ValueError("El CSV no contiene observaciones.")
    observations = []
    for line, cells in rows:
        location = "Fila {}".format(line)
        if len(cells) != len(first):
            raise ValueError("{}: número de columnas inconsistente.".format(location))
        point = str(parse_point_number(cells[indices["PUNTO"]], location))
        angle = parse_angle_deg(cells[indices["ANGULO"]], location, "ANGULO")
        distance = parse_coordinate(cells[indices["DISTANCIA"]], "DISTANCIA",
                                    point, location)
        cota = (parse_coordinate(cells[indices["COTA"]], "COTA", point, location,
                                 optional=True) if "COTA" in indices else None)
        code = cells[indices["OBS"]] if "OBS" in indices else None
        observations.append((point, angle, distance, cota, location, code))
    return observations


def read_observations_from_layer(layer):
    """Read observations (PUNTO, ANGULO, DISTANCIA, COTA?, OBS?) from a QGIS layer."""
    aliases = {
        "PUNTO": ("PUNTO", "EST"),
        "ANGULO": ("ANGULO", "ANG", "ANG_H", "H_ANGLE", "ANGULO_HORIZONTAL"),
        "DISTANCIA": ("DISTANCIA", "DIST", "DH", "DIST_H", "DISTANCIA_HORIZONTAL"),
        "COTA": ("COTA", "Z"),
        "OBS": ("OBS",),
    }
    names = [f.name() for f in layer.fields()]
    fields = {}
    for canonical, options in aliases.items():
        matches = [n for n in names if str(n).strip().upper() in options]
        if len(matches) > 1:
            raise ValueError("Campos ambiguos para {}: {}. Deje una sola columna.".format(
                canonical, ", ".join(matches)))
        if matches:
            fields[canonical] = matches[0]
        elif canonical not in ("COTA", "OBS"):
            raise ValueError("Falta el campo {} ({}).".format(canonical, " o ".join(options)))
    field_by_name = {f.name(): f for f in layer.fields()}
    integer_types = (QVariant.Int, QVariant.UInt,
                     QVariant.LongLong, QVariant.ULongLong)
    if field_by_name[fields["PUNTO"]].type() not in integer_types:
        raise ValueError("El campo {} debe ser de tipo entero.".format(fields["PUNTO"]))
    if field_by_name[fields["ANGULO"]].type() not in (QVariant.Double, QVariant.String) + integer_types:
        raise ValueError("El campo {} debe ser numérico o texto DMS.".format(fields["ANGULO"]))
    if field_by_name[fields["DISTANCIA"]].type() not in (QVariant.Double,) + integer_types:
        raise ValueError("El campo {} debe ser real o doble.".format(fields["DISTANCIA"]))
    if "COTA" in fields and field_by_name[fields["COTA"]].type() not in (
            QVariant.Double,) + integer_types:
        raise ValueError("El campo COTA debe ser numérico (entero o real).")
    observations = []
    for feature in layer.getFeatures():
        location = "registro QGIS ID {}".format(feature.id())
        point = str(parse_point_number(feature[fields["PUNTO"]], location))
        angle = parse_angle_deg(feature[fields["ANGULO"]], location, "ANGULO")
        distance = parse_coordinate(feature[fields["DISTANCIA"]], "DISTANCIA",
                                    point, location)
        cota = (parse_coordinate(feature[fields["COTA"]], "COTA", point, location,
                                 optional=True) if "COTA" in fields else None)
        code = feature[fields["OBS"]] if "OBS" in fields else None
        observations.append((point, angle, distance, cota, location, code))
    by_number = {}
    for obs in observations:
        number = int(obs[0])
        if number in by_number:
            raise ValueError("PUNTO {} está duplicado.".format(number))
        by_number[number] = obs
    for expected in range(1, len(observations) + 1):
        if expected not in by_number:
            raise ValueError("Falta PUNTO {}: la numeración debe ser consecutiva de 1 a N.".format(expected))
    return [by_number[i] for i in range(1, len(observations) + 1)]


def _report_angle_dms(value):
    """Display degrees as DMS with four decimal places in arcseconds."""
    sign = "−" if value < 0 else ""
    units = round(abs(value) * 3600 * 10000)
    degrees, remainder = divmod(units, 3600 * 10000)
    minutes, second_units = divmod(remainder, 60 * 10000)
    return "{}{}°{:02d}′{:07.4f}″".format(sign, degrees, minutes, second_units / 10000)


def _azimuth_dms(az_deg):
    """Format an azimuth (0..360) as D°M'S\"."""
    az = az_deg % 360.0
    total_seconds = round(az * 3600) % (360 * 3600)
    degrees, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return "{}°{:02d}'{:02d}\"".format(degrees, minutes, seconds)


def _angle_dms(deg):
    """Format a general angle (degrees) as D°M'S\" without modulo."""
    sign = "-" if deg < 0 else ""
    total_seconds = round(abs(deg) * 3600)
    degrees, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return "{}{}°{:02d}'{:02d}\"".format(sign, degrees, minutes, seconds)


def normalize_traverse_rows(rows, allow_empty_cota=False):
    """Validate EST=1..N, interior decimal degrees and horizontal metres."""
    ordered = {}
    for index, row in enumerate(rows, 1):
        if len(row) != 4:
            raise ValueError("Fila {}: se requieren EST, ANGULO, DISTANCIA y COTA.".format(index))
        station = parse_point_number(row[0], "Fila {}".format(index))
        if station in ordered:
            raise ValueError("EST {} está duplicada.".format(station))
        angle = parse_coordinate(row[1], "ANGULO", station, "observación")
        distance = parse_coordinate(row[2], "DISTANCIA", station, "observación")
        cota = parse_coordinate(row[3], "COTA", station, "observación", optional=True)
        if not 0 < angle < 360:
            raise ValueError("EST {}: ANGULO debe ser interior, mayor que 0 y menor que 360 grados.".format(station))
        if distance <= 0:
            raise ValueError("EST {}: DISTANCIA horizontal debe ser mayor que cero.".format(station))
        ordered[station] = (str(station), angle, distance, cota)
    if len(ordered) < 3:
        raise ValueError("Se requieren al menos tres estaciones con todos los ángulos interiores.")
    for station in range(1, len(ordered) + 1):
        if station not in ordered:
            raise ValueError("Falta EST {}: la secuencia debe ser de 1 a N.".format(station))
    records = [ordered[station] for station in range(1, len(ordered) + 1)]
    validate_cota_completeness(records, allow_empty_cota)
    return records


def _propagate_traverse(records, angles, north, east, initial_azimuth, clockwise):
    """Propagate from a fixed first azimuth; the final measured side returns to 1'."""
    azimuths = [initial_azimuth % 360]
    sign = 1 if clockwise else -1
    for angle in angles[1:]:
        azimuths.append((azimuths[-1] + sign * (180 - angle)) % 360)
    return _project_traverse(records, azimuths, north, east)


def _project_traverse(records, azimuths, north, east):
    """Project given directions without adding a closing side."""
    deltas = [(record[2] * math.cos(math.radians(azimuth)),
               record[2] * math.sin(math.radians(azimuth)))
              for record, azimuth in zip(records, azimuths)]
    points = [(records[0][0], north, east, records[0][3])]
    for index in range(len(records)):
        next_index = (index + 1) % len(records)
        label = records[next_index][0] if next_index else "1′"
        # fsum avoids accumulating unnecessary binary error; do not force arrival.
        n = north + math.fsum(delta[0] for delta in deltas[:index + 1])
        e = east + math.fsum(delta[1] for delta in deltas[:index + 1])
        points.append((label, n, e, records[next_index][3]))
    dn = math.fsum(delta[0] for delta in deltas)
    de = math.fsum(delta[1] for delta in deltas)
    perimeter = math.fsum(record[2] for record in records)
    error = math.hypot(dn, de)
    return {"azimuths": azimuths, "deltas": deltas, "points": points,
            "dn": dn, "de": de, "error": error, "perimeter": perimeter,
            "relative_error": error / perimeter,
            "precision": perimeter / error if error else math.inf}


def _closed_traverse_area(points):
    """Validate and measure the adjusted geometry, without rounding observations."""
    xy = [(point[2] - points[0][2], point[1] - points[0][1]) for point in points]
    count = len(xy) - 1
    if len(set(xy[:-1])) != count:
        raise ValueError("El contorno ajustado tiene vértices repetidos.")
    for i in range(count):
        if math.hypot(xy[i + 1][0] - xy[i][0], xy[i + 1][1] - xy[i][1]) <= 1e-10:
            raise ValueError("El contorno ajustado tiene lados de longitud cero.")
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue
            if _intersects(xy[i], xy[i + 1], xy[j], xy[j + 1]):
                raise ValueError("Los lados {} y {} del contorno ajustado se cruzan o se tocan.".format(i + 1, j + 1))
    area = abs(math.fsum(xy[i][0] * xy[i + 1][1] - xy[i + 1][0] * xy[i][1] for i in range(count))) / 2
    if area <= 1e-10:
        raise ValueError("El área ajustada es nula.")
    return area


def calculate_raw_traverse(records, initial_north, initial_east, initial_azimuth, clockwise=True,
                           max_angular_seconds=None, minimum_precision=None,
                           adjust_angles=False, adjust_linear=False, allow_empty_cota=False):
    """Analyze a closed loop from measured interior angles and horizontal distances."""
    records = normalize_traverse_rows(records, allow_empty_cota)
    north = parse_coordinate(initial_north, "NORTE inicial", "1", "control")
    east = parse_coordinate(initial_east, "ESTE inicial", "1", "control")
    azimuth = parse_coordinate(initial_azimuth, "Azimut inicial", "1-2", "control")
    if not 0 <= azimuth < 360:
        raise ValueError("El azimut inicial debe estar entre 0 y menos de 360 grados decimales.")
    criteria = {}
    for name, value in (("Tolerancia angular (segundos)", max_angular_seconds),
                        ("Precisión mínima 1:N", minimum_precision)):
        if value is not None:
            value = parse_coordinate(value, name, "control", "criterio")
            if value <= 0:
                raise ValueError("{} debe ser mayor que cero.".format(name))
        criteria[name] = value
    max_angular_seconds = criteria["Tolerancia angular (segundos)"]
    minimum_precision = criteria["Precisión mínima 1:N"]
    if adjust_angles and max_angular_seconds is None:
        raise ValueError("Indique la tolerancia angular antes de aplicar el ajuste angular.")
    if adjust_linear and (not adjust_angles or minimum_precision is None):
        raise ValueError("Bowditch requiere seleccionar el ajuste angular y una precisión mínima 1:N.")
    angles = [record[1] for record in records]
    theoretical = (len(records) - 2) * 180.0
    observed_sum = math.fsum(angles)
    angular_error = observed_sum - theoretical
    angular_seconds = angular_error * 3600
    angular_ok = None if max_angular_seconds is None else abs(angular_seconds) <= max_angular_seconds + 1e-7
    raw = _propagate_traverse(records, angles, north, east, azimuth, clockwise)
    warnings = []
    corrections = [0.0] * len(records)
    angles_applied = bool(adjust_angles and angular_ok)
    if adjust_angles and not angular_ok:
        warnings.append("El error angular excede el criterio indicado: no se ajustaron ángulos ni proyecciones.")
    if angles_applied:
        corrections = [-angular_error / len(records)] * len(records)
        corrected_angles = [angle + correction for angle, correction in zip(angles, corrections)]
        if any(not 0 < angle < 360 for angle in corrected_angles):
            raise ValueError("La corrección produce ángulos interiores inválidos. Revise las observaciones.")
        working = _propagate_traverse(records, corrected_angles, north, east, azimuth, clockwise)
    else:
        corrected_angles = list(angles)
        working = raw
    linear_ok = (None if minimum_precision is None
                 else working["error"] <= working["perimeter"] / minimum_precision)
    if linear_ok is False:
        warnings.append("El cierre lineal no cumple la precisión mínima indicada para la etapa evaluada.")
    if angular_ok is False and not adjust_angles:
        warnings.append("El error angular no cumple la tolerancia indicada.")
    if max_angular_seconds is None or minimum_precision is None:
        warnings.append("No se han definido ambos criterios de aceptación; no se determina cumplimiento conjunto.")
    if not angles_applied:
        warnings.append("El cierre lineal mostrado usa ángulos sin ajustar; "
                        "la evaluación definitiva requiere balance angular.")
    linear_applied = bool(adjust_linear and angles_applied and linear_ok)
    if adjust_linear and not linear_applied:
        warnings.append("No se aplicó Bowditch: primero deben cumplirse los criterios angular y lineal.")
    linear_corrections = [(0.0, 0.0)] * len(records)
    adjusted_points = None
    adjusted_area = None
    adjusted_deltas = None
    if linear_applied:
        linear_corrections = [(-working["dn"] * record[2] / working["perimeter"],
                               -working["de"] * record[2] / working["perimeter"]) for record in records]
        adjusted_deltas = [(dn + cn, de + ce) for (dn, de), (cn, ce)
                           in zip(working["deltas"], linear_corrections)]
        adjusted_points = [working["points"][0]]
        for index in range(len(records)):
            label, _, _, z = working["points"][index + 1]
            adjusted_points.append((label, north + math.fsum(v[0] for v in adjusted_deltas[:index + 1]),
                                    east + math.fsum(v[1] for v in adjusted_deltas[:index + 1]), z))
        # Only the adjusted endpoint is snapped to control for machine arithmetic.
        adjusted_points[-1] = ("1 (ajustada)", north, east, records[0][3])
        try:
            adjusted_area = _closed_traverse_area(adjusted_points)
        except ValueError as exc:
            warnings.append(str(exc) + " No se presenta área y no se considera un contorno válido.")
    return {"records": records, "initial_north": north, "initial_east": east, "initial_azimuth": azimuth,
            "clockwise": clockwise, "observed_sum": observed_sum, "theoretical_sum": theoretical,
            "angular_error": angular_error, "angular_seconds": angular_seconds,
            "max_angular_seconds": max_angular_seconds, "minimum_precision": minimum_precision,
            "angular_ok": angular_ok, "linear_ok": linear_ok, "angles_applied": angles_applied,
            "linear_applied": linear_applied, "angle_corrections": corrections, "corrected_angles": corrected_angles,
            "linear_corrections": linear_corrections, "raw": raw, "working": working,
            "adjusted_points": adjusted_points, "adjusted_deltas": adjusted_deltas,
            "adjusted_area": adjusted_area, "warnings": warnings}


def parse_quadrant_bearing(value, location="RUMBO"):
    """Parse a quadrant bearing, retaining the supplied observation."""
    text = str(value).strip().upper().replace("º", "°")
    match = re.fullmatch(r"([NS])\s*(.+?)\s*([EWO])", text)
    if not match:
        raise ValueError("{}: rumbo inválido '{}'; use N81°34′59″E.".format(location, value))
    north_south, angle_text, east_west = match.groups()
    angle = parse_angle_deg(angle_text, location, "RUMBO")
    if not 0 <= angle <= 90:
        raise ValueError("{}: el ángulo del rumbo debe estar entre 0° y 90°.".format(location))
    azimuth = angle if north_south == "N" else 180 - angle
    if east_west in ("W", "O"):
        azimuth = (360 - azimuth) % 360
    return azimuth % 360


def resolve_bearing_fields(names):
    aliases = {"ESTACION": ("ESTACION", "PUNTO", "EST"),
               "DISTANCIA": ("DISTANCIA", "DIST", "DH"), "RUMBO": ("RUMBO", "RUMBOS"),
               "COTA": ("COTA", "Z")}
    fields = {}
    for canonical, choices in aliases.items():
        matches = [name for name in names if str(name).strip().upper() in choices]
        if len(matches) > 1:
            raise ValueError("Campos ambiguos para {}: {}.".format(canonical, ", ".join(matches)))
        if matches:
            fields[canonical] = matches[0]
        elif canonical != "COTA":
            raise ValueError("Falta el campo {} ({}).".format(canonical, " o ".join(choices)))
    return fields


def read_bearing_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as source:
        sample = source.read(8192)
        source.seek(0)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
        reader = csv.DictReader(source, delimiter=delimiter)
        fields = resolve_bearing_fields(reader.fieldnames or [])
        rows = []
        for line, row in enumerate(reader, 2):
            if not any(str(value or "").strip() for value in row.values()):
                continue
            if None in row:
                raise ValueError("Registro {}: hay más valores que columnas; revise el CSV.".format(line))
            rows.append(tuple(row.get(fields[key]) if key in fields else None
                              for key in ("ESTACION", "DISTANCIA", "RUMBO", "COTA")))
    return rows


def read_bearings_from_layer(layer):
    fields = resolve_bearing_fields([field.name() for field in layer.fields()])
    by_name = {field.name(): field for field in layer.fields()}
    integers = (QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.ULongLong)
    for key in ("ESTACION", "RUMBO"):
        if by_name[fields[key]].type() != QVariant.String:
            raise ValueError("El campo {} debe ser texto.".format(fields[key]))
    for key in ("DISTANCIA", "COTA"):
        if key in fields and by_name[fields[key]].type() not in (QVariant.Double,) + integers:
            raise ValueError("El campo {} debe ser numérico.".format(fields[key]))
    return [tuple(feature[fields[key]] if key in fields else None
                  for key in ("ESTACION", "DISTANCIA", "RUMBO", "COTA"))
            for feature in layer.getFeatures()]


def normalize_bearing_rows(rows, allow_empty_cota=False):
    """Require 1-2, 2-3, ..., N-1; sort by the integer origin station."""
    ordered = {}
    for index, row in enumerate(rows, 1):
        if len(row) != 4:
            raise ValueError("Registro {}: se requieren estación, distancia, rumbo y cota opcional.".format(index))
        label, distance, bearing, cota = row
        match = re.fullmatch(r"\s*([1-9]\d*)\s*[-–]\s*([1-9]\d*)\s*", str(label))
        if not match:
            raise ValueError("Registro {}: ESTACION debe ser texto con el tramo, por ejemplo 1-2.".format(index))
        start, end = map(int, match.groups())
        if start in ordered:
            raise ValueError("La estación de origen {} está duplicada.".format(start))
        location = "Tramo {}-{}".format(start, end)
        azimuth = parse_quadrant_bearing(bearing, location)
        length = parse_coordinate(distance, "DISTANCIA", label, location)
        if length <= 0:
            raise ValueError("{}: DISTANCIA debe ser mayor que cero.".format(location))
        z = parse_coordinate(cota, "COTA", label, location, optional=True)
        ordered[start] = (end, (str(start), azimuth, length, z), str(bearing).strip())
    count = len(ordered)
    if count < 3:
        raise ValueError("Se requieren al menos tres tramos del contorno, incluido N-1.")
    for start in range(1, count + 1):
        if start not in ordered:
            raise ValueError("Falta el tramo que inicia en EST {}.".format(start))
        expected = start + 1 if start < count else 1
        if ordered[start][0] != expected:
            raise ValueError("Tramo {}-{}: se esperaba {}-{}. Revise la secuencia del recorrido.".format(
                start, ordered[start][0], start, expected))
    records = [ordered[i][1] for i in range(1, count + 1)]
    validate_cota_completeness(records, allow_empty_cota)
    bearings = [ordered[i][2] for i in range(1, count + 1)]
    return records, bearings


def calculate_bearing_traverse(rows, initial_north=0, initial_east=0, minimum_precision=None,
                               adjust_linear=False, allow_empty_cota=False):
    records, bearings = normalize_bearing_rows(rows, allow_empty_cota)
    north = parse_coordinate(initial_north, "NORTE inicial", "1", "control")
    east = parse_coordinate(initial_east, "ESTE inicial", "1", "control")
    if minimum_precision is not None:
        minimum_precision = parse_coordinate(minimum_precision, "Precisión mínima", "1", "criterio")
        if minimum_precision <= 0:
            raise ValueError("La precisión mínima 1:N debe ser positiva.")
    if adjust_linear and minimum_precision is None:
        raise ValueError("Indique la precisión mínima antes de aplicar Bowditch.")
    raw = _project_traverse(records, [r[1] for r in records], north, east)
    linear_ok = None if minimum_precision is None else raw["error"] <= raw["perimeter"] / minimum_precision
    warnings = []
    if linear_ok is False:
        warnings.append("El cierre lineal no cumple la precisión mínima indicada; no se aplica Bowditch.")
    if minimum_precision is None:
        warnings.append("Sin criterio lineal indicado: no se determina aceptación del cierre.")
    warnings.append("No se evalúa cierre angular: se recibieron rumbos de los lados, no ángulos interiores observados. "
                    "Si los rumbos provienen de coordenadas o de un plano, el cierre verifica esa tabla.")
    linear_applied = bool(adjust_linear and linear_ok)
    corrections = [(0., 0.)] * len(records)
    adjusted_points = adjusted_deltas = adjusted_area = None
    if linear_applied:
        corrections = [(-raw["dn"] * r[2] / raw["perimeter"], -raw["de"] * r[2] / raw["perimeter"])
                       for r in records]
        adjusted_deltas = [(n + cn, e + ce) for (n, e), (cn, ce) in zip(raw["deltas"], corrections)]
        adjusted_points = [raw["points"][0]]
        for index, point in enumerate(raw["points"][1:]):
            adjusted_points.append((point[0], north + math.fsum(v[0] for v in adjusted_deltas[:index + 1]),
                                    east + math.fsum(v[1] for v in adjusted_deltas[:index + 1]), point[3]))
        adjusted_points[-1] = ("1 (ajustada)", north, east, records[0][3])
        try:
            adjusted_area = _closed_traverse_area(adjusted_points)
        except ValueError as exc:
            warnings.append(str(exc) + " No se presenta área ajustada de un contorno inválido.")
    return {"input_kind": "bearings", "records": records, "original_bearings": bearings,
            "initial_north": north, "initial_east": east, "initial_azimuth": records[0][1],
            "angles_applied": False, "linear_applied": linear_applied, "minimum_precision": minimum_precision,
            "linear_ok": linear_ok, "linear_corrections": corrections, "raw": raw, "working": raw,
            "adjusted_points": adjusted_points, "adjusted_deltas": adjusted_deltas,
            "adjusted_area": adjusted_area, "warnings": warnings}


def _raw_csv_download(headers, rows, filename, label):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    data = base64.b64encode(b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")).decode("ascii")
    return ('<a class="download" download="{}" href="data:text/csv;charset=utf-8;base64,{}">{}</a>').format(
        html.escape(filename, quote=True), data, html.escape(label))


def _observed_traverse_svg(points, area=None, closed=False):
    """Draw the measured open arrival, or the balanced closed polygon."""
    plotted = points[:-1] if closed else points
    segments = [(point[0], point[0], "", point[1], point[2]) for point in plotted]
    # An area is only labelled for an adjusted valid contour.
    svg, omitted = _polygon_svg(segments, area or 0.0)
    diagnostics = {"omitted_labels": omitted}
    if not closed:
        svg = re.sub(r'<polygon points="([^"]+)"[^>]*/>',
                     r'<polyline points="\1" fill="none" stroke="#1769aa" stroke-width="3"/>', svg)
    if area is None:
        svg = re.sub(r'<g class="area-label">.*?</g>', '', svg)
    return svg, diagnostics["omitted_labels"]


def build_raw_traverse_html(result, source, crs, author=""):
    """A separate report preserving original observations and preadjustment closure."""
    def esc(value):
        return html.escape(str(value), quote=True)

    def numeric(value, digits=3):
        return ("{:,." + str(digits) + "f}").format(value)

    def status(value):
        return "No evaluado" if value is None else "Cumple el criterio indicado" if value else "No cumple"

    def ratio(stage):
        if stage["error"] <= 1e-10:
            return "Sin residuo detectable a precisión numérica"
        return "1/" + numeric(stage["precision"])

    def table(headers, rows):
        return ('<div class="table-scroll"><table><thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>').format(
            "".join("<th>{}</th>".format(esc(header)) for header in headers),
            "".join("<tr>{}</tr>".format("".join("<td>{}</td>".format(esc(cell)) for cell in row)) for row in rows))

    def stage_table(stage):
        return table(("Indicador", "Resultado"), (
            ("St / ΔN (llegada − control)", numeric(stage["dn"], 9) + " m"),
            ("Sg / ΔE (llegada − control)", numeric(stage["de"], 9) + " m"),
            ("Distancia horizontal acumulada P", numeric(stage["perimeter"]) + " m"),
            ("Error de cierre", numeric(stage["error"], 9) + " m"),
            ("Error relativo", numeric(stage["relative_error"], 13)),
            ("Precisión lineal", ratio(stage))))

    bearings_mode = result.get("input_kind") == "bearings"
    records = result["records"]
    raw, working = result["raw"], result["working"]
    angle_rows = [] if bearings_mode else [(record[0], _report_angle_dms(record[1]), numeric(record[2]),
                                            "Vacía" if record[3] is None else numeric(record[3]),
                                            numeric(correction * 3600, 6), _report_angle_dms(angle))
                                           for record, correction, angle in
                                           zip(records, result["angle_corrections"], result["corrected_angles"])]
    if bearings_mode:
        observation_headers = ("ESTACION", "DISTANCIA", "RUMBO", "COTA")
        observation_rows = [(r[0] + "-" + records[(i + 1) % len(records)][0], repr(r[2]), bearing,
                             "" if r[3] is None else repr(r[3]))
                            for i, (r, bearing) in enumerate(zip(records, result["original_bearings"]))]
        angle_rows = [(row[0], numeric(r[2]), bearing, "Vacía" if r[3] is None else numeric(r[3]))
                      for row, r, bearing in zip(observation_rows, records, result["original_bearings"])]
    else:
        observation_headers = ("EST", "ANGULO", "DISTANCIA", "COTA")
        observation_rows = [(r[0], repr(r[1]), repr(r[2]), "" if r[3] is None else repr(r[3])) for r in records]
    observed_csv = _raw_csv_download(observation_headers, observation_rows,
                                     "observaciones_poligonal.csv", "Descargar observaciones originales")
    projection_rows = []
    delivery_segments = []
    for index, record in enumerate(records):
        a, b = working["points"][index:index + 2]
        end = "1′" if index == len(records) - 1 else records[index + 1][0]
        dn, de = working["deltas"][index]
        cn, ce = result["linear_corrections"][index]
        next_cota = records[(index + 1) % len(records)][3]
        dz = None if record[3] is None else next_cota - record[3]
        slope = None if dz is None else math.hypot(record[2], dz)
        projection_rows.append((record[0] + "-" + end, _report_angle_dms(working["azimuths"][index]),
                                numeric(record[2]), _bearing(dn, de), numeric(dn, 6), numeric(de, 6),
                                numeric(cn, 6), numeric(ce, 6),
                                "No disponible" if dz is None else numeric(dz),
                                "No disponible" if slope is None else numeric(slope)))
        delivery_segments.append((record[0] + "-" + end, record[0], end, a[1], a[2], b[1], b[2],
                                  dn, de, record[2], _bearing(dn, de)))

    def coordinate_section(stage, title, filename):
        rows = [(p[0], numeric(p[1], 6), numeric(p[2], 6),
                 "Vacía" if p[3] is None else numeric(p[3])) for p in stage["points"]]
        download = _raw_csv_download(
            ("EST", "NORTE", "ESTE", "COTA", "ETIQUETA"),
            [(str(i), format(p[1], '.9f'), format(p[2], '.9f'),
              '' if p[3] is None else format(p[3], '.6f'), p[0]) for i, p in enumerate(stage["points"], 1)],
            filename, "Descargar coordenadas calculadas")
        return ('<section><h2>{}</h2><p>{}</p>{}<small>1′ es la llegada calculada al control inicial; '
                'se conserva su diferencia respecto de EST 1. ETIQUETA identifica el punto y EST enumera '
                'las filas exportadas, incluida la llegada. Esa llegada no es otro vértice del lindero.</small>'
                '</section>').format(esc(title), download,
                                     table(("Estación", "NORTE (m)", "ESTE (m)", "COTA (m)"), rows))

    coordinates_sections = coordinate_section(raw, "Coordenadas calculadas sin ajustar", "coordenadas_sin_ajuste.csv")
    if result["angles_applied"]:
        coordinates_sections += coordinate_section(
            working, "Coordenadas después del ajuste angular, antes de Bowditch", "coordenadas_ajuste_angular.csv")
    adjusted_section = ""
    if result["adjusted_points"] is not None:
        points = result["adjusted_points"]
        area = result["adjusted_area"]
        adjusted_csv = _raw_csv_download(
            ("EST", "NORTE", "ESTE", "COTA", "ETIQUETA"),
            [(str(i), format(p[1], '.9f'), format(p[2], '.9f'),
              '' if p[3] is None else format(p[3], '.6f'), p[0]) for i, p in enumerate(points, 1)],
            "coordenadas_bowditch.csv", "Descargar coordenadas ajustadas")
        adjusted_rows = [(p[0], numeric(p[1], 6), numeric(p[2], 6),
                          "Vacía" if p[3] is None else numeric(p[3])) for p in points]
        area_text = _format_area(area) if area is not None else "No disponible: geometría inválida"
        adjusted_section = ('<section><h2>Coordenadas ajustadas por Bowditch</h2><p>{}</p>{}'
                            '<p>Área por coordenadas ajustadas: <b>{}</b></p>'
                            '<small>El cierre algebraico después del ajuste no reemplaza el error observado, '
                            'que se conserva arriba. No se ajustan las cotas.</small></section>').format(
                                adjusted_csv, table(("Estación", "NORTE (m)", "ESTE (m)", "COTA (m)"),
                                                    adjusted_rows), area_text)
        if area is not None:
            adjusted_segments = []
            for index in range(len(records)):
                a, b = points[index:index + 2]
                dn, de = result["adjusted_deltas"][index]
                end = records[(index + 1) % len(records)][0]
                adjusted_segments.append((records[index][0] + "-" + end, records[index][0], end,
                                          a[1], a[2], b[1], b[2], dn, de, math.hypot(dn, de), _bearing(dn, de)))
            adjusted_section += _delivery_table(
                adjusted_segments, "tramos_bowditch_distancias_rumbos.csv").replace(
                    "<h2>Tabla para entrega</h2>", "<h2>Tramos del contorno ajustado</h2>")
    angular_stage = ('<section><h2>Cierre lineal después del ajuste angular, antes de Bowditch</h2>{}'
                     '<small>Estos indicadores permiten evaluar el cierre lineal después de equilibrar '
                     'los ángulos y antes de corregir las proyecciones.</small></section>').format(stage_table(working))
    if not result["angles_applied"]:
        angular_stage = ""
    if bearings_mode:
        criteria_table = table(("Comprobación", "Criterio indicado", "Resultado"), (
            ("Precisión lineal de rumbos y distancias",
             "No indicada" if result["minimum_precision"] is None else "1/" + numeric(result["minimum_precision"]),
             status(result["linear_ok"])),))
    else:
        criteria_table = table(("Comprobación", "Criterio indicado", "Resultado"), (
            ("Error angular", "No indicado" if result["max_angular_seconds"] is None else
             numeric(result["max_angular_seconds"], 3) + ' segundos', status(result["angular_ok"])),
            ("Precisión lineal tras balance angular" if result["angles_applied"] else
             "Precisión lineal sin balance angular",
             "No indicada" if result["minimum_precision"] is None else "1/" + numeric(result["minimum_precision"]),
             status(result["linear_ok"]))))
    if result.get("angular_coefficient") is not None:
        coefficient_text = numeric(result["angular_coefficient"], 3)
        tolerance_text = numeric(result["max_angular_seconds"], 3)
        criteria_table += ('<p>Coeficiente configurado a = {}″; tolerancia angular a√n = {}″. '
                           'Este criterio lo define el proyecto y no se deduce automáticamente de la precisión '
                           'nominal del instrumento.</p>').format(coefficient_text, tolerance_text)
    warnings = ''.join('<aside class="notice">{}</aside>'.format(esc(message))
                       for message in result["warnings"])
    raw_chart, omitted = _observed_traverse_svg(raw["points"])
    chart_notice = ('<aside class="notice">Se omitieron {} etiquetas para evitar superposiciones; '
                    'todos los puntos están en las tablas y el dibujo.</aside>').format(omitted) if omitted else ""
    adjusted_chart = ""
    if result["adjusted_area"] is not None:
        svg, count = _observed_traverse_svg(result["adjusted_points"], result["adjusted_area"], closed=True)
        adjusted_chart = ('<section><h2>Plano del contorno ajustado</h2><div class="chart">{}</div>'
                          '<small>{} etiquetas omitidas; consulte los puntos en la tabla.</small></section>').format(
                              svg, count)
    elevations = [r[3] for r in records if r[3] is not None]
    vertical = '<p>COTA completamente vacía: se calculó solo en planta.</p>'
    if elevations:
        slope = math.fsum(math.hypot(records[i][2], records[(i + 1) % len(records)][3] - records[i][3])
                          for i in range(len(records)))
        vertical = ('<p>Cota mínima: <b>{} m</b> · Cota máxima: <b>{} m</b> · '
                    'Desnivel EST 1–EST N: <b>{:+.3f} m</b> · '
                    'Distancia inclinada calculada: <b>{} m</b>.</p>'
                    '<small>Se usan las cotas de los vértices; el último tramo usa la cota conocida de EST 1. '
                    'No se evalúa cierre altimétrico: no hay una nueva cota observada al regresar al control. '
                    'La distancia inclinada calculada no sustituye una observación del instrumento.</small>').format(
                        numeric(min(elevations)), numeric(max(elevations)), records[-1][3] - records[0][3],
                        numeric(slope))
    if bearings_mode:
        control = table(("Dato de control", "Valor"), (
            ("NORTE inicial", numeric(result["initial_north"], 6) + " m"),
            ("ESTE inicial", numeric(result["initial_east"], 6) + " m"),
            ("Direcciones", "Rumbo independiente de cada tramo"),
            ("Ajuste lineal aplicado", "Sí, Bowditch" if result["linear_applied"] else "No")))
        angular_table = ""
    else:
        control = table(("Dato de control", "Valor"), (
            ("NORTE inicial", numeric(result["initial_north"], 6) + " m"),
            ("ESTE inicial", numeric(result["initial_east"], 6) + " m"),
            ("Azimut inicial 1-2, desde norte en sentido horario", _report_angle_dms(result["initial_azimuth"])),
            ("Sentido del recorrido", "Horario" if result["clockwise"] else "Antihorario"),
            ("Ajuste angular aplicado", "Sí, igual peso por ángulo" if result["angles_applied"] else "No"),
            ("Ajuste lineal aplicado", "Sí, Bowditch" if result["linear_applied"] else "No")))
        angular_table = table(("Indicador angular", "Resultado"), (
            ("Suma de ángulos observados", _report_angle_dms(result["observed_sum"])),
            ("Suma teórica (N − 2) × 180°", _report_angle_dms(result["theoretical_sum"])),
            ("Error angular (observada − teórica)", '{:+.6f} segundos'.format(result["angular_seconds"])),
            ("Corrección uniforme aplicada por ángulo",
             '{:+.6f} segundos'.format(result["angle_corrections"][0] * 3600))))
    # Reuse the existing blue report styles and zoom conventions.
    style = '''body{font:15px system-ui,Arial;margin:0;background:#f3f7fb;color:#193b58}
main{max-width:1180px;margin:2rem auto;padding:0 1.25rem 2rem}
header{background:linear-gradient(90deg,#193b58,#1769aa);color:white;padding:1.5rem;border-radius:12px}
h1{font-size:1.7rem}h2{color:#193b58;border-bottom:2px solid #1769aa;padding-bottom:.4rem;font-size:1.15rem}
section{background:white;padding:1.35rem;margin-top:1.2rem;border-radius:10px}
.notice{background:#fef5e7;border-left:4px solid #e67e22;padding:1rem;margin:1rem 0;color:#7d5a22}
.table-scroll,.chart{overflow:auto}table{border-collapse:collapse;width:100%;margin-top:.7rem}
th,td{border:1px solid #d8e6f0;padding:.55rem;text-align:center;white-space:nowrap}
th{background:#193b58;color:white}.area-line{display:block;white-space:nowrap}
tr:nth-child(even){background:#f8fbfe}small{display:block;margin-top:1rem;line-height:1.5;color:#617d92}
.download{display:inline-block;background:#1769aa;color:white;text-decoration:none;padding:.7rem;border-radius:6px}
svg{width:100%;height:auto;border:1px solid #d8e6f0;border-radius:8px;box-sizing:border-box}
svg .grid{stroke:#e9f1f7;stroke-width:1}svg .axis{fill:#193b58;font:bold 14px system-ui}
svg .tick{fill:#617d92;font:11px system-ui}svg .vertex circle{fill:#1769aa;stroke:white;stroke-width:1.5}
svg .point-label{fill:#193b58;font:bold 12px system-ui;paint-order:stroke;stroke:white;stroke-width:3px}
svg .area-label rect{fill:#e9f4fc;stroke:#1769aa;stroke-width:1}svg .area-label text{font:bold 13px system-ui}
.chart-tools{display:flex;gap:.6rem;justify-content:flex-end;margin-bottom:.7rem}.chart-tools input{width:150px}
@media print{main{margin:0;max-width:none}.chart-tools{display:none}.chart svg{width:100%!important}}
'''
    zoom = '''<label class="chart-tools">Tamaño del plano
<input type="range" min="-2" max="2" step="0.25" value="0" aria-label="Ampliación del plano"
oninput="var z=100*Math.pow(2,Number(this.value));this.closest('section').querySelector('svg').style.width=z+'%';
this.parentElement.querySelector('output').textContent=Math.round(z)+'%'"><output>100%</output></label>'''
    if adjusted_chart:
        adjusted_chart = adjusted_chart.replace('<div class="chart">', zoom + '<div class="chart">', 1)
    projection_headers = ("Estación", "Azimut (° ′ ″)", "Distancia (m)", "Rumbo",
                          "ΔN antes de Bowditch (m)", "ΔE antes de Bowditch (m)",
                          "C_N (m)", "C_E (m)", "ΔCota (m)", "Distancia inclinada calculada (m)")
    angular_section = "" if bearings_mode else (
        '<section><h2>Cierre angular observado</h2>{}</section>').format(angular_table)
    input_note = ""
    if bearings_mode:
        input_note = ('<p>Entrada: ESTACION (texto 1-2, 2-3, …, N-1), DISTANCIA horizontal en metros, '
                      'RUMBO cuadrantal y COTA opcional. No se añade un lado de cierre. '
                      'Las coordenadas iniciales ubican el dibujo; si ambas se dejan vacías se usa un origen local.</p>'
                      '<small>Las direcciones deben compartir referencia norte. No se convierten rumbos magnéticos '
                      'o verdaderos a norte de cuadrícula ni distancias terreno/cuadrícula automáticamente. '
                      'No se calcula cierre angular con esta entrada.</small>')
    angle_headers = (("ESTACION", "Distancia (m)", "Rumbo observado (° ′ ″)", "Cota (m)") if bearings_mode else
                     ("EST", "Ángulo observado (° ′ ″)", "Distancia (m)", "Cota (m)",
                      "Corrección angular (″)", "Ángulo utilizado (° ′ ″)"))
    report_html = ('<!doctype html><html lang="es"><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width, initial-scale=1">'
                   '<title>Cierre observado de poligonal</title>'
                   '<style>{style}</style><main><header><h1>📐 Cierre observado de poligonal</h1>'
                   '<p>Desarrollado por: Raúl Víquez | viquezr@gmail.com</p><p>Archivo: {source} · CRS: {crs} · '
                   'Autor: {author}</p></header>{warnings}'
                   '<section><h2>Control y convención de entrada</h2>{control}'
                   '<p>ANGULO es el ángulo interior en grados decimales de la estación. DISTANCIA es horizontal '
                   'y corresponde al tramo hacia la siguiente estación; la última fila contiene el tramo observado '
                   'hacia EST 1. '
                   'No se añade un lado artificial ni se fuerza la llegada sin ajustar.</p>'
                   '<small>90.0050 significa 90.005 grados, no 90°00′50″. Se requieren todos los ángulos interiores, '
                   'incluido el de EST 1. Los ángulos de círculo, exteriores, cenitales y distancias inclinadas '
                   'deben convertirse antes de importarlos. Los datos deben estar referidos a la cuadrícula del CRS: '
                   'no se aplican factores terreno/cuadrícula ni convergencia meridiana '
                   'automáticamente.</small></section>'
                   '<section><h2>Observaciones y correcciones angulares</h2><p>{observed_csv}</p>{observations}'
                   '<small>Ángulos y azimuts en grados, minutos y segundos; segundos con cuatro decimales. '
                   'Las correcciones angulares se expresan en segundos de arco. El CSV original conserva '
                   'los grados decimales y los cálculos usan la precisión completa.</small></section>'
                   '{angular_section}'
                   '<section><h2>Cierre lineal con observaciones sin ajustar</h2>{raw_table}'
                   '<small>Se propagan los ángulos interiores desde el azimut del primer tramo. '
                   'Las proyecciones usan las distancias horizontales observadas sin redondeos '
                   'intermedios.</small></section>'
                   '{angular_stage}<section><h2>Comprobación de los criterios indicados</h2>{criteria}'
                   '<small>Los criterios pertenecen al proyecto; el complemento no asigna una norma de aceptación. '
                   'Un cumplimiento lineal y angular no certifica por sí solo la aprobación '
                   'catastral.</small></section>'
                   '<section><h2>Azimuts y proyecciones de la etapa de cálculo</h2>{projections}'
                   '<small>La etapa usa ángulos corregidos solo si se aplicó su ajuste; las '
                   'columnas C_N y C_E muestran '
                   'correcciones Bowditch solo si se aplicó. Estas correcciones no modifican las observaciones '
                   'originales.</small>'
                   '</section>{coordinates_sections}{adjusted_section}'
                   '<section><h2>Cotas y distancia inclinada calculada</h2>{vertical}</section>'
                   '<section><h2>Recorrido observado sin ajustar</h2>{zoom}{chart_notice}<div '
                   'class="chart">{raw_chart}</div>'
                   '<small>El dibujo muestra la llegada 1′; no cierra automáticamente el recorrido ni calcula un área '
                   'cerrándolo con un lado añadido. Se conserva la proporción entre ejes.</small></section>'
                   '{adjusted_chart}{delivery}</main></html>').format(
                style=style, source=esc(source), crs=esc(crs),
                author=esc(author.strip()) if author.strip() else 'No indicado',
                warnings=warnings, control=control, observed_csv=observed_csv,
                observations=table(angle_headers, angle_rows),
                angular_section=angular_section, raw_table=stage_table(raw), angular_stage=angular_stage,
                criteria=criteria_table, projections=table(projection_headers, projection_rows),
                coordinates_sections=coordinates_sections,
                adjusted_section=adjusted_section, vertical=vertical, zoom=zoom, chart_notice=chart_notice,
                raw_chart=raw_chart, adjusted_chart=adjusted_chart,
                delivery=_delivery_table(delivery_segments, "tramos_observados_distancias_rumbos.csv").replace(
                    "<h2>Tabla para entrega</h2>", "<h2>Tramos antes de Bowditch</h2>"))

    if bearings_mode:
        if result["adjusted_area"] is not None:
            summary_area = _format_area(result["adjusted_area"])
        elif result["linear_applied"]:
            summary_area = "No disponible: contorno ajustado inválido."
        else:
            summary_area = "No disponible: Bowditch no aplicado."
        summary_rows = [
            ("Perímetro observado", numeric(raw["perimeter"]) + " m"),
            ("Error de cierre observado", numeric(raw["error"], 9) + " m"),
            ("Error relativo", numeric(raw["relative_error"], 13)),
            ("Precisión lineal observada", ratio(raw)),
            ("Criterio lineal", status(result["linear_ok"])),
            ("Ajuste Bowditch", "Aplicado" if result["linear_applied"] else "No aplicado")]
        summary = ('<section><h2>Resumen de resultados</h2>'
                   '<p><strong>Área final por coordenadas ajustadas:</strong></p><p><b>{}</b></p>{}'
                   '<small>El error y la precisión corresponden a las observaciones antes de Bowditch. '
                   'El área final requiere ajuste aplicado y un contorno válido.</small></section>').format(
                       summary_area, table(("Indicador", "Resultado"), summary_rows))
        report_html = report_html.replace("</header>", "</header>" + summary, 1)
        report_html = report_html.replace("Cierre observado de poligonal", "Cierre por rumbos y distancias")
        report_html = report_html.replace("Observaciones y correcciones angulares", "Rumbos y distancias originales")
        report_html = re.sub(r'<p>ANGULO es.*?</small>', input_note, report_html, count=1, flags=re.S)
        report_html = re.sub(r'<small>Ángulos y azimuts.*?</small>',
                             '<small>Azimuts en grados, minutos y segundos; se conserva el rumbo original. '
                             'Cálculos con precisión completa.</small>', report_html, count=1, flags=re.S)
        report_html = report_html.replace(
            "Se propagan los ángulos interiores desde el azimut del primer tramo.",
            "Se proyecta cada rumbo con su distancia horizontal observada.")
        report_html = report_html.replace("Un cumplimiento lineal y angular", "Un cumplimiento lineal")
        report_html = report_html.replace(
            "La etapa usa ángulos corregidos solo si se aplicó su ajuste; las columnas C_N y C_E muestran ",
            "Las proyecciones ΔN y ΔE proceden de los rumbos originales; las columnas C_N y C_E muestran ")
    return report_html


def process_observations(observations, start_n, start_e, start_azimuth_deg,
                         direction, angular_precision_sec=10.0, linear_tolerance_den=10000.0,
                         evaluate_criteria=False, adjust_angles=False, adjust_linear=False,
                         allow_empty_cota=False):
    """Analyze measured observations, preserving each preadjustment stage."""
    directions = {"ccw": False, "antihorario": False, "counterclockwise": False, "levógiro": False,
                  "cw": True, "horario": True, "clockwise": True, "dextrógiro": True}
    if str(direction).lower() not in directions:
        raise ValueError("Sentido desconocido: {}".format(direction))
    rows = [(obs[0], obs[1], obs[2], obs[3]) for obs in observations]
    maximum = None
    minimum = None
    if evaluate_criteria:
        coefficient = parse_coordinate(angular_precision_sec, "Coeficiente angular a", "control", "criterio")
        if coefficient <= 0:
            raise ValueError("El coeficiente angular a debe ser mayor que cero.")
        maximum = coefficient * math.sqrt(len(rows))
        minimum = linear_tolerance_den
    result = calculate_raw_traverse(
        rows, start_n, start_e, start_azimuth_deg, directions[str(direction).lower()],
        maximum, minimum, adjust_angles, adjust_linear, allow_empty_cota)
    result["angular_coefficient"] = angular_precision_sec if evaluate_criteria else None
    return result


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


def _area_parts(area):
    """Show rounded square metres and canonical hectares plus remainder."""
    total_thousandths = round(abs(area) * 1000)
    hectares, remainder_thousandths = divmod(total_thousandths, 10_000_000)
    return ("{:,.3f} m²".format(total_thousandths / 1000),
            "{:,} ha + {:,.3f} m²".format(hectares, remainder_thousandths / 1000))


def _format_area(area, signed=False):
    square_metres, decomposition = _area_parts(area)
    if area < 0 and round(abs(area) * 1000):
        square_metres = "−" + square_metres
        decomposition = "−({})".format(decomposition)
    elif signed and round(abs(area) * 1000):
        square_metres = "+" + square_metres
    return ('<span class="area-line">{}</span>'
            '<span class="area-line">({})</span>').format(square_metres, decomposition)


def _polygon_svg(segments, area):
    """Plano independiente, escalado en proporción a metros del CRS proyectado."""
    points = [(segment[4], segment[3]) for segment in segments]
    point_ids = [str(segment[1]) for segment in segments]
    east = [p[0] for p in points]
    north = [p[1] for p in points]
    min_e, max_e = min(east), max(east)
    min_n, max_n = min(north), max(north)
    width, height = 1500, 900
    left, top, right, bottom = 82, 55, 62, 74
    span_e = max(max_e - min_e, 1e-6)
    span_n = max(max_n - min_n, 1e-6)
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
    omitted_labels = 0
    directions = ((1, -1), (1, 1), (-1, -1), (-1, 1),
                  (1, 0), (-1, 0), (0, -1), (0, 1))
    for index, ((x, y), (e, n)) in enumerate(zip(coords, points), 1):
        point_id = str(point_ids[index - 1])
        title = html.escape("Punto {} · E {:.3f} m · N {:.3f} m".format(
            point_id, e, n), quote=True)
        circles.append(
            '<g class="vertex"><circle cx="{:.2f}" cy="{:.2f}" r="5">'
            '<title>{}</title></circle></g>'.format(x, y, title))
        label_width = 9 + 8 * len(point_id)
        for radius in (12, 20, 28, 38, 48):
            for sx, sy in directions:
                lx = x + (radius if sx > 0 else -radius - label_width if sx < 0
                          else -label_width / 2)
                ly = y + (radius if sy > 0 else -radius / 2 if sy < 0 else 4)
                box = (lx - 2, ly - 13, lx + label_width, ly + 3)
                if (box[0] < left or box[2] > width - right
                        or box[1] < top + 4 or box[3] > height - bottom):
                    continue
                if any(box[0] - 3 < b[2] and box[2] + 3 > b[0]
                       and box[1] - 3 < b[3] and box[3] + 3 > b[1] for b in occupied):
                    continue
                if any(box[0] - 4 < px < box[2] + 4
                       and box[1] - 4 < py < box[3] + 4
                       for point_index, (px, py) in enumerate(coords, 1)
                       if point_index != index):
                    continue
                labels.append('<text class="point-label" x="{:.2f}" y="{:.2f}">{}</text>'.format(
                    lx, ly, html.escape(point_id)))
                occupied.append(box)
                break
            else:
                continue
            break
        else:
            omitted_labels += 1
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
    shape = ('<polygon points="{}" fill="#2d8bd0" fill-opacity=".20" '
             'stroke="#1769aa" stroke-width="3" stroke-linejoin="round"/>'.format(path))
    square_metres, decomposition = _area_parts(area)
    area_label = ('<g class="area-label"><rect x="{:.0f}" y="6" width="310" '
                  'height="42" rx="7"/><text x="{:.0f}" y="22" '
                  'text-anchor="middle">Área: {}</text><text x="{:.0f}" y="39" '
                  'text-anchor="middle">{}</text></g>'.format(
                      width / 2 - 155, width / 2, square_metres,
                      width / 2, decomposition))
    svg = ('<svg viewBox="0 0 {width} {height}" role="img" '
           'aria-label="Plano del polígono con puntos numerados y ejes proyectados">'
           '<rect x="0" y="0" width="{width}" height="{height}" fill="#f8fbfe"/>'
           '<g>{ticks}</g>{shape}{circles}{labels}{area}'
           '<text x="{mid_x}" y="{axis_y}" text-anchor="middle" class="axis">Este (m)</text>'
           '<text transform="translate(17 {mid_y}) rotate(-90)" text-anchor="middle" '
           'class="axis">Norte (m)</text>'
           '<text x="{north_x}" y="40" class="axis" text-anchor="middle">N ↑</text>'
           '</svg>').format(width=width, height=height, ticks="".join(ticks),
                            shape=shape, circles="".join(circles),
                            labels="".join(labels), area=area_label,
                            mid_x=width / 2, mid_y=height / 2,
                            axis_y=height - 13, north_x=width - right)
    return svg, omitted_labels


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
               pre_summary=None, full_summary=None, delivery_filename=None,
               observation_block=""):
    def esc(value):
        return html.escape(str(value), quote=True)

    def fmt(value):
        if abs(value) < 0.0005:
            value = 0.0
        return "{:,.3f}".format(value)
    rows = []
    for s in segments:
        rows.append("<tr><td>{}</td><td>{}</td><td>{}</td>".format(
            esc(s[0]), esc(s[1]), esc(s[2]))
            + "".join("<td class='num'>{}</td>".format(fmt(v)) for v in s[3:10])
            + "<td>{}</td></tr>".format(esc(s[10])))

    def fmt_small(value):
        if abs(value) < 0.5e-13:
            value = 0.0
        return "{:.13f}".format(value)
    precision = fmt(summary["precision"]) if math.isfinite(summary["precision"]) else "∞"
    shortest = min(segments, key=lambda segment: segment[9])
    longest = max(segments, key=lambda segment: segment[9])
    equal_lengths = abs(shortest[9] - longest[9]) < 0.0005
    equal_note = (" <b>(todos los tramos miden igual)</b>"
                  if equal_lengths else "")
    area_difference = summary["corrected_area"] - summary["raw_area"]
    area_difference_text = _format_area(area_difference, signed=True)
    author_html = "<span>Autor: {}</span>".format(esc(author.strip())) if author.strip() else ""
    chart, omitted_labels = _polygon_svg(segments, summary["corrected_area"])
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
            '<small>Estos componentes describen los extremos en orden de PUNTO, '
            'antes de añadir la primera coordenada. Cuando el '
            'último registro es otro vértice del contorno, e representa el lado '
            'faltante, no el error de cierre de una observación repetida. '
            '<b>Solo si el último registro reobserva el control inicial, la '
            'precisión topográfica se expresa como 1:(P/e).</b></small>'
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
                dz=(pre_summary if pre_summary is not None else full_summary)["net_dz"])
    elif full_summary is not None and full_summary["min_z"] is None:
        vertical_section = (
            '<section><h2>Cotas</h2><p>La columna COTA está vacía en todos '
            'los puntos. El informe contiene cálculos en planta; no se '
            'calculan desniveles ni distancias inclinadas.</p></section>')
    elif full_summary is not None:
        vertical_section = (
            '<section><h2>Cotas parciales</h2><p>Cota mínima: '
            '<b>{min_z:,.3f} m</b> · Cota máxima: <b>{max_z:,.3f} m</b>. '
            'Cobertura: <b>{covered} de {total} puntos</b> tienen cota.</p>'
            '<small>Algunos puntos no tienen cota. Por eso no se calculan '
            'distancias inclinadas ni desniveles del perímetro. Complete las '
            'cotas faltantes si necesita esos valores.</small></section>').format(
                min_z=full_summary["min_z"], max_z=full_summary["max_z"],
                covered=full_summary["cota_count"],
                total=full_summary["cota_total"])
    label_note = (
        " No se pudieron dibujar {} etiquetas por falta de espacio; "
        "pase el cursor sobre el punto para consultar su número.".format(
            omitted_labels) if omitted_labels else "")
    precision_short = ("∞ (e = 0)" if not math.isfinite(summary["precision"])
                       else "1/" + precision)
    return """<!doctype html><html lang="es"><meta charset="utf-8">
<title>Cierre geométrico</title><style>
body{{font:15px system-ui,Arial;margin:0;background:#f3f7fb;color:#193b58}}
main{{max-width:1180px;margin:2rem auto;padding:0 1.25rem 2rem}}
header{{background:linear-gradient(90deg,#193b58 0%,#1769aa 100%);
 color:white;padding:1.5rem 2rem;border-radius:12px;
 box-shadow:0 4px 14px rgba(25,59,88,0.18)}}
h1{{margin:.2rem 0;font-size:1.7rem;letter-spacing:.2px}}
h2{{margin:0 0 .9rem;color:#193b58;border-bottom:2px solid #1769aa;
 padding-bottom:.4rem;font-size:1.15rem}}
.developer{{font-style:italic;opacity:.95;margin:.15rem 0 .7rem;
 font-size:.95rem;color:#e9f1f7}}
.meta{{display:flex;gap:1.5rem;flex-wrap:wrap;opacity:.92;font-size:.92rem}}
.summary{{display:flex;gap:1rem;flex-wrap:wrap;margin:1.2rem 0}}
.card{{background:white;border-left:4px solid #1769aa;padding:1rem 1.3rem;
 border-radius:8px;box-shadow:0 2px 10px rgba(25,59,88,0.08);
 flex:1;min-width:170px;color:#193b58}}
.card b{{display:block;margin-top:.4rem;font-size:1.2rem;color:#1769aa}}
.notice{{background:#fef5e7;border-left:4px solid #e67e22;
 padding:1rem 1.2rem;margin:1rem 0;border-radius:5px;line-height:1.5;
 color:#7d5a22}}
section{{background:white;padding:1.35rem;margin-top:1.2rem;border-radius:10px;
 box-shadow:0 2px 10px rgba(25,59,88,0.06)}}
.chart{{overflow:auto;text-align:center}}
svg{{width:100%;max-width:none;height:auto;box-sizing:border-box;border:1px solid #d8e6f0;
 border-radius:8px;background:white}}
svg .grid{{stroke:#e9f1f7;stroke-width:1}}
svg .axis{{fill:#193b58;font:bold 14px system-ui}}
svg .tick{{fill:#617d92;font:11px system-ui}}
svg .vertex circle{{fill:#1769aa;stroke:white;stroke-width:1.5}}
svg .point-label{{fill:#193b58;font:bold 12px system-ui;paint-order:stroke;
 stroke:white;stroke-width:3px;stroke-linejoin:round}}
.chart-tools{{display:flex;align-items:center;gap:.6rem;justify-content:flex-end;
 color:#193b58;font-size:.9rem;margin-bottom:.7rem}}
.chart-tools input{{width:150px;accent-color:#1769aa}}
svg .area-label rect{{fill:#e9f4fc;stroke:#1769aa;stroke-width:1}}
svg .area-label{{pointer-events:none}}
svg .area-label text{{fill:#193b58;font:bold 13px system-ui}}
table{{border-collapse:collapse;width:100%;margin-top:.7rem;
 background:white;border-radius:6px;overflow:hidden}}
th,td{{border:1px solid #d8e6f0;padding:.55rem;text-align:center;white-space:nowrap;
 color:#193b58}}
th{{background:linear-gradient(180deg,#123554 0%,#193b58 100%);
 color:white;text-align:center;vertical-align:middle;font-weight:600}}
.num{{text-align:center;font-variant-numeric:tabular-nums}}
.area-line{{display:block;white-space:nowrap}}
tr:nth-child(even){{background:#f8fbfe}}
small{{display:block;margin-top:1rem;line-height:1.5;color:#617d92}}
.download{{display:inline-block;
 background:linear-gradient(90deg,#1769aa 0%,#2d8bd0 100%);
 color:white;text-decoration:none;
 padding:.7rem 1.1rem;border-radius:6px;font-weight:bold;
 box-shadow:0 2px 8px rgba(23,105,170,0.25)}}
.download:hover{{background:#1769aa}}
@media print{{body{{background:white}}main{{margin:0;max-width:none}}
 .chart-tools{{display:none}}
 .chart svg{{width:100%!important}}
 section,.card{{box-shadow:none;border:1px solid #d8e6f0}}}}
</style><main><header><h1>📐 Cierre geométrico de polígono</h1>
<div class="developer">Desarrollado por: Raúl Víquez | viquezr@gmail.com</div>
<div class="meta"><span>Tabla: {layer}</span><span>CRS: {crs}</span>{author}</div></header><div class="summary">
<div class="card">Tramos: <b>{count}</b></div>
<div class="card">Perímetro: <b>{perimeter} m</b></div>
<div class="card">Área por coordenadas corregidas: <b>{area}</b></div>
{slope_card}</div>
{observation_block}
{close_notice}
{pre_section}
{vertical_section}
<section><h2>Proyecciones y cierre</h2><div style="overflow-x:auto"><table><thead>
<tr><th>Indicador</th><th>Resultado</th></tr></thead><tbody>
<tr><td>St (N-S)</td><td class="num">{st} m</td></tr>
<tr><td>Sg (E-W)</td><td class="num">{sg} m</td></tr>
<tr><td>Error relativo (reconstrucción)</td><td class="num">{relative_error}</td></tr>
<tr><td>Residuo de cierre por redondeo</td><td class="num">{closure_error} m</td></tr>
<tr><td>Relación P/e (referencial)</td><td class="num">{precision_short}</td></tr>
<tr><td>Área por coordenadas corregidas</td><td class="num">{area}</td></tr>
</tbody></table></div><small>La corrección proporcional por distancia hace que las sumas de
proyecciones corregidas St y Sg sean cero, salvo residuos numéricos; por eso no se
repiten como indicadores. Este residuo es efecto de redondear datos derivados de
coordenadas y no representa la precisión topográfica del levantamiento. La relación
P/e se muestra solo como referencia de la reconstrucción.</small></section>
<section><h2>Medidas complementarias</h2><div style="overflow-x:auto"><table><thead>
<tr><th>Indicador</th><th>Resultado</th></tr></thead><tbody>
<tr><td>Tramo más corto</td><td class="num">{shortest_station}: {shortest_distance} m{equal_note}</td></tr>
<tr><td>Tramo más largo</td><td class="num">{longest_station}: {longest_distance} m</td></tr>
<tr><td>Área por coordenadas originales</td><td class="num">{raw_area}</td></tr>
<tr><td>Diferencia de área (ajustada − original)</td><td class="num">{area_difference}</td></tr>
</tbody></table></div><small>La diferencia conserva su signo: un valor positivo indica que el
área ajustada es mayor. Las áreas se calculan con precisión completa y se presentan
redondeadas a tres decimales.</small></section>
<section><h2>Plano del polígono</h2>
<label class="chart-tools">Tamaño del plano
<input type="range" min="-2" max="2" step="0.25" value="0"
 aria-label="Ampliación del plano" oninput="var c=this.closest('section').querySelector('.chart');
 var z=100*Math.pow(2,Number(this.value));c.querySelector('svg').style.width=z+'%';
 this.parentElement.querySelector('output').textContent=Math.round(z)+'%';
 this.setAttribute('aria-valuetext',Math.round(z)+'%')"><output>100%</output></label>
<div class="chart">{chart}</div>
<small>Vista de las coordenadas proyectadas originales, con proporción igual en ambos ejes.
El área del recuadro procede de las proyecciones corregidas. El plano inicia
ajustado al ancho disponible (100%). El control permite reducirlo hasta 25%
o ampliarlo hasta 400%; desplácelo cuando esté ampliado. Si un número no cabe, pase el cursor
sobre el punto para consultar su número y coordenadas.{label_note}</small></section>
{delivery_table}
<section><h2>Detalle de tramos</h2><div style="overflow-x:auto">
<table><thead><tr><th>Estación</th><th>Punto inicial</th><th>Punto final</th>
<th>N inicial (m)</th><th>E inicial (m)</th><th>N final (m)</th><th>E final (m)</th>
<th>ΔN (m)</th><th>ΔE (m)</th><th>Distancia (m)</th><th>Rumbo</th></tr></thead>
<tbody>{rows}</tbody></table></div><small>Las proyecciones se reconstruyen con distancia redondeada a 0.001 m
y rumbo redondeado al segundo, y se corrigen en proporción a la longitud (Bowditch).
El error mostrado procede de ese redondeo; las coordenadas proyectadas de entrada ya cierran.
Área plana de la cuadrícula proyectada.</small></section></main>
</html>""".format(layer=esc(layer), crs=esc(crs), author=author_html,
                  close_notice=close_notice,
                  observation_block=observation_block,
                  pre_section=pre_section, vertical_section=vertical_section,
                  slope_card=slope_card,
                  chart=chart, delivery_table=delivery_table,
                  label_note=label_note,
                  count=len(segments),
                  perimeter=fmt(perimeter), area=_format_area(summary["corrected_area"]),
                  st=fmt_small(summary["st"]), sg=fmt_small(summary["sg"]),
                  relative_error=fmt_small(summary["relative_error"]),
                  closure_error=fmt_small(summary["closure_error"]),
                  shortest_station=esc(shortest[0]),
                  shortest_distance=fmt(shortest[9]),
                  longest_station=esc(longest[0]),
                  longest_distance=fmt(longest[9]),
                  raw_area=_format_area(summary["raw_area"]),
                  area_difference=area_difference_text,
                  precision_short=precision_short,
                  equal_note=equal_note,
                  rows="".join(rows))


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
        '<header><h1>📐 Finca: varios polígonos</h1>'
        '<div class="developer">Desarrollado por: Raúl Víquez | viquezr@gmail.com</div>'
        '<div class="meta">'
        '<span>Archivo: {}</span><span>CRS: {}</span>{}</div></header>'
        '<section><h2>Resumen de contornos</h2><p>{} polígonos: {}.</p>'
        '<p>Suma aritmética de áreas por coordenadas originales: <b>{}</b>. '
        'La suma representa el área conjunta solo cuando los contornos no se superponen.</p>'
        '<small>Los puntos se ordenan por PUNTO entero consecutivo de 1 a N. '
        'El código PT/PC en OBS identifica '
        'cada contorno. '
        'Se excluyen los traslados entre grupos. El lado final añadido une el '
        'último vértice con el primero de cada contorno y no constituye una '
        'reobservación de cierre topográfico.</small></section>').format(
            html.escape(source), html.escape(crs),
            '<span>Autor: {}</span>'.format(html.escape(author.strip()))
            if author.strip() else "", len(polygons), navigation, _format_area(total))
    return ('<!doctype html><html lang="es"><meta charset="utf-8">'
            '<title>Cierre geométrico · varios polígonos</title><style>{}'
            'article{{margin-top:2.4rem}}'
            'article>h2{{padding:.6rem 0;color:#193b58;'
            'border-bottom:2px solid #1769aa}}'
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
        dialog.setWindowTitle("Cierre geométrico desde coordenadas proyectadas")
        dialog.setMinimumWidth(430)
        dialog.setStyleSheet("""
            QDialog { background: #f3f7fb; color: #193b58; font-size: 13px; }

            QLabel#heading {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #193b58, stop:1 #1769aa);
                color: white; font-size: 17px; font-weight: bold;
                padding: 12px 14px; border-radius: 6px;
            }
            QLabel#developer {
                color: #1769aa; font-style: italic;
                font-size: 11px; padding: 4px 4px 2px 4px;
            }
            QLabel#help { color: #617d92; padding: 5px 3px; font-size: 11px; }

            QGroupBox {
                background: white; border: 2px solid #1769aa;
                border-radius: 6px; margin-top: 10px;
                padding: 10px 10px 8px 10px; font-weight: bold;
                font-size: 11px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px;
                padding: 0 5px; color: #1769aa;
            }

            QComboBox, QLineEdit, QDoubleSpinBox {
                background: white; color: #193b58;
                border: 1px solid #afc5d5; border-radius: 4px;
                padding: 5px 6px; min-height: 22px;
            }
            QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus {
                border: 2px solid #1769aa;
            }
            QComboBox:disabled, QLineEdit:disabled, QDoubleSpinBox:disabled {
                background: #e9f1f7; color: #8299a9;
            }

            QPushButton {
                background-color: #e2edf5; color: #193b58; border: none;
                border-radius: 4px; padding: 8px 16px;
                font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #d0e2f0; }
            QPushButton:disabled { background-color: #e9f1f7; color: #8299a9; }

            QPushButton#generate {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1769aa, stop:1 #2d8bd0);
                color: white;
            }
            QPushButton#generate:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2d8bd0, stop:1 #1769aa);
            }

            QTabWidget::pane {
                border: 1px solid #afc5d5;
                background: #f3f7fb; border-radius: 6px;
                top: -1px;
            }
            QTabBar::tab {
                background: #d8e6f0;
                color: #193b58;
                padding: 8px 6px;
                margin-right: 3px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-weight: bold;
                font-size: 12px;
                width: 180px;
            }
            QTabBar::tab:selected {
                background: #1769aa;
                color: white;
            }
            QTabBar::tab:hover:!selected { background: #cfdfeb; }

            QCheckBox { color: #193b58; font-size: 11px; spacing: 6px; }
            QCheckBox::indicator {
                width: 15px; height: 15px;
                border: 2px solid #afc5d5; border-radius: 3px;
                background: white;
            }
            QCheckBox::indicator:checked {
                background: #1769aa; border: 2px solid #104f84;
            }
        """)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 10, 10, 10)

        heading = QLabel("📐  CIERRE GEOMÉTRICO  ·  COORDENADAS PROYECTADAS")
        heading.setObjectName("heading")
        layout.addWidget(heading)

        developer = QLabel("Desarrollado por: Raúl Víquez  |  viquezr@gmail.com")
        developer.setObjectName("developer")
        developer.setAlignment(Qt.AlignCenter)
        layout.addWidget(developer)

        author_row = QHBoxLayout()
        author_label = QLabel("Autor del informe")
        author_label.setStyleSheet("font-weight: bold; color: #193b58; font-size: 11px;")
        author_row.addWidget(author_label)
        author_box = QLineEdit()
        author_box.setPlaceholderText("Nombre (opcional)")
        author_box.setMaxLength(120)
        author_row.addWidget(author_box, 1)
        layout.addLayout(author_row)

        tabs = QTabWidget(dialog)
        tabs.tabBar().setElideMode(Qt.ElideNone)
        tabs.tabBar().setExpanding(False)
        tabs.tabBar().setUsesScrollButtons(True)
        layout.addWidget(tabs, 1)

        def add_tab(title, scroll=True):
            page = QScrollArea(tabs) if scroll else QWidget(tabs)
            page.setMinimumHeight(180)
            if scroll:
                page.setWidgetResizable(True)
                page.setFrameShape(QScrollArea.NoFrame)
            content = QWidget(page) if scroll else page
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(6, 5, 9, 5)
            content_layout.setSpacing(5)
            if scroll:
                page.setWidget(content)
            tabs.addTab(page, title)
            return content_layout

        data_layout = add_tab("Datos de entrada", scroll=False)
        analysis_layout = add_tab("Análisis y cierre")
        inputs = QGroupBox("Datos de entrada")
        form = QFormLayout()
        form.setVerticalSpacing(4)
        inputs.setLayout(form)
        data_layout.addWidget(inputs)
        source_box = QComboBox()
        source_box.addItem("Coordenadas", "coordinates")
        source_box.addItem("Ángulos y distancias", "angles")
        source_box.addItem("Rumbos y distancias", "bearings")
        form.addRow("Tipo de datos", source_box)
        layer_source_radio = QRadioButton("Tabla en QGIS")
        csv_source_radio = QRadioButton("Archivo CSV")
        origin_group = QButtonGroup(dialog)
        origin_group.addButton(layer_source_radio)
        origin_group.addButton(csv_source_radio)
        layer_source_radio.setChecked(True)
        origin_row = QHBoxLayout()
        origin_row.addWidget(layer_source_radio)
        origin_row.addWidget(csv_source_radio)
        form.addRow("Origen", origin_row)

        def selected_input_source():
            kind = source_box.currentData()
            via_csv = csv_source_radio.isChecked()
            if kind == "coordinates":
                return "station_csv" if via_csv else "layer"
            if kind == "angles":
                return "observation_csv" if via_csv else "observation_layer"
            return "bearing_csv" if via_csv else "bearing_layer"

        mode_box = QComboBox()
        mode_box.addItem("Polígono cerrado y cálculo de área", "polygon")
        mode_box.addItem("Varios polígonos por OBS (PT, PC, PT1…)", "multi")
        analysis_group = QGroupBox("Configuración del análisis")
        analysis_form = QFormLayout(analysis_group)
        analysis_form.setVerticalSpacing(4)
        analysis_layout.addWidget(analysis_group)
        analysis_form.addRow("Tipo de informe", mode_box)
        csv_path_box = QLineEdit()
        csv_path_box.setPlaceholderText("PUNTO/EST, ESTE/X, NORTE/Y, COTA/Z [, OBS]")
        csv_browse = QPushButton("Examinar…")
        csv_row = QHBoxLayout()
        csv_row.addWidget(csv_path_box)
        csv_row.addWidget(csv_browse)
        form.addRow("Archivo CSV", csv_row)

        def browse_station_csv():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Seleccionar archivo de datos", "", "CSV (*.csv *.txt);;Todos (*)")
            if path:
                csv_path_box.setText(path)
                csv_source_radio.setChecked(True)

        csv_browse.clicked.connect(browse_station_csv)
        layer_box = QComboBox()
        layers = [layer for layer in QgsProject.instance().mapLayers().values()
                  if isinstance(layer, QgsVectorLayer)]
        for layer in sorted(layers, key=lambda item: item.name().casefold()):
            layer_box.addItem(layer.name(), layer.id())
        form.addRow("Tabla de coordenadas", layer_box)
        input_help = QLabel()
        input_help.setWordWrap(True)
        input_help.setObjectName("help")
        help_scroll = QScrollArea()
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QScrollArea.NoFrame)
        help_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        help_scroll.setMinimumHeight(80)
        help_content = QWidget(help_scroll)
        legend_layout = QVBoxLayout(help_content)
        legend_layout.setContentsMargins(6, 5, 9, 5)
        legend_layout.setSpacing(5)
        legend_layout.setAlignment(Qt.AlignTop)
        legend_layout.addWidget(input_help)
        help_scroll.setWidget(help_content)
        allow_empty_cota = QCheckBox("Permitir COTA completamente vacía (solo cálculos en planta)")
        form.addRow(allow_empty_cota)

        # === NUEVO === Grupo de parámetros para observaciones de campo
        observation_group = QGroupBox("Parámetros del cálculo desde observaciones")
        observation_form = QFormLayout(observation_group)
        observation_form.setVerticalSpacing(4)
        obs_north = QLineEdit()
        obs_north.setPlaceholderText("Norte del primer punto (m)")
        observation_form.addRow("Norte inicial", obs_north)
        obs_east = QLineEdit()
        obs_east.setPlaceholderText("Este del primer punto (m)")
        observation_form.addRow("Este inicial", obs_east)
        obs_azimuth = QLineEdit()
        obs_azimuth.setPlaceholderText("Azimut del primer lado (DMS o decimal)")
        observation_form.addRow("Azimut inicial", obs_azimuth)
        obs_direction = QComboBox()
        obs_direction.addItem("Antihorario (CCW)", "ccw")
        obs_direction.addItem("Horario (CW)", "cw")
        observation_form.addRow("Sentido del recorrido", obs_direction)
        obs_precision = QDoubleSpinBox()
        obs_precision.setRange(0.1, 600.0)
        obs_precision.setDecimals(2)
        obs_precision.setSingleStep(1.0)
        obs_precision.setValue(10.0)
        obs_precision.setSuffix(" \"")
        observation_form.addRow("Coeficiente angular a (″)", obs_precision)
        obs_linear_tol = QDoubleSpinBox()
        obs_linear_tol.setRange(100.0, 1_000_000.0)
        obs_linear_tol.setDecimals(0)
        obs_linear_tol.setSingleStep(1000.0)
        obs_linear_tol.setValue(10000.0)
        obs_linear_tol.setPrefix("1 : ")
        observation_form.addRow("Tolerancia lineal", obs_linear_tol)
        obs_help = QLabel(
            "Modo observaciones: CSV con EST/PUNTO, ANGULO (interior, "
            "decimal o DMS), DISTANCIA horizontal (m) y COTA opcional. "
            "El ángulo en cada fila es el interior del vértice y la distancia "
            "es la del lado hacia el vértice siguiente. Requiere al menos 3 "
            "vértices y un azimut inicial conocido.")
        obs_help.setWordWrap(True)
        obs_help.setObjectName("help")
        observation_form.addRow(obs_help)
        evaluate_criteria_box = QCheckBox("Evaluar los criterios configurados (a√n y 1:N)")
        angular_adjust_box = QCheckBox("Aplicar ajuste angular uniforme")
        linear_adjust_box = QCheckBox("Aplicar Bowditch después del ajuste angular")
        return_measured_box = QCheckBox("Confirmo el último tramo observado hacia EST 1")
        observation_form.addRow(evaluate_criteria_box)
        observation_form.addRow(angular_adjust_box)
        observation_form.addRow(linear_adjust_box)
        observation_form.addRow(return_measured_box)
        observation_form.addRow(QLabel("a√n es un criterio del proyecto; a no se infiere del equipo."))
        analysis_layout.addWidget(observation_group)

        def update_adjustments():
            if linear_adjust_box.isChecked():
                angular_adjust_box.setChecked(True)
            if angular_adjust_box.isChecked():
                evaluate_criteria_box.setChecked(True)

        def update_angular():
            if not angular_adjust_box.isChecked():
                linear_adjust_box.setChecked(False)
            else:
                evaluate_criteria_box.setChecked(True)

        def update_criteria():
            if not evaluate_criteria_box.isChecked():
                angular_adjust_box.setChecked(False)
                linear_adjust_box.setChecked(False)

        linear_adjust_box.toggled.connect(update_adjustments)
        angular_adjust_box.toggled.connect(update_angular)
        evaluate_criteria_box.toggled.connect(update_criteria)

        bearing_group = QGroupBox("Cierre por rumbos y distancias")
        bearing_form = QFormLayout(bearing_group)
        bearing_north = QLineEdit()
        bearing_east = QLineEdit()
        bearing_north.setPlaceholderText("Opcional; ambas vacías = origen local 0,0")
        bearing_east.setPlaceholderText("Opcional; ambas vacías = origen local 0,0")
        bearing_form.addRow("Norte inicial", bearing_north)
        bearing_form.addRow("Este inicial", bearing_east)
        bearing_precision = QDoubleSpinBox()
        bearing_precision.setRange(1, 10000000)
        bearing_precision.setDecimals(0)
        bearing_precision.setValue(10000)
        bearing_precision.setPrefix("1 : ")
        bearing_form.addRow("Precisión mínima", bearing_precision)
        bearing_evaluate = QCheckBox("Evaluar precisión lineal indicada")
        bearing_adjust = QCheckBox("Aplicar Bowditch si cumple la precisión indicada")
        bearing_return = QCheckBox("Confirmo que N-1 es el último tramo medido hacia el inicio")
        bearing_form.addRow(bearing_evaluate)
        bearing_form.addRow(bearing_adjust)
        bearing_form.addRow(bearing_return)
        bearing_help = QLabel(
            "ESTACION o PUNTO: texto 1-2, 2-3, …, N-1. DISTANCIA: horizontal, numérica en metros. "
            "RUMBO o RUMBOS: texto N81°34′59″E; admite W u O para oeste. COTA/Z opcional: "
            "cota del origen del tramo. Se ignoran columnas adicionales. "
            "No se evalúa cierre angular; no se añade un lado de cierre.")
        bearing_help.setWordWrap(True)
        bearing_help.setObjectName("help")
        bearing_form.addRow(bearing_help)
        analysis_layout.addWidget(bearing_group)

        def update_bearing_adjust(checked):
            if checked:
                bearing_evaluate.setChecked(True)

        def update_bearing_evaluate(checked):
            if not checked:
                bearing_adjust.setChecked(False)

        bearing_adjust.toggled.connect(update_bearing_adjust)
        bearing_evaluate.toggled.connect(update_bearing_evaluate)

        crs_box = QgsProjectionSelectionWidget(dialog)
        crs_box.setCrs(QgsCoordinateReferenceSystem(""))
        form.addRow("CRS de las coordenadas", crs_box)
        crs_help = QLabel(
            "Seleccione el CRS proyectado en metros de las coordenadas de entrada "
            "(UTM de cualquier zona u otra proyección métrica). "
            "Se usa para identificar el informe; no se reproyectan los valores.")
        crs_help.setWordWrap(True)
        crs_help.setObjectName("help")
        legend_layout.addWidget(crs_help)
        data_layout.addWidget(help_scroll, 1)
        closure_help = QLabel(
            "Para un polígono, se repite internamente el primer punto si falta "
            "al final del archivo.")
        closure_help.setWordWrap(True)
        analysis_form.addRow("Cierre", closure_help)
        sequence_help = QLabel(
            "Para un polígono, OBS se ignora; retire los puntos radiados antes de calcular. "
            "Para varios, use bloques PT, PC, PT1, PC1 o PT_2. "
            "Se rechazan códigos ajenos y grupos que reaparecen. El trazado no debe cruzarse.")
        sequence_help.setWordWrap(True)
        sequence_help.setObjectName("help")
        analysis_layout.addWidget(sequence_help)

        def update_source():
            source = selected_input_source()
            bearings_mode = source in ("bearing_csv", "bearing_layer")
            observations = source in ("observation_csv", "observation_layer")
            field_mode = observations or bearings_mode
            bearing_group.setVisible(bearings_mode)
            crs_box.setEnabled(not bearings_mode or bool(bearing_north.text().strip() or bearing_east.text().strip()))
            # Both selectors stay usable; choosing a resource activates its origin.
            csv_path_box.setEnabled(True)
            csv_browse.setEnabled(True)
            layer_box.setEnabled(True)
            observation_group.setVisible(observations)
            analysis_group.setVisible(not field_mode)
            sequence_help.setVisible(not field_mode)
            mode_box.setEnabled(not field_mode)
            dialog.setWindowTitle("Cierre observado desde ángulos y distancias" if observations else
                                  "Cierre geométrico desde coordenadas proyectadas")
            form.labelForField(layer_box).setText("Tabla de observaciones" if observations else "Tabla de coordenadas")
            heading.setText("📐  CIERRE GEOMÉTRICO  ·  OBSERVACIONES DE CAMPO" if observations else
                            "📐  CIERRE GEOMÉTRICO  ·  COORDENADAS PROYECTADAS")
            input_help.setText(input_format_help(source_box.currentData()))
            input_help.setVisible(True)
            if bearings_mode:
                dialog.setWindowTitle("Cierre por rumbos y distancias")
                heading.setText("📐  CIERRE GEOMÉTRICO  ·  RUMBOS Y DISTANCIAS")
                form.labelForField(layer_box).setText("Tabla de rumbos y distancias")
                csv_path_box.setPlaceholderText("ESTACION/PUNTO, DISTANCIA, RUMBO [, COTA]")
            elif observations:
                csv_path_box.setPlaceholderText(
                    "EST, ANGULO, DISTANCIA [, COTA] [, OBS]")
            else:
                csv_path_box.setPlaceholderText(
                    "PUNTO/EST, ESTE/X, NORTE/Y, COTA/Z [, OBS]")

        bearing_east.textChanged.connect(update_source)
        bearing_north.textChanged.connect(update_source)
        layer_source_radio.toggled.connect(update_source)
        csv_source_radio.toggled.connect(update_source)
        csv_path_box.textEdited.connect(lambda _text: csv_source_radio.setChecked(True))
        layer_box.activated.connect(lambda _index: layer_source_radio.setChecked(True))
        source_box.currentIndexChanged.connect(update_source)
        update_source()

        def update_fields():
            selected = QgsProject.instance().mapLayer(layer_box.currentData())
            if selected is None:
                return
            if selected.crs().isValid():
                crs_box.setCrs(selected.crs())

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
            dialog.resize(min(600, available.width() - 70),
                          min(620, available.height() - 90))
        else:
            dialog.resize(540, 580)

        def generate_report():
            crs = crs_box.crs()
            selected_source = selected_input_source()
            local_bearings = (selected_source in ("bearing_csv", "bearing_layer")
                              and not bearing_north.text().strip() and not bearing_east.text().strip())
            if not local_bearings and not crs.isValid():
                QMessageBox.warning(dialog, "CRS", "Seleccione el CRS de las coordenadas.")
                return
            if not local_bearings and (crs.isGeographic() or crs.mapUnits() != QgsUnitTypes.DistanceMeters):
                QMessageBox.warning(
                    dialog, "CRS", "El CRS debe ser proyectado y usar metros. "
                    "No se calculan distancias ni áreas a partir de grados o pies.")
                return
            records = []
            observation_result = None
            selected_source = selected_input_source()
            selected_mode = mode_box.currentData()
            try:
                if selected_source in ("bearing_csv", "bearing_layer"):
                    n_text, e_text = bearing_north.text().strip(), bearing_east.text().strip()
                    if bool(n_text) != bool(e_text):
                        raise ValueError("Complete NORTE y ESTE iniciales, o deje ambos vacíos para origen local.")
                    if not bearing_return.isChecked():
                        raise ValueError("Confirme que el último tramo N-1 fue medido hacia el inicio.")
                    if selected_source == "bearing_layer":
                        layer = QgsProject.instance().mapLayer(layer_box.currentData())
                        if layer is None:
                            raise ValueError("Seleccione la tabla de rumbos y distancias.")
                        bearing_rows = read_bearings_from_layer(layer)
                        source_name = layer.name()
                    else:
                        csv_path = csv_path_box.text().strip()
                        if not csv_path:
                            raise ValueError("Seleccione el CSV de rumbos y distancias.")
                        bearing_rows = read_bearing_csv(csv_path)
                        source_name = os.path.basename(csv_path)
                    result = calculate_bearing_traverse(
                        bearing_rows, n_text or 0, e_text or 0,
                        minimum_precision=bearing_precision.value() if bearing_evaluate.isChecked() else None,
                        adjust_linear=bearing_adjust.isChecked(), allow_empty_cota=allow_empty_cota.isChecked())
                    path, _ = QFileDialog.getSaveFileName(
                        dialog, "Guardar cierre por rumbos", "cierre_rumbos_distancias.html", "HTML (*.html)")
                    if not path:
                        return
                    if not path.lower().endswith(".html"):
                        path += ".html"
                    crs_text = "Sistema local en metros (sin georreferenciar)" if local_bearings else crs.authid()
                    with open(path, "w", encoding="utf-8") as report:
                        report.write(build_raw_traverse_html(result, source_name, crs_text, author_box.text()))
                    webbrowser.open("file://" + os.path.abspath(path))
                    message = path + "\n\n" + "\n".join(result["warnings"])
                    if result["linear_ok"] is False or (result["linear_applied"] and result["adjusted_area"] is None):
                        QMessageBox.warning(dialog, "Informe generado con advertencias", message)
                    else:
                        QMessageBox.information(dialog, "Informe generado", message)
                    return
                # === NUEVO === Rama de observaciones de campo
                if selected_source in ("observation_csv", "observation_layer"):
                    start_n = parse_coordinate(obs_north.text().strip(),
                                               "NORTE inicial", "1", "Parámetros")
                    start_e = parse_coordinate(obs_east.text().strip(),
                                               "ESTE inicial", "1", "Parámetros")
                    start_az = parse_angle_deg(obs_azimuth.text(),
                                               "Parámetros", "Azimut inicial")
                    if selected_source == "observation_layer":
                        obs_layer = QgsProject.instance().mapLayer(layer_box.currentData())
                        if obs_layer is None:
                            raise ValueError(
                                "Seleccione la tabla cargada en QGIS con las observaciones.")
                        observations = read_observations_from_layer(obs_layer)
                        source_name = obs_layer.name()
                    else:
                        csv_path = csv_path_box.text().strip()
                        if not csv_path:
                            raise ValueError("Seleccione el CSV de observaciones de campo.")
                        observations = read_observation_csv(csv_path)
                        source_name = os.path.basename(csv_path)
                    if not return_measured_box.isChecked():
                        raise ValueError("Confirme que el último tramo fue observado hacia EST 1.")
                    observation_result = process_observations(
                        observations, start_n, start_e, start_az,
                        obs_direction.currentData(), angular_precision_sec=obs_precision.value(),
                        linear_tolerance_den=obs_linear_tol.value(),
                        evaluate_criteria=evaluate_criteria_box.isChecked(),
                        adjust_angles=angular_adjust_box.isChecked(), adjust_linear=linear_adjust_box.isChecked(),
                        allow_empty_cota=allow_empty_cota.isChecked())
                    path, _ = QFileDialog.getSaveFileName(
                        dialog, "Guardar informe observado", "cierre_observado.html", "HTML (*.html)")
                    if not path:
                        return
                    if not path.lower().endswith(".html"):
                        path += ".html"
                    report_text = build_raw_traverse_html(
                        observation_result, source_name, crs.authid(), author_box.text())
                    with open(path, "w", encoding="utf-8") as report:
                        report.write(report_text)
                    webbrowser.open("file://" + os.path.abspath(path))
                    if observation_result["warnings"]:
                        QMessageBox.warning(dialog, "Informe generado con observaciones",
                                            path + "\n\n" + "\n".join(observation_result["warnings"]))
                    else:
                        QMessageBox.information(dialog, "Informe generado", path)
                    return
                # === FIN NUEVO ===

                direct = selected_source == "station_csv"
                if direct:
                    csv_path = csv_path_box.text().strip()
                    if not csv_path:
                        raise ValueError("Seleccione el CSV de la estación total.")
                    source_name = os.path.basename(csv_path)
                    observations = read_station_observations(
                        csv_path, require_obs=selected_mode == "multi")
                    ordered = normalize_station_observations(
                        observations, ignore_obs=selected_mode == "polygon")
                    records = [record for _, _, record in ordered]
                else:
                    layer = QgsProject.instance().mapLayer(layer_box.currentData())
                    if layer is None:
                        raise ValueError("Seleccione una tabla cargada en QGIS.")
                    fields = resolve_input_fields(
                        [field.name() for field in layer.fields()],
                        require_obs=selected_mode == "multi")
                    field_by_name = {field.name(): field for field in layer.fields()}
                    integer_types = (QVariant.Int, QVariant.UInt,
                                     QVariant.LongLong, QVariant.ULongLong)
                    if field_by_name[fields["PUNTO"]].type() not in integer_types:
                        raise ValueError("El campo {} debe ser de tipo entero.".format(fields["PUNTO"]))
                    if (selected_mode == "multi" and "OBS" in fields
                            and field_by_name[fields["OBS"]].type() != QVariant.String):
                        raise ValueError("El campo OBS debe ser de tipo texto.")
                    for name in ("ESTE", "NORTE"):
                        if field_by_name[fields[name]].type() != QVariant.Double:
                            raise ValueError("El campo {} debe ser real o doble.".format(fields[name]))
                    if field_by_name[fields["COTA"]].type() not in (
                            QVariant.Double,) + integer_types:
                        raise ValueError("El campo COTA debe ser numérico (entero o real).")
                    observations = []
                    for feature in layer.getFeatures():
                        point = feature[fields["PUNTO"]]
                        obs_value = (feature[fields["OBS"]]
                                     if "OBS" in fields else None)
                        observations.append((
                            point, point, feature[fields["NORTE"]],
                            feature[fields["ESTE"]], feature[fields["COTA"]],
                            "registro QGIS ID {}".format(feature.id()),
                            (obs_value if obs_value is not None else "")
                            if "OBS" in fields else None))
                    ordered = normalize_station_observations(
                        observations, ignore_obs=selected_mode == "polygon")
                    records = [record for _, _, record in ordered]
                    source_name = layer.name()
                validate_cota_completeness(records, allow_empty_cota.isChecked())
                if selected_mode == "multi":
                    polygons = analyze_station_groups(
                        group_station_observations(observations))
                else:
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
