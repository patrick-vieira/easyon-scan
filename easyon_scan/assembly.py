"""Experimental, planar garment-assembly/v0 DXF adapter (all geometry in mm)."""
import hashlib
import base64
import math
from pathlib import Path

import ezdxf
from ezdxf import path as dxfpath
from shapely.geometry import LineString, Point, Polygon

FLATTEN_MM = 0.1
CORNER_DEGREES = 30
UNITS = {1: ('in', 25.4), 2: ('ft', 304.8), 4: ('mm', 1.),
         5: ('cm', 10.), 6: ('m', 1000.)}


def _provenance(entity):
    return dict(handle=entity.dxf.handle, layer=entity.dxf.layer,
                dxftype=entity.dxftype())


def _points(entity, factor):
    kind = entity.dxftype()
    if kind == 'POINT':
        vertices = [entity.dxf.location]
    else:
        vertices = dxfpath.make_path(entity).flattening(FLATTEN_MM / factor)
    return [[float(v.x * factor), float(v.y * factor)] for v in vertices]


def _edges(ring, marks, panel_id, provenance):
    line = LineString(ring)
    distances = [0.]
    for a, b in zip(ring, ring[1:]):
        distances.append(distances[-1] + math.dist(a, b))
    cuts = {0., line.length}
    for i in range(len(ring) - 1):
        prev, here, nxt = ring[i-1 if i else -2], ring[i], ring[i+1]
        u = [here[k]-prev[k] for k in (0, 1)]
        v = [nxt[k]-here[k] for k in (0, 1)]
        norm = math.hypot(*u) * math.hypot(*v)
        if norm and math.degrees(math.acos(max(-1., min(1., sum(a*b for a,b in zip(u,v))/norm)))) >= CORNER_DEGREES:
            cuts.add(distances[i])
    projected = [line.project(Point(p)) for p in marks]
    cuts.update(projected)
    # Insert projected notch vertices so edge indices refer to the output ring.
    positions = sorted(set(distances) | cuts)
    vertices = [list(line.interpolate(s).coords[0]) for s in positions]
    indices = {s: i for i, s in enumerate(positions)}
    cuts = sorted(cuts)
    edges = []
    for start, end in zip(cuts, cuts[1:]):
        if end-start < 1e-8:
            continue
        # A boundary notch belongs to the following edge, except at closure.
        notches = [dict(at_mm=s-start, kind='notch') for s in projected
                   if start <= s < end or (end == line.length and s == end)]
        edges.append(dict(id=f'{panel_id}:e{len(edges)+1}', from_mm=start, to_mm=end,
                          length_mm=end-start, start_vertex=indices[start],
                          end_vertex=indices[end], notches=notches, provenance=provenance.copy()))
    return vertices, edges


