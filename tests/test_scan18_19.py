from io import BytesIO
import json
import sys
import cv2 as cv
import numpy as np
import pytest
from easyon_scan.web import create_app
from easyon_scan.sketch import measure_sketch, tiled_pdf, sketch_kit, length_mm


def grid_scene():
    from test_multi import scene
    image=scene()
    for x in range(815,1050,25): cv.line(image,(x,180),(x,530),(130,130,130),1)
    for y in range(195,530,25): cv.line(image,(800,y),(1050,y),(130,130,130),1)
    return image


def test_web_default_matches_cli_grid(tmp_path,monkeypatch,capsys):
    from easyon_scan.cli import main
    image=grid_scene(); path=tmp_path/'scene.png';cv.imwrite(str(path),image)
    monkeypatch.setattr(sys,'argv',['easyon-scan','measure',str(path),'--all-objects','--reference','bic','--out',str(tmp_path/'cli.dxf'),'--preview',str(tmp_path/'overlay.png')])
    main(); captured=capsys.readouterr(); cli=json.loads(captured.out)
    data=cv.imencode('.png',image)[1].tobytes()
    client=create_app().test_client()
    reply=client.post('/',data={'reference':'bic','response':'json','photo':(BytesIO(data),'scene.png')})
    assert reply.status_code==200
    assert reply.json==cli
    html=client.post('/',data={'reference':'bic','photo':(BytesIO(data),'scene.png')}).get_data(as_text=True)
    import re,base64
    encoded=re.search(r'data:application/dxf;base64,([^"]+)',html)[1]
    overlay=re.search(r'data:image/png;base64,([^"]+)',html)[1]
    assert np.array_equal(cv.imdecode(np.frombuffer(base64.b64decode(overlay),np.uint8),cv.IMREAD_COLOR),cv.imread(str(tmp_path/'overlay.png')))
    table=re.search(r'data:text/markdown;base64,([^"]+)',html)[1]
    assert base64.b64decode(table).decode().strip()==captured.err.strip()
    import ezdxf
    # DXF metadata includes timestamps/handles; compare model-space geometry.
    a=ezdxf.read(__import__('io').StringIO(base64.b64decode(encoded).decode()))
    b=ezdxf.readfile(tmp_path/'cli.dxf')
    assert [(e.dxf.layer,list(e.get_points())) for e in a.modelspace()]==[(e.dxf.layer,list(e.get_points())) for e in b.modelspace()]
    assert 'objects.json' in html and 'data:image/png;base64,' in html


def sketch(grid=True):
    im=np.full((700,700,3),245,np.uint8)
    if grid:
        for x in range(25,700,25):cv.line(im,(x,0),(x,699),(160,170,180),1)
        for y in range(25,700,25):cv.line(im,(0,y),(699,y),(160,170,180),1)
    cv.rectangle(im,(150,150),(450,500),(30,30,30),3)
    cv.line(im,(220,300),(380,340),(25,25,25),3)
    if not grid:cv.line(im,(100,600),(600,600),(20,20,20),3)
    return im


@pytest.mark.parametrize('grid',[True,False])
def test_sketch_scale_contour_marking_and_web(grid,tmp_path):
    im=sketch(grid)
    options={} if grid else dict(bar_points=[[100,600],[600,600]],bar_length='10 cm')
    r=measure_sketch(im,**options)
    assert len(r.pieces)==1
    assert r.width_mm==pytest.approx(60,abs=1)
    assert r.height_mm==pytest.approx(70,abs=1)
    assert r.pieces[0].internal_lines_mm
    assert tiled_pdf(r).startswith(b'%PDF')
    data={'workflow':'sketch','photo':(BytesIO(cv.imencode('.png',im)[1].tobytes()),'sketch.png')}
    if not grid:data.update(bar_points=json.dumps(options['bar_points']),bar_length='10 cm')
    reply=create_app().test_client().post('/',data=data)
    assert reply.status_code==200
    assert 'pattern-a4.pdf' in reply.get_data(as_text=True)
    kit=tmp_path/'kit.pdf';sketch_kit(kit);assert kit.read_bytes().startswith(b'%PDF')


def test_enlarged_sketch_and_invalid_scale():
    a=measure_sketch(sketch(),grid_pitch_mm=20)
    assert a.width_mm==pytest.approx(240,abs=3)
    assert length_mm('2 cm')==20
    with pytest.raises(ValueError):length_mm('nan')
    with pytest.raises(ValueError):measure_sketch(sketch(False),bar_points=[[0,0],[0,0]],bar_length='10 cm')


def test_sketch_pdf_tiling_dimensions_and_dxf_layers():
    import re
    from io import StringIO
    import ezdxf
    from easyon_scan.dxf import dxf_bytes
    r=measure_sketch(sketch(),grid_pitch_mm=20)
    payload=tiled_pdf(r).decode('latin1')
    boxes=re.findall(r'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)',payload)
    assert len(boxes)==4
    assert tuple(map(float,boxes[0]))==pytest.approx((210/25.4*72,297/25.4*72),abs=.01)
    doc=ezdxf.read(StringIO(dxf_bytes(r).decode()))
    assert doc.units==4
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="OUTLINE"]'))==1
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="INTERNAL"]'))>=1


def test_multiple_curved_sketches():
    im=sketch(False)
    cv.ellipse(im,(550,280),(55,90),0,0,360,(30,30,30),3)
    result=measure_sketch(im,bar_points=[[100,600],[600,600]],bar_length='10 cm')
    assert len(result.pieces)==2
    assert any(len(p.contour_mm)>10 for p in result.pieces)


def test_pencil_on_projective_grid():
    im=sketch()
    im[np.all(im<60,axis=2)]=(110,110,110)
    H=cv.getPerspectiveTransform(np.float32([[0,0],[699,0],[699,699],[0,699]]),np.float32([[40,10],[650,50],[690,650],[15,690]]))
    im=cv.warpPerspective(im,H,(700,700),borderValue=(25,25,25))
    r=measure_sketch(im)
    assert len(r.pieces)==1
    assert r.width_mm==pytest.approx(60,abs=2)
    assert r.height_mm==pytest.approx(70,abs=2)


def test_automatic_hand_drawn_bar():
    im=sketch(False)
    for x in (100,600):cv.line(im,(x,590),(x,610),(20,20,20),3)
    r=measure_sketch(im,bar_length='10 cm')
    assert r.width_mm==pytest.approx(60,abs=1)
    assert len(r.pieces)==1
