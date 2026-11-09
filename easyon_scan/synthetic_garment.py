"""Deterministic synthetic garment fixture; dimensions are illustrative millimetres."""
import hashlib
import io
import json
from pathlib import Path

import ezdxf

from .assembly import dxf_to_assembly, validate_assembly
from .assembly_v1 import migrate_v0, validate_assembly_v1
from .assembly_eval import evaluate_proposals
from .propose import propose_assembly

# Explicit fixture table, not a size grading rule or real-body measurements.
MEASURES_MM = {
    'P': {'body_width': 180, 'body_height': 260, 'side_width': 55, 'shoulder_width': 180, 'shoulder_height': 45},
    'M': {'body_width': 195, 'body_height': 275, 'side_width': 60, 'shoulder_width': 195, 'shoulder_height': 48},
    'G': {'body_width': 210, 'body_height': 290, 'side_width': 65, 'shoulder_width': 210, 'shoulder_height': 51},
}


def _rectangle(width, height):
    return [(0, 0), (width, 0), (width, height), (0, height)]


def _dxf(size):
    m = MEASURES_MM[size]
    pieces = [
        ('Front', m['body_width'], m['body_height']),
        ('Back', m['body_width'], m['body_height']),
        ('Side Left', m['side_width'], m['body_height']),
        ('Side Right', m['side_width'], m['body_height']),
        ('Shoulder Left', m['shoulder_width'], m['shoulder_height']),
        ('Shoulder Right', m['shoulder_width'], m['shoulder_height']),
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
        block.add_lwpolyline(_rectangle(width, height), close=True, dxfattribs={'layer': '1'})
        block.add_lwpolyline([(8, 8), (width - 8, 8), (width - 8, height - 8), (8, height - 8)], close=True, dxfattribs={'layer': '14'})
        block.add_point((width / 2, 0), dxfattribs={'layer': '4'})
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
    # Adapter splits the bottom edge at the central notch; side/top edges retain indices.
    pairs = [('Front', 3, 'Side Left', 5), ('Back', 5, 'Side Right', 3),
             ('Front', 4, 'Shoulder Left', 4), ('Back', 4, 'Shoulder Right', 4)]
    result = []
    for left, ai, right, bi in pairs:
        a, b = panels[left]['edges'][ai - 1], panels[right]['edges'][bi - 1]
        result.append({'a': {'edge_id': a['id'], 'start_mm': 0., 'end_mm': a['length_mm']},
                       'b': {'edge_id': b['id'], 'start_mm': 0., 'end_mm': b['length_mm']},
                       'orientation': 'reversed', 'construction': 'plain'})
    return result


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


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
        v1 = migrate_v0(v0)
        errors = [i for i in validate_assembly_v1(v1, length_tolerance_mm=1) if i['level'] == 'error']
        if errors:
            raise ValueError(f'Invalid synthetic v1: {errors}')
        truth = _truth(v0)
        paired = {ref['edge_id'] for pair in truth for ref in (pair['a'], pair['b'])}
        free_edges = [edge['id'] for panel in v0['panels'] for edge in panel['edges'] if edge['id'] not in paired]
        candidates = propose_assembly(v0)['proposals']
        normalized = [{'id': p['id'], 'a': {'edge_id': p['a'], 'start_mm': 0., 'end_mm': next(e['length_mm'] for q in v0['panels'] for e in q['edges'] if e['id'] == p['a'])},
                       'b': {'edge_id': p['b'], 'start_mm': 0., 'end_mm': next(e['length_mm'] for q in v0['panels'] for e in q['edges'] if e['id'] == p['b'])},
                       'orientation': p['orientation'], 'construction': p['construction'], 'score': p['score']} for p in candidates]
        report = evaluate_proposals(normalized, truth, endpoint_tolerance_mm=1)
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
