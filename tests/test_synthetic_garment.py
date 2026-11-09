"""Synthetic fixture contract; no physical accuracy claim."""
import hashlib
import json

import pytest
import ezdxf

from easyon_scan.assembly_eval import evaluate_proposals, ranked_by_edge

from easyon_scan.synthetic_garment import MEASURES_MM, _truth, generate_fixture
from easyon_scan.assembly_v1 import validate_assembly_v1


def test_fixture_is_deterministic_and_importable(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    first, second = generate_fixture(a), generate_fixture(b)
    assert first == second
    assert set(first['sizes']) == set(MEASURES_MM)
    for name, digest in first['sha256'].items():
        assert hashlib.sha256((a / name).read_bytes()).hexdigest() == digest
        assert (a / name).read_bytes() == (b / name).read_bytes()
    for size in MEASURES_MM:
        v0 = json.loads((a / f'{size}.assembly-v0.json').read_text())
        v1 = json.loads((a / f'{size}.assembly-v1.json').read_text())
        truth = json.loads((a / f'{size}.truth.json').read_text())
        report = json.loads((a / f'{size}.evaluation.json').read_text())
        assert len(v0['panels']) == 6
        assert [p['name'] for p in v0['panels']] == ['Front', 'Back', 'Side Left', 'Side Right', 'Shoulder Left', 'Shoulder Right']
        assert all(p['sewline_status'] == 'from_dxf' for p in v0['panels'])
        assert v1['schema'] == 'garment-assembly/v1'
        assert v1['review']['status'] == 'pending'
        assert len(truth['pairs']) == 8
        assert len(v1['seams']) == 8
        assert not [i for i in validate_assembly_v1(v1, length_tolerance_mm=1) if i['level'] == 'error']
        assert len({r['edge_id'] for p in truth['pairs'] for r in (p['a'], p['b'])}) == 16
        by_id = {e['id']: p['name'] for p in v0['panels'] for e in p['edges']}
        assert {tuple(sorted((by_id[p['a']['edge_id']], by_id[p['b']['edge_id']]))) for p in truth['pairs']} == {
            tuple(sorted((body, bridge))) for body in ('Front', 'Back')
            for bridge in ('Side Left', 'Side Right', 'Shoulder Left', 'Shoulder Right')}
        assert all(pair['construction'] == 'plain' for pair in truth['pairs'])
        expected = [('panel-1:e6', 'panel-3:e2', 'reversed'),
                    ('panel-2:e6', 'panel-3:e4', 'same'),
                    ('panel-1:e3', 'panel-4:e4', 'reversed'),
                    ('panel-2:e3', 'panel-4:e2', 'same'),
                    ('panel-1:e5', 'panel-5:e3', 'same'),
                    ('panel-2:e5', 'panel-5:e1', 'reversed'),
                    ('panel-1:e4', 'panel-6:e3', 'same'),
                    ('panel-2:e4', 'panel-6:e1', 'reversed')]
        assert [(p['a']['edge_id'], p['b']['edge_id'], p['orientation'])
                for p in truth['pairs']] == expected
        edges = {e['id']: (panel, e) for panel in v1['panels'] for e in panel['edges']}
        for pair, seam in zip(truth['pairs'], v1['seams']):
            lengths = []
            for ref in (pair['a'], pair['b']):
                panel, edge = edges[ref['edge_id']]
                lo, hi = edge['sewline_interval_mm']
                assert 0 <= lo < hi
                lengths.append(hi - lo)
            assert seam['ease_mm'] == pytest.approx(lengths[0] - lengths[1])
            assert seam['ease_mm'] == pytest.approx(0)
        width, height = MEASURES_MM[size]['body_width'], MEASURES_MM[size]['body_height']
        side = MEASURES_MM[size]['side_width']
        assert edges['panel-1:e6'][1]['sewline_interval_mm'] == pytest.approx([2*width + height - 48, 2*width + 2*height - 64])
        assert edges['panel-3:e4'][1]['sewline_interval_mm'] == pytest.approx([2*side + height - 48, 2*side + 2*height - 64])
        assert lengths[-1] == pytest.approx(width/2 - 8)
        assert edges['panel-1:e3'][1]['sewline_interval_mm'][1] - edges['panel-1:e3'][1]['sewline_interval_mm'][0] < MEASURES_MM[size]['body_height']
        assert report['top3']['geometry_tp'] >= report['top1']['geometry_tp']
        assert report['top1']['tp'] == report['top3']['tp'] == 0
        assert report['top3']['geometry_tp'] > 0
        assert [row['rank'] for row in report['pair_diagnostics']] == [3, 4, 3, 5, 6, 5, 5, 7]
        assert any(row['failure'] == 'rank_below_3' for row in report['pair_diagnostics'])

        assert report['top3']['truth'] == 8
        assert len(report['pair_diagnostics']) == 8
        assert report['top1']['tp'] == 0  # The current proposer does not declare construction.
        assert all(row['failure'] in {'pair_missing', 'rank_below_3',
                   'orientation_unknown_or_wrong', 'construction_unresolved'} for row in report['pair_diagnostics'])
        dxf = ezdxf.readfile(a / f'{size}.dxf')
        assert dxf.units == 4
        for n, block in ((n, dxf.blocks[f'PIECE_{n}']) for n in range(1, 7)):
            assert {'1', '14', '7'} <= {entity.dxf.layer for entity in block}
            if n <= 2:
                assert '4' in {entity.dxf.layer for entity in block}
            assert len([entity for entity in block if entity.dxftype() == 'TEXT']) == 3


def test_truth_rejects_swapped_shoulder_edge(tmp_path):
    generate_fixture(tmp_path, ('M',))
    v0 = json.loads((tmp_path / 'M.assembly-v0.json').read_text())
    for panel in v0['panels']:
        if panel['name'] == 'Shoulder Left':
            panel['edges'][0]['length_mm'] += 10
    with pytest.raises(ValueError, match='Incompatible seam lengths'):
        _truth(v0)


def test_reject_unknown_or_duplicate_sizes(tmp_path):
    for sizes in ((), ('X',), ('P', 'P')):
        with pytest.raises(ValueError):
            generate_fixture(tmp_path, sizes)


def test_truth_rejects_actual_equal_length_edge_swap_and_bad_parallelism(tmp_path):
    generate_fixture(tmp_path, ('M',))
    original = json.loads((tmp_path / 'M.assembly-v0.json').read_text())
    import copy
    swapped = copy.deepcopy(original)
    front = next(p for p in swapped['panels'] if p['name'] == 'Front')
    front['edges'][3], front['edges'][4] = front['edges'][4], front['edges'][3]
    with pytest.raises(ValueError, match='Unexpected body edge position'):
        _truth(swapped)
    tilted = copy.deepcopy(original)
    side = next(p for p in tilted['panels'] if p['name'] == 'Side Left')
    side['cutline'][2][0] += 2
    with pytest.raises(ValueError, match='Nonparallel seam'):
        _truth(tilted)


def test_geometry_ranking_changes_and_construction_is_explicit():
    def ref(edge):
        return {'edge_id': edge, 'start_mm': 0., 'end_mm': 10.}
    truth = [dict(a=ref('a'), b=ref('b'), orientation='same', construction='plain')]
    decoys = [dict(id=f'd{i}', a=ref('a'), b=ref(f'x{i}'), score=1-i*.1,
                   orientation='same', construction=None) for i in range(3)]
    hit = dict(id='hit', a=ref('a'), b=ref('b'), score=.1,
               orientation='same', construction=None)
    b_decoy = dict(id='bd', a=ref('b'), b=ref('z'), score=1.5,
                   orientation='same', construction=None)
    low = evaluate_proposals(decoys + [b_decoy, hit], truth, endpoint_tolerance_mm=0)
    high = evaluate_proposals(decoys + [b_decoy, dict(hit, score=2)], truth, endpoint_tolerance_mm=0)
    assert low['top3']['geometry_tp'] == 1  # b participates even when a ranks fourth.
    assert high['top1']['geometry_tp'] == 1
    assert low['top1']['geometry_tp'] == 0
    assert high['top1']['construction_errors'] == 1
    assert high['top1']['tp'] == 0


def test_ranking_orders_multiple_intervals_on_same_edge():
    def ref(edge, start):
        return {'edge_id': edge, 'start_mm': start, 'end_mm': start + 10}
    proposals = [
        {'id': 'low', 'a': ref('a', 0), 'b': ref('b', 0), 'score': .1},
        {'id': 'high', 'a': ref('a', 10), 'b': ref('c', 0), 'score': .9},
        {'id': 'tie', 'a': ref('a', 20), 'b': ref('d', 0), 'score': .9},
    ]
    assert [p['id'] for p in ranked_by_edge(proposals)['a']] == ['high', 'tie', 'low']
