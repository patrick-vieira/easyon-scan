"""Print metric marker cards for free placement or a fixed layout."""
from pathlib import Path

import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import unicodedata

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


def sheet(out, size="a4", layout="free", marker_id=None):
    """Write one physical-size PDF page, at 10 pixels/mm (254 dpi)."""
    if size not in ("a4", "a3"):
        raise ValueError("Sheet size must be a4 or a3")
    if marker_id is not None and not 0 <= marker_id < 50:
        raise ValueError("Marker ID must be 0–49")
    w, h = (210, 297) if size == "a4" else (297, 420)
    page = Image.new("RGB", (w * 10, h * 10), "white")
    draw = ImageDraw.Draw(page)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 29)
        label_font = ImageFont.truetype("DejaVuSans.ttf", 24)
        def printable(text):
            return text
    except OSError:
        font = ImageFont.load_default(size=29)
        label_font = ImageFont.load_default(size=24)
        def printable(text):
            return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()

    positions = ((10, 10), (110, 10), (110, 110), (10, 110))
    cards = list(enumerate(positions)) if marker_id is None else [(marker_id, positions[0])]
    for i, (x, y) in cards:
        marker = cv.aruco.generateImageMarker(dictionary(), i, 900)
        page.paste(Image.fromarray(marker).convert("RGB"), (x * 10, y * 10))
        draw.text((x * 10, (y + 91) * 10), f"ID {i} - 90 mm" + (" - TOP ^" if layout == "fixed" else ""), fill="black", font=label_font)
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
    if layout == "free":
        lines = [
            "EN: Print at 100%, actual size; do not fit to page.",
            "Check the 100 mm ruler and each 90 x 90 mm black square.",
            "Cut on the lines, keeping the white borders around markers.",
            "4+ cards must surround the WHOLE pattern with a clear gap.",
            "PT: Imprima a 100 %, sem ajustar à página; confira a régua.",
            "Confira 100 mm na regua e 90 x 90 mm em cada quadrado.",
            "4+ cartões devem CERCAR TODO o molde, com folga.",
            "Mesmo plano. Molde longe dos cartões e do limite entre eles.",
        ]
    for _, (x, y) in cards:
        x, y = x-5, y-5
        draw.rectangle((x*10, y*10, (x+100)*10, (y+100)*10), outline="gray", width=2)
    for n, line in enumerate(lines):
        draw.text((100, 2120 + n * 55), printable(line), fill="black", font=font)
    draw.line((100, 2670, 1100, 2670), fill="black", width=3)
    for x in range(100, 1101, 100):
        draw.line((x, 2650, x, 2690), fill="black", width=3)
    draw.text((100, 2720), "100 mm - verify / confira", fill="black", font=font)
    page.save(Path(out), "PDF", resolution=254.0)
