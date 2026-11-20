"""Experimental generic/AAMA DXF geometry exchange; no CAD compliance claim.

Curves become polylines at a declared chord tolerance. Layer names survive,
but block structure and text semantics do not. Reports enumerate those losses.
"""
import base64
import hashlib
import math
import re
from collections import Counter
from io import StringIO
from pathlib import Path

import ezdxf
from ezdxf import path as dxfpath
from shapely.geometry import Polygon

from .assembly import UNITS

MAX_BYTES = 10 * 1024 * 1024
MAX_ENTITIES = 100_000
MAX_EXPANDED_ENTITIES = 200_000
MAX_VERTICES = 500_000

SUPPORTED = {"LINE", "POINT", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}


def read_dxf_geometry(path_or_bytes, *, assume_units=None, tolerance_mm=0.1,
                      _raw_doc=None, _validate_closed=True):
    """Return metric geometry and inventory; refuse ambiguous/contradictory units.

    Only finite geometry in the XY plane is accepted. Nested INSERTs are
    expanded with their transforms; recursive blocks are rejected.
    Unsupported entities are reported, never silently treated as contours.
    """
    if not math.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError("tolerance_mm must be finite and positive")
    if not isinstance(path_or_bytes, bytes) and Path(path_or_bytes).stat().st_size > MAX_BYTES:
        raise ValueError("DXF exceeds 10 MiB limit")
    raw = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("DXF exceeds 10 MiB limit")
    try:
        doc = _raw_doc or ezdxf.decode_base64(base64.b64encode(raw))
        # Layout block records include modelspace and paper spaces; count each once.
        source_count = sum(1 for layout in doc.layouts for _ in layout)
        for block in doc.blocks:
            if not block.block_record.is_any_layout:
                source_count += sum(1 for _ in block)
            if source_count > MAX_ENTITIES:
                break
    except Exception as exc:
        raise ValueError("Invalid DXF") from exc
    if source_count > MAX_ENTITIES:
        raise ValueError("DXF entity limit exceeded")
    try:
        audit = doc.audit()
    except Exception as exc:
        raise ValueError("Invalid DXF") from exc
    if audit.has_errors:
        raise ValueError("Invalid DXF: audit errors")
    code = int(doc.header.get("$INSUNITS", 0))
    choices = dict(UNITS.values())
    if code == 0:
        if assume_units not in choices:
            raise ValueError("Ambiguous DXF units: declare assume_units")
        name, factor = assume_units, choices[assume_units]
    elif code in UNITS:
        name, factor = UNITS[code]
        if assume_units is not None and assume_units != name:
            raise ValueError("assume_units contradicts $INSUNITS")
    else:
        raise ValueError(f"Unsupported $INSUNITS: {code}")
    report = dict(units=dict(code=code, name=name, assumed=code == 0,
                             scale_to_mm=factor, output="mm"),
                  tolerance_mm=tolerance_mm, layers={}, blocks={}, curves={}, losses=[],
                  source=dict(file_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)),
                  exporter=None)
    entities = []
    layers, blocks, curves = {}, Counter(), Counter()
    counts = dict(expanded=0, vertices=0)

    def loss(kind, reason, layer=None):
        report["losses"].append(dict(entity_type=kind, layer=layer, reason=reason))

    def collect(space, matrices=(), stack=(), inherited="0"):
        for entity in space:
            counts["expanded"] += 1
            if counts["expanded"] > MAX_EXPANDED_ENTITIES:
                raise ValueError("DXF block expansion limit exceeded")
            kind = entity.dxftype()
            layer = entity.dxf.layer if entity.dxf.layer != "0" else inherited
            layers.setdefault(layer, Counter())[kind] += 1
            if kind == "INSERT":
                name = entity.dxf.name
                if name in stack or len(stack) >= 32:
                    raise ValueError("Invalid DXF: recursive or excessively nested block")
                if entity.mcount > MAX_EXPANDED_ENTITIES - counts["expanded"]:
                    raise ValueError("DXF block expansion limit exceeded")
                for insert in entity.multi_insert() if entity.mcount > 1 else [entity]:
                    counts["expanded"] += 1
                    if counts["expanded"] > MAX_EXPANDED_ENTITIES:
                        raise ValueError("DXF block expansion limit exceeded")
                    blocks[name] += 1
                    loss(kind, "block structure expanded", layer)
                    if insert.attribs:
                        loss("ATTRIB", "block attributes omitted", layer)
                    collect(insert.block(), (insert.matrix44(), *matrices), (*stack, name), layer)
                continue
            if kind not in SUPPORTED:
                loss(kind, "unsupported entity omitted", layer)
                continue
            if kind == "POLYLINE" and (entity.is_polygon_mesh or entity.is_poly_face_mesh):
                loss(kind, "mesh geometry omitted", layer)
                continue
            if kind == "LWPOLYLINE" and entity.has_width:
                loss(kind, "polyline width omitted; centerline retained", layer)
            # Convert vertices before block transforms; account for magnification
            # so curve flattening tolerance is in final millimetres.
            amplification = 1.
            for matrix in matrices:
                amplification *= math.sqrt(sum(matrix.transform_direction(v).magnitude_square
                                               for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1))))
            if kind == "POINT":
                vertices = [entity.dxf.location]
                closed = False
            else:
                path = dxfpath.make_path(entity)
                closed = path.is_closed
                from itertools import islice
                remaining = MAX_VERTICES - counts["vertices"]
                vertices = list(islice(path.flattening(tolerance_mm / (factor * max(amplification, 1e-12))), remaining + 1))
                if len(vertices) > remaining:
                    raise ValueError("DXF vertex limit exceeded")
                if path.has_curves:
                    curves[kind] += 1
                    loss(kind, "curve flattened to polyline", layer)
            for matrix in matrices:
                vertices = list(matrix.transform_vertices(vertices))
            counts["vertices"] += len(vertices)
            if not vertices or any(not all(math.isfinite(c) for c in v) or abs(v.z * factor) > 1e-8 for v in vertices):
                raise ValueError("Invalid DXF: nonfinite or nonplanar geometry")
            points = [[v.x * factor, v.y * factor] for v in vertices]
            if closed and len(points) > 1 and points[0] == points[-1]:
                points.pop()
            if _validate_closed and closed and (len(points) < 3 or not Polygon(points).is_valid):
                raise ValueError("Invalid DXF: invalid closed geometry")
            entities.append(dict(source_type=kind, layer=layer, closed=closed, points_mm=points))
    collect(doc.modelspace())
    report["layers"] = {k: dict(v) for k, v in sorted(layers.items())}
    report["blocks"] = dict(blocks)
    report["curves"] = dict(curves)
    report["exported_entities"] = len(entities)
    return dict(schema="dxf-geometry/v1", units="mm", entities=entities, report=report)