def dxf_to_assembly(path_or_bytes, *, size=None, assume_units=None, seam_allowance_mm=None):
    """Read a DXF path or bytes; unitless inputs require explicit units.

    Curves flatten at 0.1 mm chord tolerance; turns >=30 degrees split edges.
    Offsets are hypotheses only and are rejected if empty or disconnected.
    """
    raw = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    doc = ezdxf.decode_base64(base64.b64encode(raw))
    code = int(doc.header.get('$INSUNITS', 0))
    if code == 0:
        assumed = {name: scale for name, scale in UNITS.values()}
        if assume_units not in assumed:
            raise ValueError('Unitless DXF ($INSUNITS=0): specify assume_units (mm, cm or in)')
        factor = assumed[assume_units]
        units = 'unitless'
    elif code in UNITS:
        units, factor = UNITS[code]
    else:
        raise ValueError(f'Unsupported $INSUNITS code: {code}')
    if seam_allowance_mm is not None and (not math.isfinite(seam_allowance_mm) or seam_allowance_mm <= 0):
        raise ValueError('seam_allowance_mm must be finite and positive')
    records, annotations, labels = [], [], {}

    def collect(space, scope='model', matrix=None):
        for entity in space:
            kind = entity.dxftype()
            if kind == 'INSERT':
                if matrix is not None:
                    raise ValueError('Nested INSERTs are not supported')
                for insert in entity.multi_insert() if entity.mcount > 1 else [entity]:
                    collect(insert.block(), f'insert:{insert.dxf.handle}', insert.matrix44())
                continue
            if kind == 'TEXT':
                text = entity.dxf.text
                for prefix, key in [('Piece Name:', 'name'), ('Size:', 'size')]:
                    if text.startswith(prefix):
                        labels.setdefault(scope, {})[key] = text[len(prefix):].strip()
                continue
            if kind not in ('LWPOLYLINE', 'POLYLINE', 'SPLINE', 'ARC', 'CIRCLE', 'LINE', 'POINT'):
                continue
            points = _points(entity, factor)
            if matrix is not None:
                # Block transformation is in source units, before metric scaling.
                points = [[v.x*factor, v.y*factor] for v in
                          matrix.transform_vertices([(x/factor,y/factor,0) for x,y in points])]
            if not points:
                continue
            layer = entity.dxf.layer.upper()
            if layer in ('INTERNAL', '8'):
                continue
            record = dict(points=points, scope=scope, provenance=_provenance(entity), layer=layer)
            if layer in ('NOTCH', '4', 'GRAIN', '7'):
                annotations.append(record)
            elif kind in ('LWPOLYLINE', 'POLYLINE', 'SPLINE', 'CIRCLE'):
                closed = (kind == 'CIRCLE' or bool(getattr(entity, 'closed', False)) or
                          bool(getattr(entity, 'is_closed', False)))
                if closed:
                    if points[0] != points[-1]:
                        points.append(points[0][:])
                    record['polygon'] = Polygon(points)
                    records.append(record)
    collect(doc.modelspace())
    sew_layers = {'SEWLINE', 'SEW', 'STITCH', 'STITCHLINE'}
    outlines = [r for r in records if r['layer'] not in sew_layers]
    panels = []
    for record in outlines:
        poly = record['polygon']
        # Containment, rather than winding, identifies holes. Invalid outlines
        # are retained for validation, never silently repaired.
        parents = [r for r in outlines if r is not record and r['scope'] == record['scope']
                   and r['polygon'].is_valid and poly.is_valid and r['polygon'].contains(poly)]
        if parents:
            continue
        pid = f'panel-{len(panels)+1}'
        holes = [r['points'] for r in outlines if r is not record and r['scope'] == record['scope']
                 and poly.is_valid and r['polygon'].is_valid and poly.contains(r['polygon'])]
        relevant = [a for a in annotations if a['scope'] == record['scope']]
        marks, grain = [], None
        for a in relevant:
            pts = a['points']
            if a['layer'] in ('NOTCH', '4'):
                # Plain notch triangles: base, tip, base, closure.
                p = pts[0] if len(pts) == 1 else [(pts[0][k]+pts[2][k])/2 for k in (0,1)]
                nearest = min((r for r in outlines if r['scope'] == record['scope']),
                              key=lambda r: LineString(r['points']).distance(Point(p)))
                if nearest is record:
                    marks.append(p)
            elif poly.is_valid and poly.covers(Point(pts[0])) and len(pts) >= 2:
                # Longest pair captures the axis of the exported ink arrow.
                a0, b0 = max(((a,b) for a in pts for b in pts), key=lambda pair: math.dist(*pair))
                length = math.dist(a0,b0)
                if length:
                    grain = [(b0[k]-a0[k])/length for k in (0,1)]
        ring, edges = _edges(record['points'], marks, pid, record['provenance'])
        sew = [r for r in records if r['scope'] == record['scope'] and r['layer'] in sew_layers
               and poly.is_valid and poly.contains(r['polygon'])]
        if len(sew) > 1:
            raise ValueError(f'Ambiguous sewline contours for {pid}')
        sewline, status = (sew[0]['points'], 'from_dxf') if sew else (None, 'missing')
        if sewline is None and seam_allowance_mm is not None:
            if not poly.is_valid:
                raise ValueError('Cannot offset an invalid cutline')
            offset = Polygon(ring, holes).buffer(-seam_allowance_mm, join_style=2)
            if offset.is_empty or offset.geom_type != 'Polygon':
                raise ValueError('Allowance produces an empty or disconnected sewline')
            sewline, status = [list(p) for p in offset.exterior.coords], 'hypothesis_offset'
        panels.append(dict(id=pid, name=labels.get(record['scope'], {}).get('name', record['provenance']['layer']),
                           cutline=ring, sewline=sewline, sewline_status=status, grain=grain,
                           on_fold=False, holes=holes, edges=edges, provenance=record['provenance']))
    sizes = {label['size'] for label in labels.values() if 'size' in label}
    if size is None and len(sizes) > 1:
        raise ValueError('Multiple DXF sizes: select a single-size input explicitly')
    if size is not None and sizes and sizes != {size}:
        raise ValueError('Requested size does not match DXF size labels')
    result = dict(schema='garment-assembly/v0', source=dict(file_sha256=hashlib.sha256(raw).hexdigest(),
                  units=units, insunits_code=code, size=size if size is not None else next(iter(sizes), None)),
                  panels=panels, seams=[], openings=[], review=dict(status='pending', reviewer=None, notes=[]))
    if code == 0:
        result['source']['assumed_units'] = assume_units
    return result


