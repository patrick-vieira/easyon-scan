"""Pure adapters for the optional, experimental pattern-geometry/v1 extension.

Coordinates and point grading deltas are millimetres. No grading is inferred.
"""
import copy
import math

from shapely.geometry import LinearRing, Polygon


def add_pattern_geometry(doc, *, length_tolerance_mm=1.0):
    """Copy v1 into an ID-linked representation without guessing construction.

    Existing extension data is rejected rather than overwritten. Seam allowances
    and point grading stay unknown even when a hypothetical sewline exists.
    length_tolerance_mm is forwarded to v1 validation (default 1 mm).
    """
    from .assembly_v1 import validate_assembly_v1
    if doc.get('schema') != 'garment-assembly/v1' or 'pattern_geometry' in doc:
        raise ValueError('Expected garment-assembly/v1 without pattern_geometry')
    if any(i['level'] == 'error' for i in validate_assembly_v1(doc, length_tolerance_mm=length_tolerance_mm)):
        raise ValueError('Invalid garment-assembly/v1')
    result = copy.deepcopy(doc)
    panels = []
    for panel in doc['panels']:
        pid = panel['id']
        item = dict(panel_id=pid, points=[], edges=[], contours=[], notches=[],
                    grain=copy.deepcopy(panel['grain']),
                    fold_edge_ids=None if panel['on_fold'] else [])
        for ri, ring in enumerate([panel['cutline'], *panel['holes']]):
            prefix = f'{pid}:r{ri}'
            vertices = ring[:-1]
            pids = [f'{prefix}:p{i}' for i in range(len(vertices))]
            eids = [f'{prefix}:e{i}' for i in range(len(vertices))]
            item['points'].extend(dict(id=point_id, xy_mm=copy.deepcopy(xy),
                                       kind='unknown', grading=[])
                                  for point_id, xy in zip(pids, vertices))
            item['contours'].append(dict(id=prefix, kind='outer' if ri == 0 else 'hole',
                                         point_ids=pids, edge_ids=eids))
            distances = [0.]
            segment_lengths = []
            for a, b in zip(ring, ring[1:]):
                length = math.dist(a, b)
                if length <= 1e-8:
                    raise ValueError(f'Near-zero geometry edge in {prefix}')
                distances.append(distances[-1] + length)
                segment_lengths.append(length)
            for i in range(len(vertices)):
                source = next((e for e in panel['edges']
                               if e['start_vertex'] <= i < e['end_vertex']), None) if ri == 0 else None
                item['edges'].append(dict(id=eids[i], start_point_id=pids[i],
                                          end_point_id=pids[(i+1) % len(pids)],
                                          source_edge_id=source['id'] if source else None,
                                          allowance_mm=None))
            if ri == 0:
                for source in panel['edges']:
                    start, end = source['start_vertex'], source['end_vertex']
                    for ni, notch in enumerate(source['notches']):
                        # v0 permits small differences between declared arc lengths
                        # and vertex positions. Clamp within this source edge,
                        # retaining its ownership even at shared endpoints.
                        if not 0 <= start < end < len(distances):
                            raise ValueError(f"Cannot map notch on {source['id']}")
                        at = min(distances[end], max(distances[start],
                                                     source['from_mm'] + notch['at_mm']))
                        for boundary in distances[start:end+1]:
                            if math.isclose(at, boundary, rel_tol=0., abs_tol=1e-8):
                                at = boundary
                                break
                        for i in range(start, end):
                            if at < distances[i+1] or i == end-1:
                                local = min(segment_lengths[i], max(0., at-distances[i]))
                                item['notches'].append(dict(id=f"{source['id']}:notch{ni}",
                                                            edge_id=eids[i], at_mm=local,
                                                            kind=notch['kind']))
                                break
                        else:
                            raise ValueError(f"Cannot preserve notch {source['id']}:{ni}")
        expected_notches = sum(len(e['notches']) for e in panel['edges'])
        if len(item['notches']) != expected_notches:
            raise ValueError(f'Notch count changed for {pid}')
        panels.append(item)
    result['pattern_geometry'] = dict(schema='pattern-geometry/v1', units='mm', panels=panels)
    return result


