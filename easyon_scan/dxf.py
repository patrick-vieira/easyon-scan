"""Plain DXF and experimental AAMA/ASTM-style piece blocks in millimetres."""
from io import StringIO
import ezdxf
import numpy as np
from shapely.geometry.polygon import orient


def dxf_bytes(contour_mm, *, mode="plain", size="BASE", piece_names=None):
    """Export a Measurement, or a legacy single Nx2 outer contour.

    All pieces share one coordinate transform so their relative placement stays
    intact. Plain mode distinguishes holes by winding on the parent's layer.
    AAMA-style mode uses piece blocks and numeric layers; see README limits.
    """
    if mode not in ("plain", "aama"):
        raise ValueError("DXF mode must be plain or aama")
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
    names = list(piece_names) if piece_names is not None else [f"PIECE_{i+1:03d}" for i in range(len(pieces))]
    if len(names) != len(pieces) or any(not isinstance(n, str) or not n.strip() or any(c in n for c in "\n\r") for n in names):
        raise ValueError("Provide one nonempty single-line name per piece")
    if not isinstance(size, str) or not size.strip() or any(c in size for c in "\n\r"):
        raise ValueError("Size must be nonempty single-line text")
    all_points = np.concatenate([p.contour_mm for p in pieces])
    origin = all_points.min(0)
    ymax = all_points[:, 1].max()
    doc = ezdxf.new("R2000" if mode == "aama" else "R2010")
    doc.units = 4
    for name in (("1", "4", "7", "8", "11") if mode == "aama" else ("NOTCH", "GRAIN", "INTERNAL")):
        doc.layers.new(name)
    for i, piece in enumerate(pieces):
        layer = "OUTLINE" if len(pieces) == 1 else f"PIECE_{i+1:03d}"
        if mode == "aama":
            target = doc.blocks.new(f"PIECE_{i+1:03d}")
            doc.modelspace().add_blockref(target.name, (0, 0))
            layer = "1"
            anchor = piece.contour_mm.min(0)
            for offset, text in enumerate((f"Piece Name: {names[i]}", f"Size: {size}")):
                target.add_text(text, dxfattribs={"layer": "1", "height": 3,
                    "insert": (float(anchor[0]-origin[0]), float(ymax-anchor[1]+5+offset*5))})
        else:
            doc.layers.new(layer)
            target = doc.modelspace()
        def polyline(xy, layer, closed=True):
            attrs = {"layer": layer}
            if mode == "aama":
                target.add_polyline2d(xy, close=closed, dxfattribs=attrs)
            else:
                target.add_lwpolyline(xy, close=closed, dxfattribs=attrs)
        polygon = orient(piece.polygon, sign=-1.)
        for ring_index, ring in enumerate([polygon.exterior, *polygon.interiors]):
            xy = np.asarray(ring.coords)[:-1].copy()
            xy[:, 0] -= origin[0]
            xy[:, 1] = ymax - xy[:, 1]
            polyline(xy, "11" if mode == "aama" and ring_index else layer)
        for name, marks in (("NOTCH", piece.notches_mm), ("GRAIN", piece.grain_lines_mm), ("INTERNAL", piece.internal_lines_mm)):
            for mark in marks:
                xy = np.asarray(mark, dtype=float).copy()
                if xy.ndim != 2 or xy.shape[1] != 2 or not np.isfinite(xy).all():
                    raise ValueError("Expected finite Nx2 annotation")
                xy[:, 0] -= origin[0]
                xy[:, 1] = ymax - xy[:, 1]
                if mode == "aama" and name == "NOTCH":
                    if len(xy) != 3:
                        raise ValueError("AAMA notch requires base, tip, base triangle")
                    target.add_point((xy[0]+xy[2])/2, dxfattribs={"layer": "4"})
                elif mode == "aama" and name == "GRAIN":
                    if len(xy) < 2:
                        raise ValueError("Grain requires at least two points")
                    center = xy.mean(0)
                    axis = np.linalg.svd(xy-center, full_matrices=False)[2][0]
                    distances = (xy-center) @ axis
                    target.add_line(center+axis*distances.min(), center+axis*distances.max(), dxfattribs={"layer": "7"})
                else:
                    if len(xy) < 2:
                        raise ValueError("Annotation requires at least two points")
                    polyline(xy, "8" if mode == "aama" else name, closed=name != "INTERNAL")
    stream = StringIO()
    doc.write(stream)
    return doc.encode(stream.getvalue()) if mode == "aama" else stream.getvalue().encode("utf-8")


def export_dxf(contour_mm, out, **options):
    from pathlib import Path
    Path(out).write_bytes(dxf_bytes(contour_mm, **options))
