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
    assert report['top1']['predicted'] == 2 and report['top1']['geometry_tp'] == 1
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


@pytest.fixture
def confirmed(tmp_path):
    import json
    from easyon_scan.synthetic_garment import generate_fixture
    generate_fixture(tmp_path, sizes=('M',))
    return json.loads((tmp_path / 'M.assembly-v1.json').read_text())


@pytest.mark.parametrize('field,value', [('orientation', 'unknown'), ('construction', 'gather'),
                                        ('ease_mm', 5), ('matches', [])])
def test_seam_cannot_silently_change_accept(confirmed, field, value):
    confirmed['seams'][0][field] = value
    assert 'seam_changed' in codes(confirmed)


def test_explicit_edit_snapshot_is_traceable(confirmed):
    seam = confirmed['seams'][0]
    seam['allowance_mm']['a'] = 12
    assert 'seam_changed' in codes(confirmed)
    confirmed['review']['decisions'][0]['after'] = {
        k: copy.deepcopy(v) for k, v in seam.items() if k not in ('id', 'proposal_id')}
    assert not codes(confirmed)
    confirmed['review']['decisions'][0]['after'] = {'orientation': 'same'}
    assert 'decision_edit' in codes(confirmed)


def test_operation_and_panel_decisions_have_their_own_targets():
    doc = fixture()
    doc['operations'] = [dict(id='hem', type='hem', segments=[])]
    trace = dict(reviewer='test', at='2026-01-01', reason='Checked')
    doc['review']['decisions'] = [dict(action='confirm_operation', operation_id='hem', **trace),
                                 dict(action='close_panel', panel_id=doc['panels'][0]['id'], **trace)]
    assert 'decision_reference' not in codes(doc)
    doc['review']['decisions'][0]['operation_id'] = 'missing'
    assert {'decision_reference', 'unconfirmed_operation'} <= codes(doc)


@pytest.mark.parametrize('action', ['accept', 'reject', 'defer'])
def test_conflicting_decisions_are_rejected(confirmed, action):
    decision = copy.deepcopy(confirmed['review']['decisions'][0])
    decision['action'] = action
    confirmed['review']['decisions'].append(decision)
    assert 'decision_conflict' in codes(confirmed)


@pytest.mark.parametrize('field,value', [('grain', 'invalid'), ('sewline', [1, 2])])
def test_nullable_structures_are_checked(field, value):
    doc = fixture()
    doc['panels'][0][field] = value
    assert 'structure' in codes(doc)


def test_migration_keeps_actual_generated_proposals(tmp_path):
    from easyon_scan.synthetic_garment import _dxf
    from easyon_scan.propose import propose_assembly
    proposed = propose_assembly(dxf_to_assembly(_dxf('M')), max_per_edge=None)
    assert proposed['proposals']
    original = copy.deepcopy(proposed)
    migrated = migrate_v0(proposed)
    assert proposed == original
    assert len(migrated['proposals']) == len(proposed['proposals'])
    assert not [i for i in validate_assembly_v1(migrated, length_tolerance_mm=1) if i['level'] == 'error']
    migrated['proposals'][0]['content_sha256'] = 'invalid'
    assert 'structure' in codes(migrated)


def test_internal_matches_and_swapped_ab_evaluation():
    ref = lambda edge: dict(edge_id=edge, start_mm=0., end_mm=100.)
    truth = dict(a=ref('a'), b=ref('b'), orientation='reversed', construction='plain',
                 matches=[dict(a_mm=0., b_mm=100.), dict(a_mm=20., b_mm=70.), dict(a_mm=100., b_mm=0.)])
    candidate = dict(copy.deepcopy(truth), id='candidate', score=1.)
    def result():
        return evaluate_proposals([candidate], [truth], endpoint_tolerance_mm=0)['top1']
    assert result()['tp'] == 1
    candidate['a'], candidate['b'] = candidate['b'], candidate['a']
    candidate['matches'] = [dict(a_mm=m['b_mm'], b_mm=m['a_mm']) for m in candidate['matches']]
    assert result()['tp'] == 1
    candidate['matches'][1]['b_mm'] = 21.
    assert result()['tp'] == 0 and result()['matches_errors'] == 1
    candidate['orientation'] = 'same'
    assert result()['orientation_errors'] == 1


@pytest.mark.parametrize('field', ['a', 'b'])
def test_segment_edit_requires_explicit_snapshot(confirmed, field):
    confirmed['seams'][0][field]['start_mm'] += 1
    assert 'seam_changed' in codes(confirmed)


def test_swapping_segments_without_match_columns_is_wrong():
    a = dict(edge_id='a', start_mm=0., end_mm=100.)
    b = dict(edge_id='b', start_mm=0., end_mm=100.)
    truth = dict(a=a, b=b, orientation='reversed', construction='plain',
                 matches=[dict(a_mm=0., b_mm=100.), dict(a_mm=20., b_mm=70.), dict(a_mm=100., b_mm=0.)])
    candidate = dict(truth, a=b, b=a, id='wrong', score=1.)
    report = evaluate_proposals([candidate], [truth], endpoint_tolerance_mm=0)
    assert report['top1']['geometry_tp'] == 1
    assert report['top1']['matches_errors'] == 1
    assert report['top1']['tp'] == 0


def test_missing_panel_reference_reports_instead_of_crashing():
    doc = fixture()
    doc['review']['status'] = 'human_approved'
    doc['review']['decisions'] = [dict(action='close_panel', reviewer='x', at='now', reason='x')]
    assert {'decision_reference', 'panel_approval'} <= codes(doc)



def test_accept_requires_resulting_seam(confirmed):
    confirmed['seams'].pop()
    assert 'accepted_seam_missing' in codes(confirmed)


@pytest.mark.parametrize('action', ['reject', 'defer'])
def test_only_accept_can_edit(confirmed, action):
    decision = confirmed['review']['decisions'][0]
    decision['action'] = action
    decision['after'] = {}
    assert 'decision_edit' in codes(confirmed)