def geometry_dxf_bytes(geometry):
    """Export adapter geometry in mm, without changing placement or orientation."""
    if geometry.get("schema") != "dxf-geometry/v1" or geometry.get("units") != "mm":
        raise ValueError("Expected dxf-geometry/v1 in mm")
    doc = ezdxf.new("R2010")
    doc.units = 4
    for entity in geometry["entities"]:
        points = entity["points_mm"]
        if (not points or any(len(p) != 2 or not all(math.isfinite(c) for c in p) for p in points)
                or entity["closed"] and len(points) < 3):
            raise ValueError("Invalid finite planar geometry")
        layer = entity["layer"]
        if layer not in doc.layers:
            doc.layers.new(layer)
        attrs = {"layer": layer}
        if len(points) == 1:
            doc.modelspace().add_point(points[0], dxfattribs=attrs)
        else:
            doc.modelspace().add_lwpolyline(points, close=entity["closed"], dxfattribs=attrs)
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def pattern_geometry_dxf_bytes(doc, *, length_tolerance_mm=1.0):
    """Export validated v1 point/contour geometry; return bytes and loss report.

    This is an explicit geometry-only conversion. Review decisions, grading,
    construction, notches and grain metadata remain in the source JSON.
    """
    from .assembly_v1 import validate_assembly_v1
    if "pattern_geometry" not in doc or any(i["level"] == "error" for i in
            validate_assembly_v1(doc, length_tolerance_mm=length_tolerance_mm)):
        raise ValueError("Expected valid v1 with pattern_geometry")
    target = ezdxf.new("R2010")
    target.units = 4
    target.layers.new("1")
    target.layers.new("11")
    count = 0
    for i, panel in enumerate(doc["pattern_geometry"]["panels"]):
        # Never encode arbitrary user IDs as DXF layer or block names.
        block = target.blocks.new(f"SCAN_PANEL_{i + 1:06d}")
        target.modelspace().add_blockref(block.name, (0, 0))
        points = {p["id"]: p["xy_mm"] for p in panel["points"]}
        for contour in panel["contours"]:
            block.add_lwpolyline([points[p] for p in contour["point_ids"]], close=True,
                                 dxfattribs={"layer": "11" if contour["kind"] == "hole" else "1"})
            count += 1
    stream = StringIO()
    target.write(stream)
    report = dict(units="mm", scale_to_mm=1.0, exported_entities=count,
                  losses=["IDs, grading, review, construction, notch and grain metadata are not encoded"],
                  panel_blocks={p['panel_id']: f"SCAN_PANEL_{i + 1:06d}"
                                for i, p in enumerate(doc['pattern_geometry']['panels'])})
    return stream.getvalue().encode("utf-8"), report