def validate_assembly(doc):
    """Return structural and geometric issues without optional dependencies."""
    import json
    from importlib.resources import files
    schema = json.loads(files('easyon_scan').joinpath('schemas/garment-assembly-v0.schema.json').read_text())
    issues = []

    def issue(code, message, panel=None, level='error'):
        issues.append(dict(level=level, code=code, panel=panel, message=message))

    def structure(value, spec, location='$'):
        if '$ref' in spec:
            spec = schema['$defs'][spec['$ref'].split('/')[-1]]
        if 'anyOf' in spec:
            # Nullable structures are the only union used by this schema.
            spec = next((s for s in spec['anyOf'] if s.get('type') != 'null'), {}) if value is not None else {'type': 'null'}
        types = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
                 'string': lambda v: isinstance(v, str), 'boolean': lambda v: isinstance(v, bool),
                 'integer': lambda v: type(v) is int,
                 'number': lambda v: type(v) in (int, float) and math.isfinite(v), 'null': lambda v: v is None}
        if 'type' in spec and not types[spec['type']](value):
            issue('structure', f'{location}: expected {spec["type"]}')
            return
        if 'const' in spec and value != spec['const'] or 'enum' in spec and value not in spec['enum']:
            issue('structure', f'{location}: invalid value')
        if isinstance(value, dict):
            for key in spec.get('required', []):
                if key not in value:
                    issue('structure', f'{location}: missing {key}')
            if spec.get('additionalProperties') is False:
                for key in value.keys() - spec.get('properties', {}).keys():
                    issue('structure', f'{location}: unexpected {key}')
            for key, child in spec.get('properties', {}).items():
                if key in value:
                    structure(value[key], child, f'{location}.{key}')
        elif isinstance(value, list):
            if len(value) < spec.get('minItems', 0) or len(value) > spec.get('maxItems', math.inf):
                issue('structure', f'{location}: invalid array length')
            for i, child in enumerate(value):
                structure(child, spec.get('items', {}), f'{location}[{i}]')
        elif type(value) in (int, float):
            if value < spec.get('minimum', -math.inf) or value > spec.get('maximum', math.inf):
                issue('structure', f'{location}: number outside range')
        elif isinstance(value, str) and 'pattern' in spec:
            import re
            if not re.fullmatch(spec['pattern'], value):
                issue('structure', f'{location}: invalid text format')
    structure(doc, schema)
    if issues:
        return issues
    if not doc['panels']:
        issue('no_panels', 'No supported closed outlines found')
    ids, edges = set(), {}
    for panel in doc['panels']:
        pid = panel['id']
        if pid in ids:
            issue('duplicate_id', 'Duplicate panel ID', pid)
        ids.add(pid)
        for label, ring in [('cutline', panel['cutline']), *[('hole', h) for h in panel['holes']],
                            *([('sewline', panel['sewline'])] if panel['sewline'] is not None else [])]:
            if ring[0] != ring[-1]:
                issue('open_contour', f'{label} must repeat its first vertex', pid)
            polygon = Polygon(ring)
            if not polygon.is_valid:
                issue('self_intersection', f'{label} is invalid or self-intersects', pid)
            if polygon.area <= 0:
                issue('area', f'{label} area must be positive', pid)
        polygon = Polygon(panel['cutline'], panel['holes'])
        if not polygon.is_valid or polygon.area <= 0:
            issue('panel_geometry', 'Invalid panel including holes', pid)
        if (panel['sewline'] is None) != (panel['sewline_status'] == 'missing'):
            issue('sewline_status', 'Sewline and status disagree', pid)
        if panel['grain'] is None:
            issue('grain_missing', 'Grain direction is undefined', pid, 'warning')
        elif math.hypot(*panel['grain']) == 0:
            issue('grain_zero', 'Grain direction must be nonzero', pid)
        perimeter = LineString(panel['cutline']).length
        tolerance = max(1e-5, perimeter*1e-6)
        total, cursor = 0., 0.
        for edge in panel['edges']:
            if edge['id'] in edges:
                issue('duplicate_id', 'Duplicate edge ID', pid)
            edges[edge['id']] = pid
            total += edge['length_mm']
            if abs(edge['from_mm']-cursor) > tolerance or abs(edge['to_mm']-edge['from_mm']-edge['length_mm']) > tolerance:
                issue('edge_range', 'Edges must cover consecutive arc-length intervals', pid)
            cursor = edge['to_mm']
            for key, distance in [('start_vertex', edge['from_mm']), ('end_vertex', edge['to_mm'])]:
                index = edge[key]
                if index >= len(panel['cutline']):
                    issue('edge_vertex', 'Edge vertex index is out of range', pid)
                    continue
                measured = LineString(panel['cutline'][:index+1]).length if index else 0.
                if abs(measured-distance) > tolerance:
                    issue('edge_vertex', 'Edge vertex does not match arc-length position', pid)
            for notch in edge['notches']:
                if not 0 <= notch['at_mm'] <= edge['length_mm']:
                    issue('notch_range', 'Notch lies outside edge', pid)
        if abs(total-perimeter) > tolerance or abs(cursor-perimeter) > tolerance:
            issue('edge_perimeter', 'Edge lengths must sum to cutline perimeter', pid)
    used = set()
    seam_ids = set()
    for seam in doc['seams']:
        if seam['id'] in seam_ids:
            issue('duplicate_id', 'Duplicate seam ID')
        seam_ids.add(seam['id'])
        for endpoint in (seam['a'], seam['b']):
            if endpoint not in edges:
                issue('seam_reference', f'Unknown edge {endpoint}')
            if endpoint in used:
                issue('edge_reused', f'Edge {endpoint} used more than once', edges.get(endpoint))
            used.add(endpoint)
    return issues
