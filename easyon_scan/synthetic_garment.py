"""Deterministic synthetic garment fixture; dimensions are illustrative millimetres."""
import hashlib
import io
import json
from pathlib import Path

import ezdxf
from shapely.geometry import LineString

from .assembly import dxf_to_assembly, validate_assembly
from .assembly_v1 import _digest, migrate_v0, validate_assembly_v1
from .assembly_eval import _same, evaluate_proposals, ranked_by_edge
from .propose import propose_assembly, sewline_interval_mm

# Explicit fixture table, not a size grading rule or real-body measurements.
MEASURES_MM = {
    'P': {'body_width': 180, 'body_height': 260, 'side_width': 55, 'shoulder_width': 180, 'shoulder_height': 45},
    'M': {'body_width': 195, 'body_height': 275, 'side_width': 60, 'shoulder_width': 195, 'shoulder_height': 48},
    'G': {'body_width': 210, 'body_height': 290, 'side_width': 65, 'shoulder_width': 210, 'shoulder_height': 51},
}


def _rectangle(width, height):
    return [(0, 0), (width, 0), (width, height), (0, height)]


def _outline(width, height, split_top=False):
    points = [(0, 0), (width / 2, 0), (width, 0), (width, height)]
    if split_top:
        points.append((width / 2, height))
    points.append((0, height))
    return points


def _dxf(size):
    m = MEASURES_MM[size]
    pieces = [
        ('Front', m['body_width'], m['body_height']),
        ('Back', m['body_width'], m['body_height']),
        ('Side Left', m['side_width'], m['body_height']),
        ('Side Right', m['side_width'], m['body_height']),
        ('Shoulder Left', m['shoulder_width'] / 2, m['shoulder_height']),
        ('Shoulder Right', m['shoulder_width'] / 2, m['shoulder_height']),
    ]
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        doc = ezdxf.new('R2010')
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous
    doc.units = 4
    for layer in ('1', '14', '4', '7'):
        doc.layers.new(layer)
    for index, (name, width, height) in enumerate(pieces):
        block = doc.blocks.new(f'PIECE_{index + 1}')
        block.add_lwpolyline(_outline(width, height, name in ('Front', 'Back')), close=True, dxfattribs={'layer': '1'})
        inset = 4 if name.startswith('Shoulder') else 8
        block.add_lwpolyline([(inset, inset), (width - inset, inset),
                              (width - inset, height - inset), (inset, height - inset)],
                             close=True, dxfattribs={'layer': '14'})
        if name in ('Front', 'Back'):
            block.add_point((width / 2, 0), dxfattribs={'layer': '4'})
            block.add_point((width / 2, height), dxfattribs={'layer': '4'})
        block.add_line((width / 2, 12), (width / 2, height - 12), dxfattribs={'layer': '7'})
        for row, label in enumerate((f'Piece Name: {name}', f'Size Name: {size}', 'Quantity: 1')):
            block.add_text(label, dxfattribs={'layer': '0', 'height': 3, 'insert': (12, 15 + row * 6)})
        doc.modelspace().add_blockref(block.name, (index * 300, 0))
    stream = io.StringIO()
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        doc.write(stream)
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous
    return stream.getvalue().encode('utf-8')