def remove_pattern_geometry(doc):
    """Copy a v1 document, dropping only the optional geometry extension.

    Extension edits are deliberately not applied to the legacy cutline or review
    snapshots. This conversion preserves the common v1 fields exactly.
    """
    if doc.get('schema') != 'garment-assembly/v1':
        raise ValueError('Expected garment-assembly/v1')
    result = copy.deepcopy(doc)
    result.pop('pattern_geometry', None)
    return result


def _validate_geometry(doc):
    """Semantic checks after assembly_v1 has checked the JSON structure."""
    issues = []
    seen = set()
    def issue(code, message, panel, entity):
        issues.append(dict(level='error', code=code, panel=panel,
                           entity_id=entity, message=message))
    def register(entity, pid):
        if entity in seen:
            issue('geometry_duplicate_id', f'Duplicate ID {entity}', pid, entity)
        seen.add(entity)
    originals = {p['id']: p for p in doc['panels']}
    represented = set()
    for panel in doc['pattern_geometry']['panels']:
        pid = panel['panel_id']
        if 'molde_geometria' in panel and panel['molde_geometria'] != to_molde_geometria(panel):
            issue('geometry_molde_projection', 'Provisional profile must match its geometry panel', pid, pid)
        if pid not in originals or pid in represented:
            issue('geometry_panel_reference', 'Unknown or repeated panel', pid, pid)
        represented.add(pid)
        points = {p['id']: p for p in panel['points']}
        edges = {e['id']: e for e in panel['edges']}
        hole_edges = {eid for c in panel['contours'] if c['kind'] == 'hole' for eid in c['edge_ids']}
        source_edges = {e['id'] for e in originals.get(pid, {}).get('edges', [])}
        for collection in ('points', 'edges', 'contours', 'notches'):
            for value in panel[collection]:
                register(value['id'], pid)
        for edge in panel['edges']:
            if edge['id'] in hole_edges and edge['source_edge_id'] is not None:
                issue('geometry_edge_reference', 'Hole edge cannot reference an outer source edge', pid, edge['id'])
            if edge['source_edge_id'] is not None and edge['source_edge_id'] not in source_edges:
                issue('geometry_edge_reference', 'Unknown source edge', pid, edge['id'])
            if edge['start_point_id'] not in points or edge['end_point_id'] not in points:
                issue('geometry_reference', 'Unknown edge endpoint', pid, edge['id'])
        for point in panel['points']:
            sizes = [rule['size'] for rule in point['grading']]
            if len(set(sizes)) != len(sizes):
                issue('geometry_grading_size', 'Repeated grading size for point', pid, point['id'])
        for edge in panel['edges']:
            if all(edge[k] in points for k in ('start_point_id', 'end_point_id')):
                if math.dist(points[edge['start_point_id']]['xy_mm'], points[edge['end_point_id']]['xy_mm']) <= 1e-8:
                    issue('geometry_zero_edge', 'Edge length must exceed 1e-8 mm', pid, edge['id'])
        used_points, used_edges = [], []
        outer_count = 0
        rings = []
        for contour in panel['contours']:
            ids, links = contour['point_ids'], contour['edge_ids']
            used_points.extend(ids)
            used_edges.extend(links)
            outer_count += contour['kind'] == 'outer'
            if any(p not in points for p in ids) or any(e not in edges for e in links):
                issue('geometry_reference', 'Unknown contour point or edge', pid, contour['id'])
                continue
            if len(set(ids)) != len(ids) or len(set(links)) != len(links):
                issue('geometry_duplicate_reference', 'Repeated point or edge in contour', pid, contour['id'])
            if len(ids) != len(links) or any(
                    edges[e]['start_point_id'] != ids[i] or
                    edges[e]['end_point_id'] != ids[(i+1) % len(ids)]
                    for i, e in enumerate(links[:len(ids)])):
                issue('geometry_open_contour', 'Edges must follow and close the ordered point ring', pid, contour['id'])
            coords = [points[p]['xy_mm'] for p in ids]
            rings.append((contour['kind'], coords))
            ring = LinearRing(coords)
            if not ring.is_simple:
                issue('geometry_self_intersection', 'Contour intersects itself', pid, contour['id'])
            if not ring.is_valid:
                issue('geometry_invalid_contour', 'Degenerate contour', pid, contour['id'])
        if outer_count != 1:
            issue('geometry_outer_count', 'Exactly one outer contour required', pid, pid)
        if outer_count == 1 and len(rings) == len(panel['contours']):
            outer = next(coords for kind, coords in rings if kind == 'outer')
            holes = [coords for kind, coords in rings if kind == 'hole']
            if not Polygon(outer, holes).is_valid:
                issue('geometry_invalid_polygon', 'Invalid outer contour or hole placement', pid, pid)
        if set(used_points) != set(points) or set(used_edges) != set(edges) or len(used_edges) != len(set(used_edges)):
            issue('geometry_unused_entity', 'Every point and edge must belong to one contour', pid, pid)
        for notch in panel['notches']:
            edge = edges.get(notch['edge_id'])
            if edge is None:
                issue('geometry_reference', 'Unknown notch edge', pid, notch['id'])
            elif edge['start_point_id'] in points and edge['end_point_id'] in points:
                length = math.dist(points[edge['start_point_id']]['xy_mm'], points[edge['end_point_id']]['xy_mm'])
                if notch['at_mm'] > length:
                    issue('geometry_notch_range', 'Notch outside edge', pid, notch['id'])
        if panel['fold_edge_ids'] is not None:
            if len(panel['fold_edge_ids']) != len(set(panel['fold_edge_ids'])):
                issue('geometry_duplicate_reference', 'Repeated fold edge', pid, pid)
            for eid in panel['fold_edge_ids']:
                if eid not in edges:
                    issue('geometry_reference', 'Unknown fold edge', pid, eid)
                elif eid in hole_edges:
                    issue('geometry_fold_reference', 'Fold edge must belong to the outer contour', pid, eid)
        if panel['grain'] is not None and math.hypot(*panel['grain']) == 0:
            issue('geometry_grain_zero', 'Grain vector must be nonzero', pid, pid)
    if represented != set(originals):
        issue('geometry_panel_reference', 'Extension must represent every panel', None, None)
    return issues


