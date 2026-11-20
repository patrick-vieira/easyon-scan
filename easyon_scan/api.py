"""Public, in-memory flat-pattern API.

Coordinates and dimensions in :class:`Measurement` are millimetres. NumPy
images use OpenCV's BGR channel order; encoded bytes retain EXIF orientation.
"""

from io import BytesIO

import numpy as np

from .dxf import dxf_bytes
from .scan import Measurement, ScanError, measure


def measure_image(
    image: bytes | np.ndarray,
    *,
    layout: str = "free",
    marker_size: dict[int, float] | None = None,
    layout_file=None,
    profile=None,
    reference: str | None = None,
    reference_frontal: bool = False,
) -> Measurement:
    """Measure one encoded photograph or BGR image without writing files.

    ``layout`` is ``free`` or ``fixed``. ``marker_size`` maps ArUco IDs (0–49)
    to their printed black-square side in mm; omitted IDs use 90 mm. A saved
    layout may be supplied as a path. The fixed A3 kit uses ``layout='fixed'``
    and IDs 0–3 with their original 90 mm geometry. Invalid or unmeasurable
    inputs raise :class:`ScanError` or :class:`ValueError`.
    """
    if isinstance(image, bytes):
        if not image:
            raise ScanError("Empty photograph")
        source = BytesIO(image)
    elif isinstance(image, np.ndarray):
        source = image
    else:
        raise TypeError("image must be encoded bytes or a BGR numpy array")
    return measure(source, layout=layout, marker_size=marker_size,
                   layout_file=layout_file, profile=profile, reference=reference,
                   reference_frontal=reference_frontal)


def measurement_to_dxf_bytes(result: Measurement, *, mode: str = "plain",
                             size: str = "BASE", piece_names=None) -> bytes:
    """Export all measured pieces, holes and annotations as millimetre DXF bytes.

    ``mode='aama'`` is an experimental AAMA/ASTM-style representation.
    """
    if not isinstance(result, Measurement):
        raise TypeError("result must be a Measurement")
    return dxf_bytes(result, mode=mode, size=size, piece_names=piece_names)
