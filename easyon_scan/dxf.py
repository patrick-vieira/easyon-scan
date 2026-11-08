"""Plain geometric DXF with closed piece outlines and holes in millimetres."""
from io import StringIO
import ezdxf
import numpy as np
from shapely.geometry.polygon import orient


def dxf_bytes(contour_mm):
    """Export a Measurement, or a legacy single Nx2 outer contour.

    All pieces share one coordinate transform so their relative placement stays
    intact. Holes share their parent's layer; winding distinguishes inner rings.
    """
    from .scan import Piece, Measurement
    pieces = (contour_mm.pieces if isinstance(contour_mm, Measurement)
              else [Piece(np.asarray(contour_mm, dtype=float), [])])
    if not pieces:
        raise ValueError("Expected at least one piece")
    for piece in pieces:
        for ring in [piece.contour_mm, *piece.holes_mm]:
            points = np.asarray(ring)
            if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
                raise ValueError("Expected finite Nx2 contour")
        if not piece.polygon.is_valid or piece.polygon.area <= 0:
            raise ValueError("Expected a valid nonempty polygon")
    all_points = np.concatenate([p.contour_mm for p in pieces])
    origin = all_points.min(0)
    ymax = all_points[:, 1].max()
    doc = ezdxf.new("R2010")
    doc.units = 4
    for name in ("NOTCH", "GRAIN"):
        doc.layers.new(name)
    for i, piece in enumerate(pieces):
        layer = "OUTLINE" if len(pieces) == 1 else f"PIECE_{i+1:03d}"
        doc.layers.new(layer)
        polygon = orient(piece.polygon, sign=-1.)
        for ring in [polygon.exterior, *polygon.interiors]:
            xy = np.asarray(ring.coords)[:-1].copy()
            xy[:, 0] -= origin[0]
            xy[:, 1] = ymax - xy[:, 1]
            doc.modelspace().add_lwpolyline(xy, close=True, dxfattribs={"layer": layer})
        for name, marks in (("NOTCH", piece.notches_mm), ("GRAIN", piece.grain_lines_mm)):
            for mark in marks:
                xy = np.asarray(mark, dtype=float).copy()
                if xy.ndim != 2 or xy.shape[1] != 2 or not np.isfinite(xy).all():
                    raise ValueError("Expected finite Nx2 annotation")
                xy[:, 0] -= origin[0]
                xy[:, 1] = ymax - xy[:, 1]
                doc.modelspace().add_lwpolyline(xy, close=True, dxfattribs={"layer": name})
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def export_dxf(contour_mm, out):
    from pathlib import Path
    Path(out).write_bytes(dxf_bytes(contour_mm))
