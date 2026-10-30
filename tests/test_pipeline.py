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
from easyon_scan.synthetic import generate, generate_free
from easyon_scan.dxf import export_dxf
from easyon_scan.markers import sheet
from easyon_scan.web import create_app


@pytest.fixture(scope="module")
def scene():
    return generate()


@pytest.mark.parametrize("perspective", [False, True])
def test_end_to_end(tmp_path, perspective):
    image, truth = generate(tmp_path / "photo.jpg", perspective=perspective)
    result = measure(tmp_path / "photo.jpg", layout="fixed")
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


@pytest.mark.parametrize("layout", ["free", "fixed"])
def test_cli(tmp_path, layout):
    generate(tmp_path / "photo.png")
    run = subprocess.run([sys.executable, "-c", "from easyon_scan.cli import main; main()", "measure",
        str(tmp_path / "photo.png"), "--layout", layout, "--out", str(tmp_path / "out.dxf"),
        "--preview", str(tmp_path / "preview.png")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "out.dxf").exists()
    assert cv.imread(str(tmp_path / "preview.png")) is not None


@pytest.mark.parametrize("tilt", [0, 10, 20, 35])
@pytest.mark.parametrize("seed,count", [(3,4), (17,6)])
def test_free_random_poses(tilt, seed, count):
    image, truth = generate_free(tilt=tilt, seed=seed, count=count)
    result = measure(image)
    assert abs(result.width_mm-truth["width_mm"]) < 1
    assert abs(result.height_mm-truth["height_mm"]) < 1
    assert result.residual_mm < 1


def test_free_metric_corners():
    from easyon_scan.calibration import free_homography, project, SQUARE
    rng = np.random.default_rng(12)
    world = []
    for _ in range(7):
        theta = rng.uniform(-3,3)
        c,s = np.cos(theta),np.sin(theta)
        world.append(SQUARE @ np.array([[c,s],[-s,c]]) + rng.uniform(-500,500,2))
    h = np.array([[2., .3, 1000.], [.1, 2.1, 900.], [.0003,.0002,1.]])
    photo = project(np.concatenate(world),h)
    inverse, _, residual = free_homography(photo)
    assert residual < .0001
    mapped = project(project(SQUARE,h),inverse)
    np.testing.assert_allclose(np.linalg.norm(mapped[1]-mapped[0]),90,atol=.001)


def test_metric_residual_detects_inconsistent_square():
    from easyon_scan.calibration import free_homography, SQUARE
    photo = np.concatenate([SQUARE+[x,y] for x,y in [(0,0),(700,0),(700,900),(0,900)]])
    photo[8:12] = (photo[8:12]-[700,900])*1.12+[700,900]
    _, _, residual_mm = free_homography(photo)
    assert residual_mm > 1


def test_single_card_sheet(tmp_path):
    path = tmp_path / "single.pdf"
    sheet(path, marker_id=17)
    assert path.read_bytes().startswith(b"%PDF")
    with pytest.raises(ValueError):
        sheet(path, marker_id=50)


def test_cli_warns_in_mm(scene, tmp_path, monkeypatch, capsys):
    from easyon_scan import cli
    result = measure(scene[0])
    result.residual_mm = 1.1
    result.residual_px = .1
    monkeypatch.setattr(cli, "measure", lambda *args, **kwargs: result)
    monkeypatch.setattr(sys, "argv", ["easyon-scan", "measure", "unused.png", "--out", str(tmp_path/"warn.dxf")])
    cli.main()
    assert "exceeds 1 mm" in capsys.readouterr().err


@pytest.mark.parametrize("shape", ["sheet", "cup"])
def test_competing_light_object(shape):
    image, _ = generate(perspective=False)
    if shape == "sheet":
        image[1000:1300, 400:580] = 240
    else:
        cv.circle(image, (480, 1600), 110, (240, 240, 240), -1)
    with pytest.raises(ScanError, match="remove light objects"):
        measure(image)


@pytest.mark.parametrize("kind", ["singular", "horizon", "huge"])
def test_bad_homography(monkeypatch, kind):
    from easyon_scan import scan
    image, _ = generate(perspective=False)
    inverse = np.eye(3)
    if kind == "singular":
        inverse[1] = inverse[0]
    elif kind == "horizon":
        inverse[2] = [.001, 0, -1.]
    else:
        inverse[:2, :2] *= 100
    monkeypatch.setattr(scan, "free_homography", lambda photo: (inverse, photo, 0.))
    with pytest.raises(ScanError, match="Degenerate|Extreme"):
        measure(image)


def test_large_preview_is_bounded(monkeypatch):
    from easyon_scan import scan
    original = scan.free_homography
    def enlarged(photo):
        inverse, world, residual = original(photo)
        scale = np.diag([8., 8., 1.])
        return scale @ inverse, world * 8, residual * 8
    monkeypatch.setattr(scan, "free_homography", enlarged)
    image, _ = generate(perspective=False)
    result = measure(image)
    assert max(result.preview.shape[:2]) == 4000
    assert result.height_mm > 4800
