from io import StringIO, BytesIO
import ezdxf
import numpy as np
import pytest
from easyon_scan.dxf import dxf_bytes, export_dxf
from easyon_scan.scan import Piece, Measurement


def fixture():
    outer = np.array([[10, 20], [110, 20], [110, 220], [10, 220]], float)
    hole = np.array([[30, 40], [40, 40], [40, 50], [30, 50]], float)
    p = Piece(outer, [hole], [np.array([[50, 20], [54, 26], [58, 20]])],
              [np.array([[60, 70], [60, 170]])], [np.array([[20, 80], [40, 90]])])
    return Measurement(outer, 0, 0, 0, 0, 0, None, pieces=[p, Piece(outer+[200, 0], [])])


def test_aama_roundtrip(tmp_path):
    path = tmp_path/'pieces.dxf'
    export_dxf(fixture(), path, mode='aama', size='38', piece_names=['Front', 'Back'])
    doc = ezdxf.readfile(path)
    assert not doc.audit().has_errors
    assert doc.dxfversion == 'AC1015'
    assert doc.units == 4
    refs = list(doc.modelspace().query('INSERT'))
    assert len(refs) == 2
    assert all(tuple(r.dxf.insert) == (0, 0, 0) for r in refs)
    front, back = [doc.blocks[r.dxf.name] for r in refs]
    rings = list(front.query('POLYLINE'))
    assert {e.dxf.layer for e in rings} == {'1', '8', '11'}
    assert all(e.is_closed for e in rings if e.dxf.layer != '8')
    assert not next(e for e in rings if e.dxf.layer == '8').is_closed
    assert {e.dxf.text for e in front.query('TEXT')} == {'Piece Name: Front', 'Size: 38'}
    assert next(iter(front.query('POINT'))).dxf.layer == '4'
    grain = next(iter(front.query('LINE')))
    assert grain.dxf.layer == '7'
    assert grain.dxf.start.distance(grain.dxf.end) == pytest.approx(100)
    xs = lambda block: min(v.dxf.location.x for e in block.query('POLYLINE') if e.dxf.layer == '1' for v in e.vertices)
    assert xs(back)-xs(front) == pytest.approx(200)


def test_default_and_invalid_modes():
    result = fixture()
    doc = ezdxf.read(StringIO(dxf_bytes(result).decode()))
    assert len(doc.modelspace().query('INSERT')) == 0
    assert len(doc.modelspace().query('LWPOLYLINE')) == 6
    with pytest.raises(ValueError, match='mode'):
        dxf_bytes(result, mode='unknown')
    with pytest.raises(ValueError, match='name'):
        dxf_bytes(result, mode='aama', piece_names=['Front'])


def test_web_aama_selection():
    import base64
    import re
    from unittest.mock import patch
    from easyon_scan.web import create_app
    result = fixture()
    result.preview = np.zeros((20, 20, 3), np.uint8)
    with patch('easyon_scan.web.measure', return_value=result), patch('easyon_scan.web.load_photo', return_value=np.zeros((20,20,3),np.uint8)):
        response = create_app().test_client().post('/', data={
            'photo': (BytesIO(b'unused'), 'photo.jpg'), 'dxf_mode': 'aama', 'size': '42'})
    assert response.status_code == 200
    payload = re.search(rb'data:application/dxf;base64,([^"]+)', response.data)[1]
    doc = ezdxf.read(StringIO(base64.b64decode(payload).decode()))
    assert len(doc.modelspace().query('INSERT')) == 2
    assert any(e.dxf.text == 'Size: 42' for e in doc.blocks['PIECE_001'].query('TEXT'))


def test_cli_aama(monkeypatch, tmp_path):
    import sys
    import easyon_scan.cli as cli
    monkeypatch.setattr(cli, 'measure', lambda *a, **kw: fixture())
    path = tmp_path/'cli.dxf'
    monkeypatch.setattr(sys, 'argv', ['easyon-scan', 'measure', 'unused.jpg',
        '--out', str(path), '--dxf-mode', 'aama', '--size', '40'])
    cli.main()
    doc = ezdxf.readfile(path)
    assert {e.dxf.text for e in doc.blocks['PIECE_001'].query('TEXT')} == {
        'Piece Name: PIECE_001', 'Size: 40'}


def test_aama_non_ascii_names(tmp_path):
    path = tmp_path/'unicode.dxf'
    export_dxf(fixture(), path, mode='aama', piece_names=['Frente — verão', '背面'])
    doc = ezdxf.readfile(path)
    text = next(iter(doc.blocks['PIECE_001'].query('TEXT'))).dxf.text
    assert text == 'Piece Name: Frente — verão'
    from ezdxf.lldxf.encoding import decode_dxf_unicode
    assert decode_dxf_unicode(next(iter(doc.blocks['PIECE_002'].query('TEXT'))).dxf.text) == 'Piece Name: 背面'
