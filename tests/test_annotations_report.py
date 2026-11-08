import json
from io import StringIO
import cv2 as cv
import ezdxf
import numpy as np
import pytest
from easyon_scan.scan import measure
from easyon_scan.synthetic import Parameters, generate_case, generate_pieces, draw_annotations
from easyon_scan.dxf import dxf_bytes
from scripts.real_photo_report import report


@pytest.mark.parametrize('tilt,layout', [(0,'fixed'), (20,'free'), (-20,'fixed')])
def test_annotation_export(tilt, layout):
    image, _ = generate_case(parameters=Parameters(annotations=True, tilt_deg=tilt))
    result = measure(image, layout=layout)
    piece = result.pieces[0]
    assert len(piece.notches_mm) == 2
    assert len(piece.grain_lines_mm) == 1
    assert not piece.holes_mm
    assert abs(result.area_mm2-240000) < 600
    assert abs(result.width_mm-400) < 1
    doc = ezdxf.read(StringIO(dxf_bytes(result).decode()))
    assert len(doc.modelspace().query('*[layer=="NOTCH"]')) == 2
    assert len(doc.modelspace().query('*[layer=="GRAIN"]')) == 1
    for entity in doc.modelspace():
        xy = np.array(entity.get_points())[:, :2]
        assert xy.min() >= -1e-5
        assert xy[:,0].max() <= result.width_mm+1
        assert xy[:,1].max() <= result.height_mm+1


def test_multiple_pieces_keep_holes():
    image, _ = generate_pieces()
    for args in [(190,190,170,600),(390,240,200,450)]:
        draw_annotations(image, *args)
    result = measure(image, layout='fixed')
    assert [(len(p.notches_mm),len(p.grain_lines_mm),len(p.holes_mm)) for p in result.pieces] == [(2,1,1),(2,1,1)]


def test_physical_report(tmp_path):
    (tmp_path/'ok.json').write_text(json.dumps(dict(width_mm=102, height_mm=198)))
    (tmp_path/'bad.json').write_text(json.dumps(dict(error='quality rejected')))
    csv = tmp_path/'physical.csv'
    csv.write_text('capture_id,scanner_json,metric,physical_value\na,ok.json,width_mm,100\na,ok.json,height_mm,200\nb,bad.json,width_mm,100\nc,missing.json,width_mm,100\n')
    result = report(csv, tmp_path)
    assert result['measurements'][0]['signed_error'] == 2
    assert result['measurements'][1]['signed_error'] == -2
    assert [r['status'] for r in result['measurements']] == ['ok','ok','rejected','unavailable']
    assert result['summary']['width_mm']['accepted'] == 1
    assert result['summary']['width_mm']['mean_absolute_error'] == 2
    csv.write_text('capture_id,scanner_json,metric,physical_value\na,ok.json,width_mm,nan\n')
    with pytest.raises(ValueError, match='finite'):
        report(csv, tmp_path)


def test_grain_rotation_and_plain_hole():
    from easyon_scan.annotations import grain, notches
    image, _ = generate_case(parameters=Parameters(annotations=True))
    ring = measure(image).pieces[0].grain_lines_mm[0]
    angle = .72
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    assert grain(ring @ rotation.T)
    rectangle = np.array([[0,0],[10,0],[10,100],[0,100]], float)
    assert not grain(rectangle)
    outline, marks = notches(rectangle)
    assert not marks
    np.testing.assert_array_equal(outline, rectangle)
