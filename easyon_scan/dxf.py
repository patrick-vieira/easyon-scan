"""Plain geometric DXF, with a closed outline and millimetre units."""
from io import StringIO
import ezdxf
import numpy as np
from shapely.geometry import Polygon


def dxf_bytes(contour_mm):
    points = np.asarray(contour_mm, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("Expected finite Nx2 contour")
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Expected a valid nonempty polygon")
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.layers.new("OUTLINE")
    # Image coordinates have y down; DXF has y up. Origin at lower left.
    xy = points - points.min(axis=0)
    xy[:, 1] = np.ptp(points[:, 1]) - xy[:, 1]
    doc.modelspace().add_lwpolyline(xy, close=True, dxfattribs={"layer": "OUTLINE"})
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def export_dxf(contour_mm, out):
    from pathlib import Path
    Path(out).write_bytes(dxf_bytes(contour_mm))
