import copy
import pytest

from easyon_scan.assembly_v1 import migrate_v0, validate_assembly_v1
from easyon_scan.assembly_eval import evaluate_proposals
from easyon_scan.assembly import dxf_to_assembly
import io
import ezdxf


def source():
    doc = ezdxf.new()
    doc.header['$INSUNITS'] = 4
    doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
    stream = io.StringIO()
    doc.write(stream)
    return dxf_to_assembly(stream.getvalue().encode())

def fixture():
    return migrate_v0(source())


def codes(doc):
    return {x['code'] for x in validate_assembly_v1(doc, length_tolerance_mm=1)}


def test_migrate_preserves_v0_and_requires_explicit_pending():
    v0 = source()
    original = copy.deepcopy(v0)
    v1 = migrate_v0(v0)
    assert v0 == original
    assert v1['schema'] == 'garment-assembly/v1'
    assert not v1['seams'] and not v1['proposals']
    assert not [x for x in codes(v1) if x != 'grain_missing']
    with pytest.raises(ValueError):
        migrate_v0(v1)


def test_review_requires_whole_panel():
    doc = fixture()
    doc['review']['status'] = 'human_approved'
    assert 'panel_approval' in codes(doc)


def test_segment_conflict_and_matches():
    doc = fixture()
    edges = [e for p in doc['panels'] for e in p['edges']]
    a, b = edges[:2]
    ref = lambda e: {'edge_id': e['id'], 'start_mm': 0., 'end_mm': e['length_mm']}
    pair = dict(id='s', proposal_id='p', a=ref(a), b=ref(b), orientation='same',
                matches=[{'a_mm': a['length_mm'], 'b_mm': 0.}, {'a_mm': 0., 'b_mm': b['length_mm']}],
                construction='plain', allowance_mm={'a': None, 'b': None}, ease_mm=0.)
    doc['seams'] = [pair, dict(pair, id='s2')]
    assert {'match_order', 'segment_reused', 'unconfirmed_seam', 'sewline_missing'} <= codes(doc)


def test_evaluation_denominators_and_tolerance():
    ref = lambda edge: {'edge_id': edge, 'start_mm': 0., 'end_mm': 100.}
    truth = [dict(a=ref('a'), b=ref('b'), orientation='same', construction='plain')]
    good = dict(truth[0], id='good', score=.8)
    wrong = dict(a=ref('a'), b=ref('c'), orientation='same', construction='plain', id='wrong', score=.9)
    report = evaluate_proposals([wrong, good], truth, endpoint_tolerance_mm=1, review_seconds=[10, 20])
    assert report['top1']['predicted'] == 1 and report['top1']['precision'] == 0
    assert report['top3']['truth'] == 1 and report['top3']['recall'] == 1
    assert report['review_seconds']['median'] == 15


def test_legacy_proposal_is_snapshot():
    from easyon_scan.propose import propose_assembly
    proposed = propose_assembly(source())
    migrated = migrate_v0(proposed)
    if migrated['proposals']:
        assert 'proposal_changed' not in codes(migrated)
        migrated['proposals'][0]['score'] += .01
        assert 'proposal_changed' in codes(migrated)
