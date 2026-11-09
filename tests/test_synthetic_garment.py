"""Synthetic fixture contract; no physical accuracy claim."""
import hashlib
import json

import pytest
import ezdxf

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
