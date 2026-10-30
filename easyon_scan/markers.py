"""Print cards at actual size, then assemble the fixed table layout."""
from pathlib import Path

import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw

MARKER_MM = 90
WIDTH_MM, HEIGHT_MM = 780, 980
ORIGINS = {0: (0, 0), 1: (690, 0), 2: (690, 890), 3: (0, 890)}
ROI = (90, 90, 690, 890)


def dictionary():
    return cv.aruco.getPredefinedDictionary(cv.aruco.DICT_4X4_50)


def corners_mm(marker_id):
    return np.array(ORIGINS[marker_id], dtype=float) + np.array(
        [[0, 0], [90, 0], [90, 90], [0, 90]], dtype=float
    )


def sheet(out, size="a4"):
    """Write one physical-size PDF page, at 10 pixels/mm (254 dpi)."""
    if size not in ("a4", "a3"):
        raise ValueError("Sheet size must be a4 or a3")
    w, h = (210, 297) if size == "a4" else (297, 420)
    page = Image.new("RGB", (w * 10, h * 10), "white")
    draw = ImageDraw.Draw(page)
    for i, (x, y) in enumerate(((10, 10), (110, 10), (110, 110), (10, 110))):
        marker = cv.aruco.generateImageMarker(dictionary(), i, 900)
        page.paste(Image.fromarray(marker).convert("RGB"), (x * 10, y * 10))
        draw.text((x * 10, (y + 91) * 10), f"ID {i} - 90 mm - TOP ^", fill="black", font_size=24)
    lines = [
        "EN: Print at 100% / actual size. Verify the 100 mm ruler.",
        "Cut cards leaving white borders. Keep every TOP arrow up.",
        "Tape FLAT: IDs 0/1 top, 3/2 bottom. Black outer box: 780 x 980 mm.",
        "Black top-left corners (mm): 0=(0,0), 1=(690,0),",
        "2=(690,890), 3=(0,890). Pattern inside central 600 x 800 mm.",
        "PT: Imprima a 100%, sem ajustar. Confira a regua de 100 mm.",
        "Recorte com borda branca. TOP para cima. Fixe plano na mesa.",
        "Cantos pretos nas coordenadas acima. Molde no centro; fotografe os 4 IDs.",
    ]
    for n, line in enumerate(lines):
        draw.text((100, 2120 + n * 55), line, fill="black", font_size=29)
    draw.line((100, 2670, 1100, 2670), fill="black", width=3)
    for x in range(100, 1101, 100):
        draw.line((x, 2650, x, 2690), fill="black", width=3)
    draw.text((100, 2720), "100 mm - verify / confira", fill="black", font_size=30)
    page.save(Path(out), "PDF", resolution=254.0)
