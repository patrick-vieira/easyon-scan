"""Synthetic proposals; scores are not physical accuracy claims."""
import copy
import io

import ezdxf
import pytest
from shapely.geometry import LineString

from easyon_scan.assembly import dxf_to_assembly
from easyon_scan.propose import propose_assembly, sewline_interval_mm, _sew_length
from easyon_scan.synthetic_garment import _dxf, MEASURES_MM


def sample():
    dxf = ezdxf.new()
    dxf.units = 4
    for x in (0, 200, 400):
        dxf.modelspace().add_lwpolyline([(x, 0), (x + 100, 0), (x + 100, 70), (x, 70)], close=True)
    stream = io.StringIO()
    dxf.write(stream)
    doc = dxf_to_assembly(stream.getvalue().encode())
    for panel, name in zip(doc['panels'], ('Frente', 'Costas', 'Wrong')):
        panel['name'] = name
        panel['edges'][0]['label'] = 'lateral'
    return doc


def test_review_only_and_deterministic():
    doc = sample()
    original = copy.deepcopy(doc)
    result = propose_assembly(doc)
    assert doc == original
    assert result == propose_assembly(doc)
    assert result['seams'] == [] and result['review']['status'] == 'pending'
    assert result['none_option']
    assert all(p['construction'] is None and p['status'] == 'proposed' for p in result['proposals'])
    assert all(p['score_label'] == 'review_priority' for p in result['proposals'])
    assert all(any(s['source'] == 'cutline.triage_only' for s in p['signals']) for p in result['proposals'])
    assert len(result['proposals']) <= 3 * sum(len(p['edges']) for p in doc['panels']) // 2
    front, back, wrong = (p['edges'][0]['id'] for p in doc['panels'])
    correct = next(p for p in result['proposals'] if {p['a'], p['b']} == {front, back})
    assert not any({p['a'], p['b']} == {front, wrong} for p in result['proposals'])
    assert correct['score'] > 0


def test_free_used_rejected_and_uncertain():
    doc = sample()
    front, back = (p['edges'][0]['id'] for p in doc['panels'][:2])
    doc['panels'][0]['edges'][0]['label'] = 'bainha'
    assert all(front not in (p['a'], p['b']) for p in propose_assembly(doc)['proposals'])
    doc = sample()
    doc['rejected_pairs'] = [{'a': front, 'b': back}]
    assert all({front, back} != {p['a'], p['b']} for p in propose_assembly(doc)['proposals'])
    doc = sample()
    doc['source']['units'] = 'unitless'
    with pytest.raises(ValueError, match='Uncertain units'):
        propose_assembly(doc)
    doc = sample()
    doc['panels'][0]['cutline'][1] = doc['panels'][0]['cutline'][2]
    with pytest.raises(ValueError, match='Invalid assembly'):
        propose_assembly(doc)


def test_asymmetric_notches_orientation():
    doc = sample()
    a, b = (p['edges'][0] for p in doc['panels'][:2])
    a['notches'] = [{'kind': 'notch', 'at_mm': 20}, {'kind': 'notch', 'at_mm': 65}]
    b['notches'] = [{'kind': 'notch', 'at_mm': 35}, {'kind': 'notch', 'at_mm': 80}]
    result = propose_assembly(doc)
    pair = next(p for p in result['proposals'] if {p['a'], p['b']} == {a['id'], b['id']})
    assert pair['orientation'] == 'reversed'
    assert len(pair['matches']) == 4


@pytest.mark.parametrize('size', ('P', 'M', 'G'))
@pytest.mark.parametrize('reverse,offset', ((False, 0), (True, 0), (False, 1), (True, 2), (False, 'mid'), (True, 'mid')))
def test_sew_arc_uses_physical_edge_with_reversed_and_shifted_origin(size, reverse, offset):
    panels = dxf_to_assembly(_dxf(size), size=size)['panels']
    measures = MEASURES_MM[size]
    expected = {'Front': {3: measures['body_height'] - 16, 6: measures['body_height'] - 16,
                          4: measures['body_width']/2 - 8, 5: measures['body_width']/2 - 8},
                'Side Left': {2: measures['body_height'] - 16, 4: measures['body_height'] - 16},
                'Shoulder Left': {1: measures['shoulder_width']/2 - 8,
                                  3: measures['shoulder_width']/2 - 8}}
    for panel in panels:
        if panel['name'] not in expected:
            continue
        vertices = panel['sewline'][:-1]
        if reverse:
            vertices = list(reversed(vertices))
        if offset == 'mid':
            midpoint = [(vertices[1][0] + vertices[2][0])/2,
                        (vertices[1][1] + vertices[2][1])/2]
            vertices = [midpoint] + vertices[2:] + vertices[:2] + [vertices[1]]
        else:
            vertices = vertices[offset:] + vertices[:offset]
        panel['sewline'] = vertices + vertices[:1]
        for index, length in expected[panel['name']].items():
            edge = panel['edges'][index - 1]
            lo, hi = sewline_interval_mm(panel, edge)
            assert hi - lo == pytest.approx(length)
            assert _sew_length(panel, edge) == pytest.approx(length)
            if panel['name'] == 'Front' and index == 3 and offset == 'mid' and not reverse:
                assert hi > LineString(panel['sewline']).length
