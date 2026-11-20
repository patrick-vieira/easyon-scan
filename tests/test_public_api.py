"""In-memory adapter contract exercised with synthetic scenes only."""
from io import StringIO
import subprocess
import sys

import cv2 as cv
import ezdxf
import numpy as np
import pytest

from easyon_scan import measure_image, measurement_to_dxf_bytes
from easyon_scan.scan import Measurement, ScanError
from easyon_scan.synthetic import generate, generate_pieces


def test_encoded_bytes_fixed_a3_and_dxf():
    image, _ = generate(perspective=False)
    encoded = cv.imencode(".png", image)[1].tobytes()
    result = measure_image(encoded, layout="fixed")
    assert isinstance(result, Measurement)
    assert set(result.markers) == {0, 1, 2, 3}
    assert result.quality_checks
    assert abs(result.width_mm - 400) < 1
    doc = ezdxf.read(StringIO(measurement_to_dxf_bytes(result).decode()))
    assert doc.units == 4
    assert doc.modelspace().query("LWPOLYLINE")[0].closed


def test_two_pieces_holes_and_missing_markers():
    image, _ = generate_pieces()
    result = measure_image(image, layout="fixed")
    assert len(result.pieces) == 2
    assert [len(p.holes_mm) for p in result.pieces] == [1, 1]
    doc = ezdxf.read(StringIO(measurement_to_dxf_bytes(result).decode()))
    assert len(doc.modelspace().query("LWPOLYLINE")) == 4
    with pytest.raises(ScanError, match="four ArUco"):
        measure_image(np.full((600, 600, 3), 55, np.uint8))


def test_root_import_does_not_import_flask():
    run = subprocess.run([sys.executable, "-c",
        "import sys; import easyon_scan; assert 'flask' not in sys.modules"],
        capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