def to_molde_geometria(panel):
    """Copy a geometry panel to the provisional Portuguese Scan profile.

    This is a local projection, not an implementation of the AK wire format.
    Unknown AK structures are null. Scan-only links live in dados.scan.
    """
    item = copy.deepcopy(panel)
    item.pop('molde_geometria', None)
    return dict(
        perfil='scan-molde/v1-provisional', panel_id=item['panel_id'],
        grade=None,
        pontos=[{k: v for k, v in p.items() if k != 'grading'} for p in item['points']],
        arestas=[{k: v for k, v in e.items() if k not in ('source_edge_id', 'allowance_mm')}
                 for e in item['edges']],
        contornos=item['contours'], piques=item['notches'], fio=item['grain'],
        dobra=item['fold_edge_ids'],
        margem=[dict(edge_id=e['id'], allowance_mm=e['allowance_mm']) for e in item['edges']],
        linhas_internas=None,
        furos=[c['id'] for c in item['contours'] if c['kind'] == 'hole'],
        quantidade=None,
        regras_graduacao=[dict(point_id=p['id'], grading=p['grading']) for p in item['points']],
        incrementos=None, tamanhos=None,
        dados=dict(scan=dict(source_edges=[dict(edge_id=e['id'], source_edge_id=e['source_edge_id'])
                                           for e in item['edges']])))


