from io import BytesIO
import cv2 as cv
import numpy as np
import pytest
from easyon_scan.references import CATALOGUE
from easyon_scan.synthetic import generate_reference
from easyon_scan.scan import measure, ScanError
from easyon_scan.web import create_app


@pytest.mark.parametrize("name", CATALOGUE)
@pytest.mark.parametrize("perspective", [False, True])
def test_reference(name, perspective):
    image, truth = generate_reference(name, perspective=perspective)
    result = measure(image, reference=name, reference_frontal=name in ("bic", "bic-mini"))
    tolerance = 20 if perspective and name not in ("a4", "letter", "bic", "bic-mini") else 2
    assert result.width_mm == pytest.approx(truth["width_mm"], abs=tolerance)
    assert result.height_mm == pytest.approx(truth["height_mm"], abs=tolerance)
    assert len(result.pieces) == 1
    summary = result.summary()
    assert summary["reference"] == name
    assert summary["estimated_accuracy_class"]
    assert summary["warnings"]
    assert summary["residual_mm"] is None


def test_rejections():
    image, _ = generate_reference("bic")
    with pytest.raises(ScanError, match="frontal"):
        measure(image, reference="bic")
    with pytest.raises(ValueError, match="Unknown"):
        measure(image, reference="unknown")
    with pytest.raises(ValueError, match="free"):
        measure(image, reference="a4", layout="fixed")
    with pytest.raises(ScanError):
        measure(np.full_like(image, 40), reference="a4")


def test_backend():
    image, _ = generate_reference()
    _, encoded = cv.imencode(".png", image)
    response = create_app().test_client().post("/", data={
        "photo": (BytesIO(encoded.tobytes()), "photo.png"),
        "reference": "a4", "response": "json"})
    assert response.status_code == 200
    assert response.json["reference"] == "a4"
    assert response.json["piece_count"] == 1


def test_cli(tmp_path, monkeypatch, capsys):
    import json
    from easyon_scan.cli import main
    image, _ = generate_reference('letter')
    photo = tmp_path / 'photo.png'
    output = tmp_path / 'pattern.dxf'
    cv.imwrite(str(photo), image)
    monkeypatch.setattr('sys.argv', ['easyon-scan', 'measure', str(photo),
                                   '--reference', 'letter', '--out', str(output)])
    main()
    summary = json.loads(capsys.readouterr().out)
    assert summary['reference'] == 'letter'
    assert summary['residual_mm'] is None
    assert output.exists()


def test_reference_clipped_and_touching():
    image, _ = generate_reference('a4', perspective=False)
    with pytest.raises(ScanError, match='visible'):
        measure(image[:, 80:], reference='a4')
    image[100:200, 600:1100] = 240
    with pytest.raises(ScanError):
        measure(image, reference='a4')


@pytest.mark.parametrize('corner', [(50,50),(850,50),(50,650),(850,650)])
@pytest.mark.parametrize('horizontal', [False,True])
def test_coloured_reference_any_corner(corner, horizontal):
    image = np.full((1000,1200,3),40,np.uint8)
    x,y = corner
    w,h = (246,75) if horizontal else (75,246)
    cv.rectangle(image,(x,y),(x+w,y+h),(210,60,20),-1)
    cv.rectangle(image,(400,350),(760,590),(30,130,220),-1)
    result = measure(image,reference='bic',reference_frontal=True)
    assert len(result.pieces)==1
    assert sorted([result.width_mm,result.height_mm]) == pytest.approx([80,120],abs=2)


def test_specific_reference_errors():
    from easyon_scan.references import detect
    image = np.full((600,900,3),40,np.uint8)
    cv.rectangle(image,(0,50),(75,296),(210,60,20),-1)
    cv.rectangle(image,(350,200),(750,450),(30,130,220),-1)
    with pytest.raises(ScanError) as caught:
        detect(image,'bic',True)
    assert caught.value.code=='reference_border'
    assert caught.value.overlay.shape==image.shape
    image[:]=40
    with pytest.raises(ScanError) as caught:
        detect(image,'bic',True)
    assert caught.value.code=='reference_missing'
    cv.rectangle(image,(40,50),(115,296),(210,60,20),-1)
    with pytest.raises(ScanError) as caught:
        detect(image,'bic',True)
    assert caught.value.code=='pattern_missing'
    cv.rectangle(image,(200,50),(275,296),(210,60,20),-1)
    with pytest.raises(ScanError) as caught:
        detect(image,'bic',True)
    assert caught.value.code=='reference_ambiguous'


@pytest.mark.parametrize('lang,reason',[('pt','Nenhum objeto'),('en','No object')])
def test_web_specific_error_overlay(lang,reason):
    image=np.full((600,900,3),40,np.uint8)
    _,encoded=cv.imencode('.png',image)
    response=create_app().test_client().post('/',data={'photo':(BytesIO(encoded.tobytes()),'photo.png'),'reference':'card','lang':lang})
    html=response.get_data(as_text=True)
    assert response.status_code==400
    assert reason in html
    assert 'data:image/png;base64,' in html


@pytest.mark.parametrize('name',['card','phone:iphone-15','a4'])
@pytest.mark.parametrize('rotation',[0,1,2,3])
def test_coloured_rounded_and_sheet_references(name,rotation):
    image,_=generate_reference(name,perspective=False)
    light=image[:,:,0]>200
    image[light & (np.indices(light.shape)[1]<900)]=(200,60,30)
    image[light & (np.indices(light.shape)[1]>=900)]=(40,130,220)
    image=np.ascontiguousarray(np.rot90(image,rotation))
    result=measure(image,reference=name)
    assert result.width_mm==pytest.approx(180,abs=2)
    assert result.height_mm==pytest.approx(260,abs=2)


def test_rotated_reference_and_pattern_border():
    from easyon_scan.references import detect
    image=np.full((800,1100,3),40,np.uint8)
    box=cv.boxPoints(((220,220),(75,246),32)).astype(np.int32)
    cv.fillConvexPoly(image,box,(200,60,30))
    cv.rectangle(image,(600,350),(1000,600),(40,130,220),-1)
    result=measure(image,reference='bic',reference_frontal=True)
    assert len(result.pieces)==1
    image[:,1000:]=(40,130,220)
    # Join the pattern to the right border.
    image[350:601,600:]=(40,130,220)
    with pytest.raises(ScanError) as caught:
        detect(image,'bic',True)
    assert caught.value.code=='pattern_border'


def test_cli_failure_overlay(tmp_path,monkeypatch,capsys):
    from easyon_scan.cli import main
    image=np.full((600,900,3),40,np.uint8)
    cv.rectangle(image,(0,50),(75,296),(200,60,30),-1)
    cv.rectangle(image,(350,200),(750,450),(40,130,220),-1)
    photo=tmp_path/'input.png'; overlay=tmp_path/'diagnostic.png'
    cv.imwrite(str(photo),image)
    monkeypatch.setattr('sys.argv',['easyon-scan','measure',str(photo),'--reference','bic','--reference-frontal','--out',str(tmp_path/'out.dxf'),'--debug-overlay',str(overlay)])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code==2
    assert 'reference_border' in capsys.readouterr().out
    assert cv.imread(str(overlay)).shape==image.shape
