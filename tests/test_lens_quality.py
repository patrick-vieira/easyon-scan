import json
import cv2 as cv
import numpy as np
import pytest
from easyon_scan.lens import board, board_pdf, calibrate, undistort, validate_profile
from easyon_scan.scan import measure, ScanError
from easyon_scan.synthetic import generate_case, Parameters


def distort(image, k, d):
    h, w = image.shape[:2]
    y, x = np.indices((h, w), dtype=np.float32)
    points = np.stack([x, y], -1).reshape(-1, 1, 2)
    source = cv.undistortPoints(points, k, d, P=k).reshape(h, w, 2)
    return cv.remap(image, source[:, :, 0], source[:, :, 1], cv.INTER_LINEAR, borderValue=(55,)*3)


def test_known_distortion_profile():
    image, _ = generate_case()
    h, w = image.shape[:2]
    k = np.array([[1200., 0, w/2], [0, 1200., h/2], [0, 0, 1]])
    d = np.array([-.12, .015, 0, 0, 0])
    photo = distort(image, k, d)
    profile = dict(schema_version=1, image_size=[w, h], camera_matrix=k.tolist(), distortion=d.tolist())
    corrected = measure(photo, profile=profile)
    error = max(abs(corrected.width_mm-400), abs(corrected.height_mm-600))
    # Compare the same raw pipeline with only quality rejection disabled.
    from unittest.mock import patch
    with patch('easyon_scan.quality.checks', return_value=[]):
        raw = measure(photo)
    raw_error = max(abs(raw.width_mm-400), abs(raw.height_mm-600))
    assert error < 1 and error < raw_error/10
    with pytest.raises(ScanError, match='marker_consistency'):
        measure(photo)


def test_calibration_from_rendered_views(tmp_path):
    k = np.array([[2200., 0, 1260], [0, 2240., 1560], [0, 0, 1]])
    d = np.array([-.1, .02, 0, 0, 0])
    texture = board().generateImage((720, 960))
    physical = np.array([[0, 0, 0], [.18, 0, 0], [.18, .24, 0], [0, .24, 0]], float)
    paths = []
    for i in range(10):
        r = np.array([-.35+i*.07, .2*np.sin(i), .08*np.cos(i)])
        t = np.array([-.12+.055*(i%3), -.15+.045*(i%4), .43+.015*i])
        corners = cv.projectPoints(physical, r, t, k, None)[0].reshape(4, 2)
        matrix = cv.getPerspectiveTransform(np.array([[0, 0], [719, 0], [719, 959], [0, 959]], 'float32'), corners.astype('float32'))
        image = cv.warpPerspective(texture, matrix, (2520, 3120), borderValue=210)
        image = distort(cv.cvtColor(image, cv.COLOR_GRAY2BGR), k, d)
        path = tmp_path/f'{i}.png'
        cv.imwrite(str(path), image)
        paths.append(path)
    profile = calibrate(paths, 'synthetic phone')
    assert profile['rms_px'] < 1
    assert abs(profile['camera_matrix'][0][0]-2200) < 110
    assert abs(profile['distortion'][0]+.1) < .04
    scene, _ = generate_case()
    photo = distort(scene, k, d)
    corrected = measure(photo, profile=profile, layout="fixed")
    from unittest.mock import patch
    with patch('easyon_scan.quality.checks', return_value=[]):
        raw = measure(photo, layout='fixed')
    corrected_error = max(abs(corrected.width_mm-400), abs(corrected.height_mm-600))
    raw_error = max(abs(raw.width_mm-400), abs(raw.height_mm-600))
    assert corrected_error < raw_error/5
    board_pdf(tmp_path/'board.pdf')
    assert (tmp_path/'board.pdf').read_bytes().startswith(b'%PDF')
    with pytest.raises(ValueError, match='six'):
        calibrate(paths[:2], 'phone')


def test_quality_and_profile_validation():
    image, _ = generate_case()
    result = measure(image)
    assert {'focus', 'exposure', 'shadows', 'plane_tilt_proxy', 'marker_size_px'} <= {c['name'] for c in result.quality_checks}
    assert any(c['status'] == 'warn' and c['name'] == 'scale_verification' for c in result.quality_checks)
    with pytest.raises(ValueError, match='profile'):
        validate_profile({})
    with pytest.raises(ScanError):
        measure(cv.GaussianBlur(image, (31, 31), 10))
    distorted, _ = generate_case(parameters=Parameters(radial_k1=.08))
    with pytest.raises(ScanError, match='distortion'):
        measure(distorted)


def test_quality_thresholds():
    from easyon_scan.quality import checks
    groups = np.array([[[10, 10], [100, 10], [100, 100], [10, 100]],
                       [[150, 10], [240, 10], [240, 100], [150, 100]],
                       [[10, 150], [100, 150], [100, 240], [10, 240]],
                       [[150, 150], [240, 150], [240, 240], [150, 240]]], float)
    gray = np.full((260, 260), 128, np.uint8)
    rows = {r['name']: r for r in checks(gray, groups.reshape(-1, 2), np.eye(3), 3, False)}
    assert rows['focus']['status'] == 'reject'
    assert rows['exposure']['status'] == 'reject'
    assert rows['marker_consistency_mm']['status'] == 'reject'
    small = groups*.2
    rows = checks(gray, small.reshape(-1, 2), np.diag([4., 1., 1.]), 0, True)
    assert next(r for r in rows if r['name'] == 'marker_size_px')['status'] == 'reject'
    assert next(r for r in rows if r['name'] == 'plane_tilt_proxy')['status'] == 'reject'


def test_rejected_cli_and_web(tmp_path, monkeypatch, capsys):
    import sys
    from io import BytesIO
    from easyon_scan.cli import main
    from easyon_scan.web import create_app
    image, _ = generate_case(parameters=Parameters(radial_k1=.08))
    path = tmp_path/'bad.png'
    cv.imwrite(str(path), image)
    out = tmp_path/'bad.dxf'
    monkeypatch.setattr(sys, 'argv', ['easyon-scan', 'measure', str(path), '--out', str(out)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    data = json.loads(capsys.readouterr().out)
    assert any(c['status'] == 'reject' for c in data['quality_checks'])
    assert not out.exists()
    response = create_app().test_client().post('/', data={'photo': (BytesIO(path.read_bytes()), 'bad.png')})
    assert response.status_code == 400
    assert b'marker_consistency_mm' in response.data and b'90 mm' in response.data