def _truth(assembly):
    panels = {p['name']: p for p in assembly['panels']}
    # Each side and shoulder bridge has one edge on the front and one on the back.
    def edge_index(name, axis, first):
        panel = panels[name]
        line = LineString(panel['cutline'])
        found = []
        for index, edge in enumerate(panel['edges'], 1):
            start, end = (line.interpolate(edge[k]) for k in ('from_mm', 'to_mm'))
            if (axis == 'vertical' and abs(start.x - end.x) < 1e-6 or
                    axis == 'horizontal' and abs(start.y - end.y) < 1e-6):
                found.append((min(start.x, end.x) if axis == 'vertical' else min(start.y, end.y), index))
        found.sort()
        if len(found) < 2:
            raise ValueError(f'Nonparallel seam edge in {name}')
        return found[0 if first else -1][1]
    sl_right = edge_index('Side Left', 'vertical', False)
    sl_left = edge_index('Side Left', 'vertical', True)
    sr_right = edge_index('Side Right', 'vertical', False)
    sr_left = edge_index('Side Right', 'vertical', True)
    shl_bottom = edge_index('Shoulder Left', 'horizontal', True)
    shl_top = edge_index('Shoulder Left', 'horizontal', False)
    shr_bottom = edge_index('Shoulder Right', 'horizontal', True)
    shr_top = edge_index('Shoulder Right', 'horizontal', False)
    # The body edge indices are part of this fixture's topology contract.
    for name in ('Front', 'Back'):
        panel = panels[name]
        line = LineString(panel['cutline'])
        left = min(x for x, _ in panel['cutline'])
        right = max(x for x, _ in panel['cutline'])
        bottom = min(y for _, y in panel['cutline'])
        top = max(y for _, y in panel['cutline'])
        middle = (left + right) / 2
        expected = {3: ((right, bottom), (right, top)),
                    4: ((right, top), (middle, top)),
                    5: ((middle, top), (left, top)),
                    6: ((left, top), (left, bottom))}
        for index, (first, last) in expected.items():
            edge = panel['edges'][index - 1]
            start, end = (line.interpolate(edge[key]) for key in ('from_mm', 'to_mm'))
            if max(abs(start.x - first[0]), abs(start.y - first[1]),
                   abs(end.x - last[0]), abs(end.y - last[1])) > 1e-6:
                raise ValueError(f'Unexpected body edge position: {name}/e{index}')
    pairs = [('Front', 6, 'Side Left', sl_right), ('Back', 6, 'Side Left', sl_left),
             ('Front', 3, 'Side Right', sr_left), ('Back', 3, 'Side Right', sr_right),
             ('Front', 5, 'Shoulder Left', shl_top), ('Back', 5, 'Shoulder Left', shl_bottom),
             ('Front', 4, 'Shoulder Right', shr_top), ('Back', 4, 'Shoulder Right', shr_bottom)]
    result = []
    for left, ai, right, bi in pairs:
        a, b = panels[left]['edges'][ai - 1], panels[right]['edges'][bi - 1]
        if abs(a['length_mm'] - b['length_mm']) > 1e-6:
            raise ValueError(f'Incompatible seam lengths: {left}/{right}')
        def direction(panel, edge):
            line = LineString(panel['cutline'])
            start, end = (line.interpolate(edge[k]) for k in ('from_mm', 'to_mm'))
            return (end.x - start.x, end.y - start.y)
        va, vb = direction(panels[left], a), direction(panels[right], b)
        dot = va[0] * vb[0] + va[1] * vb[1]
        if abs(abs(dot) - a['length_mm'] * b['length_mm']) > 1e-5:
            raise ValueError(f'Nonparallel seam: {left}/{right}')
        result.append({'a': {'edge_id': a['id'], 'start_mm': 0., 'end_mm': a['length_mm']},
                       'b': {'edge_id': b['id'], 'start_mm': 0., 'end_mm': b['length_mm']},
                       'orientation': 'same' if dot > 0 else 'reversed', 'construction': 'plain'})
    return result


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def _rotate_sewline_away_from_seams(panel, paired_ids):
    """Put the v1 sewline origin inside a free edge so seam arcs are linear."""
    cut = LineString(panel['cutline'])
    sew = LineString(panel['sewline'])
    vertices = panel['sewline']
    for edge in panel['edges']:
        if edge['id'] in paired_ids:
            continue
        middle = cut.interpolate((edge['from_mm'] + edge['to_mm']) / 2)
        origin = sew.project(middle)
        # The projected origin lies on one segment of this closed ring.
        cumulative = 0.
        for i, (a, b) in enumerate(zip(vertices, vertices[1:])):
            segment = LineString([a, b])
            if cumulative - 1e-8 <= origin <= cumulative + segment.length + 1e-8:
                if abs(origin - cumulative) <= 1e-8:
                    start = i
                    rotated = vertices[start:-1] + vertices[:start] + [vertices[start]]
                elif abs(origin - cumulative - segment.length) <= 1e-8:
                    start = (i + 1) % (len(vertices) - 1)
                    rotated = vertices[start:-1] + vertices[:start] + [vertices[start]]
                else:
                    point = list(sew.interpolate(origin).coords[0])
                    rotated = [point] + vertices[i + 1:-1] + vertices[:i + 1] + [point]
                old = panel['sewline']
                panel['sewline'] = rotated
                length = LineString(rotated).length
                if all(0 <= (arc := sewline_interval_mm(panel, item))[0] < arc[1] <= length + 1e-8
                       for item in panel['edges'] if item['id'] in paired_ids):
                    return
                panel['sewline'] = old
            cumulative += segment.length
    raise ValueError(f'No free sewline origin for {panel["id"]}')


