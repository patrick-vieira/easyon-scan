import base64
from io import BytesIO, StringIO
import re
import subprocess
import sys

import cv2 as cv
import ezdxf
import numpy as np
import pytest
from easyon_scan.scan import measure, ScanError
from easyon_scan.synthetic import generate
from easyon_scan.dxf import export_dxf
from easyon_scan.markers import sheet
from easyon_scan.web import create_app


@pytest.fixture(scope="module")
def scene():
    return generate()


@pytest.mark.parametrize("perspective", [False, True])
def test_end_to_end(tmp_path, perspective):
    image, truth = generate(tmp_path / "photo.jpg", perspective=perspective)
    result = measure(tmp_path / "photo.jpg")
    assert abs(result.width_mm - truth["width_mm"]) < 1
    assert abs(result.height_mm - truth["height_mm"]) < 1
    assert abs(result.area_mm2 / truth["area_mm2"] - 1) < .005
    path = tmp_path / "pattern.dxf"
    export_dxf(result.contour_mm, path)
    doc = ezdxf.readfile(path)
    poly, = doc.modelspace().query("LWPOLYLINE")
    assert doc.units == 4 and poly.closed
    points = np.array(poly.get_points("xy"))
    np.testing.assert_allclose(np.ptp(points, axis=0), [result.width_mm, result.height_mm])
    assert len(points) == len(result.contour_mm)


def test_missing_markers():
    with pytest.raises(ScanError, match="four ArUco"):
        measure(np.full((600, 600, 3), 55, np.uint8))


def test_missing_paper(scene):
    image, _ = scene
    # Use a clean layout with no perspective; remove central paper.
    image, _ = generate(perspective=False)
    image[600:2500, 600:1900] = 55
    with pytest.raises(ScanError):
        measure(image)


@pytest.mark.parametrize("size", ["a4", "a3"])
def test_sheet(tmp_path, size):
    path = tmp_path / f"{size}.pdf"
    sheet(path, size)
    assert path.read_bytes().startswith(b"%PDF")
    text = path.read_bytes().decode("latin1")
    box = re.search(r"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", text)
    assert box
    expected = (210,297) if size == "a4" else (297,420)
    np.testing.assert_allclose(np.array(box.groups(),float)*25.4/72, expected, atol=.01)


def test_web(scene):
    app = create_app()
    app.testing = True
    client = app.test_client()
    assert client.get("/").status_code == 200
    encoded = cv.imencode(".jpg", scene[0])[1].tobytes()
    response = client.post("/", data={"photo": (BytesIO(encoded), "phone.jpg")})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    match = re.search(rb'data:application/dxf;base64,([^"]+)', response.data)
    doc = ezdxf.read(StringIO(base64.b64decode(match[1]).decode()))
    assert doc.units == 4
    assert doc.modelspace().query("LWPOLYLINE")[0].closed
    assert client.post("/", data={"photo": (BytesIO(b"invalid"), "x.jpg")}).status_code == 400
    assert b"Download DXF" not in client.get("/").data


def test_cli(tmp_path):
    generate(tmp_path / "photo.png")
    run = subprocess.run([sys.executable, "-c", "from easyon_scan.cli import main; main()", "measure",
        str(tmp_path / "photo.png"), "--out", str(tmp_path / "out.dxf"),
        "--preview", str(tmp_path / "preview.png")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "out.dxf").exists()
    assert cv.imread(str(tmp_path / "preview.png")) is not None