def dxf_to_pattern_geometry(path_or_bytes, *, assume_units=None, length_tolerance_mm=1.0):
    """Read original DXF via the assembly policy, preserving source provenance.

    Single-level piece blocks retain their own metadata and hole scope. Nested
    blocks and conflicting metadata are refused rather than flattened across
    scopes. Curves use the assembly reader's fixed 0.1 mm chord tolerance.
    """
    from .assembly import dxf_to_assembly
    from .assembly_v1 import migrate_v0
    from .assembly_geometry import add_pattern_geometry
    if not isinstance(path_or_bytes, bytes) and Path(path_or_bytes).stat().st_size > MAX_BYTES:
        raise ValueError("DXF exceeds 10 MiB limit")
    raw = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("DXF exceeds 10 MiB limit")
    try:
        source = ezdxf.decode_base64(base64.b64encode(raw))
    except Exception as exc:
        raise ValueError("Invalid DXF") from exc
    geometry = read_dxf_geometry(raw, assume_units=assume_units, _raw_doc=source)
    def check(space, nested=False):
        labels = {}
        for entity in space:
            if entity.dxftype() == 'INSERT':
                if nested:
                    raise ValueError('Nested block scopes are not supported for v1 import')
                check(entity.block(), True)
            elif entity.dxftype() in ('TEXT', 'MTEXT'):
                text = entity.dxf.text if entity.dxftype() == 'TEXT' else entity.plain_text()
                match = re.match(r'^\s*(Piece Name|Size Name|Size|Quantity|Fold)\s*:\s*(.*?)\s*$', text, re.I)
                if match:
                    key = match[1].lower()
                    key = 'size' if key == 'size name' else key
                    value = match[2].upper() if key == 'fold' else match[2]
                    if key in labels and labels[key] != value:
                        raise ValueError('Conflicting metadata in DXF scope')
                    labels[key] = value
    check(source.modelspace())
    assembly = dxf_to_assembly(raw, assume_units=assume_units, _raw_doc=source,
                               _validated=True)
    result = add_pattern_geometry(migrate_v0(assembly), length_tolerance_mm=length_tolerance_mm)
    report = geometry['report']
    report['source'].update(result['source'])
    report['provenance'] = 'Original DXF SHA, units, assumptions and entity handles retained'
    report['losses'].append(dict(entity_type='ASSEMBLY_PROJECTION', layer=None,
                                reason='Only supported assembly panel contours, holes and marks are represented; generic inventory is separate'))
    return result, report