def generate_fixture(out, sizes=('P', 'M', 'G')):
    """Write one DXF and review-only assembly/evaluation bundle per selected size."""
    if not sizes or len(set(sizes)) != len(sizes) or any(s not in MEASURES_MM for s in sizes):
        raise ValueError('sizes must be a nonempty unique subset of P,M,G')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for size in sizes:
        raw = _dxf(size)
        v0 = dxf_to_assembly(raw, size=size)
        errors = [i for i in validate_assembly(v0) if i['level'] == 'error']
        if errors:
            raise ValueError(f'Invalid synthetic v0: {errors}')
        truth = _truth(v0)
        proposed = propose_assembly(v0, max_per_edge=None)
        v1 = migrate_v0(v0)
        paired_ids = {ref['edge_id'] for pair in truth for ref in (pair['a'], pair['b'])}
        for panel in v1['panels']:
            _rotate_sewline_away_from_seams(panel, paired_ids)
        edge_map = {e['id']: e for panel in v1['panels'] for e in panel['edges']}
        for pair in truth:
            for ref in (pair['a'], pair['b']):
                edge = edge_map[ref['edge_id']]
                panel = next(p for p in v1['panels'] if edge in p['edges'])
                edge['sewline_interval_mm'] = sewline_interval_mm(panel, edge)
        for index, pair in enumerate(truth, 1):
            a, b = pair['a'], pair['b']
            matches = [{'a_mm': 0., 'b_mm': b['end_mm'] if pair['orientation'] == 'reversed' else 0.},
                       {'a_mm': a['end_mm'], 'b_mm': 0. if pair['orientation'] == 'reversed' else b['end_mm']}]
            def sew_length(ref):
                edge = edge_map[ref['edge_id']]
                lo, hi = edge['sewline_interval_mm']
                return (hi - lo) * (ref['end_mm'] - ref['start_mm']) / edge['length_mm']
            proposal = dict(id=f'fixture:{index}', **pair, matches=matches, allowance_mm={'a': None, 'b': None},
                            ease_mm=sew_length(a) - sew_length(b), score=1., signals=[], model_version='synthetic-truth/v1',
                            created_at=None, status='proposed')
            proposal['content_sha256'] = _digest(proposal)
            v1['proposals'].append(proposal)
            v1['review']['decisions'].append(dict(action='accept', proposal_id=proposal['id'],
                before_sha256=proposal['content_sha256'], reviewer='synthetic-fixture',
                at='2026-01-01T00:00:00Z', reason='Known synthetic geometry'))
            v1['seams'].append({k: proposal[k] for k in ('a', 'b', 'orientation', 'matches', 'construction', 'allowance_mm', 'ease_mm')}
                               | {'id': f'seam:{index}', 'proposal_id': proposal['id']})
        errors = [i for i in validate_assembly_v1(v1, length_tolerance_mm=1) if i['level'] == 'error']
        if errors:
            raise ValueError(f'Invalid synthetic v1: {errors}')
        paired = {ref['edge_id'] for pair in truth for ref in (pair['a'], pair['b'])}
        free_edges = [edge['id'] for panel in v0['panels'] for edge in panel['edges'] if edge['id'] not in paired]
        candidates = proposed['proposals']
        normalized = [{'id': p['id'], 'a': {'edge_id': p['a'], 'start_mm': 0., 'end_mm': next(e['length_mm'] for q in v0['panels'] for e in q['edges'] if e['id'] == p['a'])},
                       'b': {'edge_id': p['b'], 'start_mm': 0., 'end_mm': next(e['length_mm'] for q in v0['panels'] for e in q['edges'] if e['id'] == p['b'])},
                       'orientation': p['orientation'], 'construction': p['construction'], 'score': p['score']} for p in candidates]
        report = evaluate_proposals(normalized, truth, endpoint_tolerance_mm=1)
        by_edge = ranked_by_edge(normalized)
        report['pair_diagnostics'] = []
        for pair in truth:
            matching = [(rank, candidate) for source in (pair['a']['edge_id'], pair['b']['edge_id'])
                        for rank, candidate in enumerate(by_edge.get(source, []), 1)
                        if _same(candidate, pair, 1)]
            rank, candidate = min(matching, key=lambda item: (item[0], item[1]['id'])) if matching else (None, None)
            tied = [] if candidate is None else sorted({p['id'] for source in (pair['a']['edge_id'], pair['b']['edge_id'])
                for p in by_edge.get(source, []) if p['score'] == candidate['score']})
            report['pair_diagnostics'].append({'a': pair['a']['edge_id'], 'b': pair['b']['edge_id'],
                'rank': rank, 'in_top3': rank is not None and rank <= 3,
                'score': None if candidate is None else candidate['score'],
                'score_tied_with': [identifier for identifier in tied if identifier != candidate['id']],
                'rank_rule': 'score_desc_then_id_asc; candidate selected at either edge',
                'orientation': None if candidate is None else candidate['orientation'],
                'orientation_correct': candidate is not None and candidate['orientation'] == pair['orientation'],
                'construction': None if candidate is None else candidate['construction'],
                'failure': 'pair_missing' if candidate is None else
                           'rank_below_3' if rank > 3 else
                           'orientation_unknown_or_wrong' if candidate['orientation'] != pair['orientation'] else
                           'construction_unresolved' if candidate['construction'] != pair['construction'] else None})
        artifacts = {f'{size}.dxf': raw, f'{size}.assembly-v0.json': _json_bytes(v0),
                     f'{size}.assembly-v1.json': _json_bytes(v1), f'{size}.truth.json': _json_bytes({'pairs': truth, 'free_edges': free_edges}),
                     f'{size}.evaluation.json': _json_bytes(report)}
        for name, data in artifacts.items():
            (out / name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = {'schema': 'synthetic-garment-fixture/v1', 'sizes': list(sizes), 'measures_mm': {s: MEASURES_MM[s] for s in sizes},
                'sha256': hashes, 'note': 'Synthetic contract fixture only; no real-photo accuracy or CAD compatibility claim.'}
    (out / 'manifest.json').write_bytes(_json_bytes(manifest))
    return manifest
