"""Synthetic fixture contract; no physical accuracy claim."""
import hashlib
import json

import pytest

from easyon_scan.synthetic_garment import MEASURES_MM, generate_fixture


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
        assert len(truth['pairs']) == 4
        assert all(pair['construction'] == 'plain' for pair in truth['pairs'])
        assert report['top3']['truth'] == 4


def test_reject_unknown_or_duplicate_sizes(tmp_path):
    for sizes in ((), ('X',), ('P', 'P')):
        with pytest.raises(ValueError):
            generate_fixture(tmp_path, sizes)
