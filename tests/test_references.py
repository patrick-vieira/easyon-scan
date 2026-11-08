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
    result = measure(image, reference=name, reference_frontal=name == "bic")
    tolerance = 20 if perspective and name not in ("a4", "letter", "bic") else 2
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
