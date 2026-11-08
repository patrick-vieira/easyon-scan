from io import BytesIO
import cv2 as cv
import numpy as np
from easyon_scan.web import create_app
from easyon_scan.synthetic import generate_free


def frame(image):
    scale = min(1, 1280 / max(image.shape[:2]))
    image = cv.resize(image, None, fx=scale, fy=scale)
    return BytesIO(cv.imencode('.jpg', image)[1].tobytes())


def test_capture_quality_and_page():
    client = create_app().test_client()
    page = client.get('/capture')
    assert page.status_code == 200
    assert b'getUserMedia' in page.data
    assert b'cdn' not in page.data.lower()
    assert b'id="shoot" type="button" disabled' in page.data
    image, _ = generate_free()
    response = client.post('/capture/check', data={'photo': (frame(image), 'frame.jpg')})
    assert response.status_code == 200
    assert response.json['ready']
    assert response.json['lights'] == dict(markers='pass', sharpness='pass', tilt='pass')
    assert response.headers['Cache-Control'] == 'no-store'
    blank = np.full((480, 640, 3), 255, np.uint8)
    response = client.post('/capture/check', data={'photo': (frame(blank), 'frame.jpg')})
    assert response.status_code == 400
    assert not response.json['ready']


def test_capture_bad_input():
    client = create_app().test_client()
    assert client.post('/capture/check').status_code == 400
    assert client.post('/capture/check', data={'photo': (BytesIO(b'bad'), 'bad.jpg')}).status_code == 400
    assert client.post('/capture/check', data=b'x'*500001).status_code == 413
    image = np.zeros((1281, 20, 3), np.uint8)
    response = client.post('/capture/check', data={'photo': (BytesIO(cv.imencode('.png', image)[1].tobytes()), 'big.png')})
    assert response.status_code == 400
    assert not response.json['ready']


def test_capture_rejecting_lights(monkeypatch):
    import easyon_scan.web as web
    rows = [dict(name='focus', status='reject', value=1, message='Hold steady'),
            dict(name='plane_tilt_proxy', status='reject', value=4, message='Move overhead')]
    monkeypatch.setattr(web, 'measure', lambda *a, **kw: rows)
    client = create_app().test_client()
    response = client.post('/capture/check', data={
        'photo': (frame(np.zeros((100, 100, 3), np.uint8)), 'frame.jpg')})
    assert response.status_code == 200
    assert not response.json['ready']
    assert response.json['lights'] == dict(markers='pass', sharpness='reject', tilt='reject')
