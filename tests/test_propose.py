"""Synthetic proposals; scores are not physical accuracy claims."""
import copy
import io

import ezdxf
import pytest

from easyon_scan.assembly import dxf_to_assembly
from easyon_scan.propose import propose_assembly


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