def from_molde_geometria(molde):
    """Invert a canonical provisional profile without changing the input.

    Only profiles produced by to_molde_geometria (including subsequent edits
    representable in that profile) are supported. Unknown external fields must
    not be silently discarded. Assembly review/provenance are never rebuilt.
    """
    import json
    from importlib.resources import files
    from .assembly_v1 import _structure_issues
    schema = json.loads(files('easyon_scan').joinpath('schemas/garment-assembly-v1.schema.json').read_text())
    issues = _structure_issues(molde, schema['$defs']['molde_geometry'], schema)
    if issues:
        raise ValueError('Invalid provisional MoldeGeometria structure: ' + issues[0]['message'])
    try:
        item = copy.deepcopy(molde)
        grading = {r['point_id']: r['grading'] for r in item['regras_graduacao']}
        margins = {r['edge_id']: r['allowance_mm'] for r in item['margem']}
        sources = {r['edge_id']: r['source_edge_id'] for r in item['dados']['scan']['source_edges']}
        panel = dict(panel_id=item['panel_id'],
                     points=[dict(p, grading=grading[p['id']]) for p in item['pontos']],
                     edges=[dict(e, allowance_mm=margins[e['id']], source_edge_id=sources[e['id']])
                            for e in item['arestas']],
                     contours=item['contornos'], notches=item['piques'], grain=item['fio'],
                     fold_edge_ids=item['dobra'])
        if to_molde_geometria(panel) != item:
            raise ValueError('Noncanonical or unsupported provisional MoldeGeometria')
        # A standalone geometry panel cannot resolve source_edge_id against an
        # assembly. Check local topology here; assembly validation resolves those
        # provenance links against the actual common panels separately.
        original = dict(id=panel['panel_id'], edges=[dict(id=e['source_edge_id'])
                        for e in panel['edges'] if e['source_edge_id'] is not None])
        issues = _validate_geometry(dict(panels=[original], pattern_geometry=dict(panels=[panel])))
        if issues:
            raise ValueError('Invalid provisional MoldeGeometria: ' + issues[0]['code'])
        return copy.deepcopy(panel)
    except (KeyError, TypeError) as exc:
        raise ValueError('Invalid provisional MoldeGeometria') from exc


def add_molde_geometry(doc, *, length_tolerance_mm=1.0):
    """Copy v1 and attach one provisional Portuguese projection per panel.

    Existing pattern geometry is preserved; existing Portuguese projections
    are rejected. Coordinates use the existing mm, y-up planar convention.
    """
    from .assembly_v1 import validate_assembly_v1
    if doc.get('schema') != 'garment-assembly/v1':
        raise ValueError('Expected garment-assembly/v1; migrate_v0 explicitly')
    result = (copy.deepcopy(doc) if 'pattern_geometry' in doc else
              add_pattern_geometry(doc, length_tolerance_mm=length_tolerance_mm))
    for panel in result['pattern_geometry']['panels']:
        if 'molde_geometria' in panel:
            raise ValueError('Existing molde_geometria would be overwritten; remove_molde_geometry explicitly before regenerating')
    if any(i['level'] == 'error' for i in validate_assembly_v1(result, length_tolerance_mm=length_tolerance_mm)):
        raise ValueError('Invalid garment-assembly/v1')
    for panel in result['pattern_geometry']['panels']:
        panel['molde_geometria'] = to_molde_geometria(panel)
    return result


def remove_molde_geometry(doc):
    """Copy v1 and drop only the provisional Portuguese projections."""
    if doc.get('schema') != 'garment-assembly/v1':
        raise ValueError('Expected garment-assembly/v1')
    result = copy.deepcopy(doc)
    for panel in result.get('pattern_geometry', {}).get('panels', []):
        panel.pop('molde_geometria', None)
    return result
