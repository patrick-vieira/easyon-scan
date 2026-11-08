import json
import sys

import cv2 as cv
import numpy as np
import pytest

from easyon_scan.synthetic import Parameters, generate_batch, generate_case


def test_clean_truth():
    from easyon_scan.scan import measure
    image, metadata = generate_case(seed=7)
    truth = metadata['ground_truth']
    assert truth['contour_mm'] == [[190., 190.], [590., 190.], [590., 790.], [190., 790.]]
    result = measure(image, layout='fixed')
    assert abs(result.width_mm-truth['width_mm']) < 1
    assert abs(result.height_mm-truth['height_mm']) < 1


@pytest.mark.parametrize('name,value', [
    ('tilt_deg', 20), ('radial_k1', .08), ('shadow', .3), ('curl_mm', 3),
    ('lighting', .3), ('printer_scale', .98), ('noise_sigma', 4), ('jpeg_quality', 55)])
def test_defects_change_pixels_and_preserve_truth(name, value):
    clean, _ = generate_case(seed=4)
    altered, metadata = generate_case(seed=4, parameters=Parameters(**{name: value}))
    assert not np.array_equal(clean, altered)
    assert metadata['ground_truth']['width_mm'] == 400
    assert metadata['ground_truth']['height_mm'] == 600
    assert metadata['parameters'][name] == value


def test_seed_and_batch(tmp_path):
    p = Parameters(curl_mm=2, noise_sigma=2, jpeg_quality=70)
    first, metadata = generate_case(seed=8, parameters=p)
    again, same = generate_case(seed=8, parameters=p)
    assert metadata == same
    np.testing.assert_array_equal(first, again)
    other, _ = generate_case(seed=9, parameters=p)
    assert not np.array_equal(first, other)
    manifest = generate_batch(tmp_path, count=1, seed=8, parameters=p)
    assert json.loads((tmp_path/'manifest.json').read_text()) == manifest
    np.testing.assert_array_equal(cv.imread(str(tmp_path/'case-0000.png')), first)
    assert manifest['cases'][0]['seed'] == 8
    with pytest.raises(ValueError, match='already contains'):
        generate_batch(tmp_path, count=1)


@pytest.mark.parametrize('kwargs', [{'tilt_deg': 90}, {'radial_k1': float('nan')},
    {'shadow': -1}, {'curl_mm': -1}, {'lighting': 2}, {'printer_scale': 0},
    {'noise_sigma': -1}, {'jpeg_quality': 0}, {'jpeg_quality': 50.5}])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        Parameters(**kwargs)


def test_batch_cli(tmp_path, monkeypatch):
    from easyon_scan.cli import main
    monkeypatch.setattr(sys, 'argv', ['easyon-scan', 'synthetic', '--out', str(tmp_path),
        '--count', '2', '--seed', '42', '--printer-scale', '.98', '--jpeg-quality', '60'])
    main()
    cases = json.loads((tmp_path/'manifest.json').read_text())['cases']
    assert [case['seed'] for case in cases] == [42, 43]
    assert cases[0]['ground_truth']['marker_size_mm'] == 88.2
    assert all((tmp_path/case['file']).exists() for case in cases)


@pytest.mark.slow
def test_benchmark(tmp_path):
    from scripts.benchmark import benchmark, write_report
    rows = benchmark(count=1, profiles={'clean': Parameters(),
                                      'printer scale': Parameters(printer_scale=.98)})
    assert rows[0]['accepted'] == 1
    assert rows[0]['worst_mm'] < 1
    assert rows[1]['accepted'] == 1
    assert rows[1]['mean_mm'] > 5
    write_report(rows, tmp_path/'benchmark.md', 2026)
    report = (tmp_path/'benchmark.md').read_text()
    assert 'Rejection rate' in report and 'p95' in report


def test_all_rejected_report(tmp_path, monkeypatch):
    from scripts import benchmark as module
    from easyon_scan.scan import ScanError
    monkeypatch.setattr(module, 'generate_case', lambda **kw: (None, {}))
    def reject(*args, **kwargs):
        raise ScanError('fixture rejected')
    monkeypatch.setattr(module, 'measure', reject)
    rows = module.benchmark(count=2, profiles={'bad': Parameters()})
    assert rows[0]['accepted'] == 0 and rows[0]['mean_mm'] is None
    module.write_report(rows, tmp_path/'report.md', 0)
    assert 'N/A | N/A | N/A | 100.0%' in (tmp_path/'report.md').read_text()


def test_tilt_keeps_references_visible():
    from easyon_scan.scan import measure
    image, metadata = generate_case(parameters=Parameters(tilt_deg=25))
    result = measure(image, layout=metadata['layout'])
    assert abs(result.width_mm-400) < 1
    assert abs(result.height_mm-600) < 1


def test_invalid_count(tmp_path):
    with pytest.raises(ValueError, match='positive'):
        generate_batch(tmp_path, count=0)
