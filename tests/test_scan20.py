"""Printer stretch affects kit ruling, independently of ruler-drawn outlines."""
from io import BytesIO
import json
import sys
import cv2 as cv
import numpy as np
import pytest
from easyon_scan.sketch import sketch_kit_image, measure_sketch
from easyon_scan.web import create_app


def scene(sx,sy,hand=False):
    image=cv.cvtColor(np.array(sketch_kit_image()),cv.COLOR_RGB2BGR)
    image=cv.resize(image,None,fx=.4*sx,fy=.4*sy)
    # A physical ruler draws this 50 x 75 mm shape AFTER printing.
    cv.rectangle(image,(140,180),(340,480),(30,30,30),2)
    if hand:
        # Independent 80 mm and 90 mm lengths, not the printed 100 mm.
        cv.line(image,(round(80*sx),round(1060*sy)),(round(80*sx)+320,round(1060*sy)),(20,20,20),2)
        cv.line(image,(round(760*sx),round(160*sy)),(round(760*sx),round(160*sy)+360),(20,20,20),2)
    return image


@pytest.mark.parametrize('sx,sy',[(1.05,.95),(.95,1.05),(1.05,1.05),(.95,.95)])
@pytest.mark.parametrize('hand',[False,True])
def test_axis_print_stretch(sx,sy,hand):
    image=scene(sx,sy,hand)
    r=measure_sketch(image,calib_mm_x=80 if hand else 100*sx,calib_mm_y=90 if hand else 100*sy)
    assert len(r.pieces)==1
    assert r.width_mm==pytest.approx(50,abs=1)
    assert r.height_mm==pytest.approx(75,abs=1)
    assert r.calibration['x']['correction_factor']==pytest.approx(sx,abs=.015)
    assert r.calibration['y']['correction_factor']==pytest.approx(sy,abs=.015)
    assert r.calibration['x']['hand_drawn']==hand
    assert r.pieces[0].internal_lines_mm==[]


def test_web_cli_calibration(tmp_path,monkeypatch,capsys):
    from easyon_scan.cli import main
    image=scene(1.05,.95)
    path=tmp_path/'kit.png';cv.imwrite(str(path),image)
    monkeypatch.setattr(sys,'argv',['easyon-scan','measure',str(path),'--sketch','--calib-mm-x','105','--calib-mm-y','95','--out',str(tmp_path/'out.dxf')])
    main(); summary=json.loads(capsys.readouterr().out)
    assert summary['calibration']['x']['correction_factor']==pytest.approx(1.05,abs=.01)
    client=create_app().test_client()
    for lang,text in [('pt','a impressão saiu'),('en','print was')]:
        reply=client.post('/',data={'lang':lang,'workflow':'sketch','calib_mm_x':'105','calib_mm_y':'95','photo':(BytesIO(cv.imencode('.png',image)[1].tobytes()),'kit.png')})
        assert reply.status_code==200
        assert text in reply.get_data(as_text=True)


@pytest.mark.parametrize('value',['0','-1','nan','inf','10001'])
def test_invalid_calibration(value):
    with pytest.raises(ValueError):measure_sketch(scene(1,1),calib_mm_x=value)


def test_missing_calibration():
    image=scene(1,1)
    gray=cv.cvtColor(image,cv.COLOR_BGR2GRAY)
    with pytest.raises(ValueError,match='Calibration lines missing'):
        measure_sketch(cv.cvtColor(gray,cv.COLOR_GRAY2BGR),calib_mm_x=100)


def test_projective_kit():
    image=scene(1.05,.95)
    h,w=image.shape[:2]
    H=cv.getPerspectiveTransform(np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]]),np.float32([[40,20],[w-50,40],[w-10,h-40],[15,h-10]]))
    image=cv.warpPerspective(image,H,(w,h),borderValue=(30,30,30))
    result=measure_sketch(image,calib_mm_x=105,calib_mm_y=95)
    assert result.width_mm==pytest.approx(50,abs=1)
    assert result.height_mm==pytest.approx(75,abs=1)


def test_optional_axis_and_nominal_kit():
    image=scene(1.05,.95)
    nominal=measure_sketch(image)
    result=measure_sketch(image,calib_mm_x=105)
    assert result.width_mm==pytest.approx(50,abs=1)
    assert result.height_mm==pytest.approx(nominal.height_mm,abs=.01)
    assert list(result.calibration)==['x']
