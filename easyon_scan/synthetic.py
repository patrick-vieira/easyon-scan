"""Small deterministic fixture, independent of the measurement pipeline."""
import argparse
import cv2 as cv
import numpy as np
from .markers import dictionary, ORIGINS


def generate(out=None, perspective=True):
    """Return a BGR photograph and the 400 x 600 mm rectangular ground truth."""
    scale, margin = 3, 30
    image = np.full((1040*scale, 840*scale, 3), 55, np.uint8)
    for i, (x,y) in ORIGINS.items():
        px, py = (x+margin)*scale, (y+margin)*scale
        image[py-15:py+285, px-15:px+285] = 255
        marker = cv.aruco.generateImageMarker(dictionary(), i, 270)
        image[py:py+270, px:px+270] = cv.cvtColor(marker, cv.COLOR_GRAY2BGR)
    # Pixel-centre coordinates: the intensity crossing lies at the true boundary.
    left, top = (190+margin)*scale, (190+margin)*scale
    image[top:top+600*scale, left:left+400*scale] = 240
    if perspective:
        src = np.float32([[0,0],[2519,0],[2519,3119],[0,3119]])
        dst = np.float32([[210,100],[2460,280],[2320,3060],[80,2810]])
        image = cv.warpPerspective(image, cv.getPerspectiveTransform(src,dst), (2600,3200), borderValue=(55,55,55))
    if out is not None and not cv.imwrite(str(out), image):
        raise OSError(f"Could not write {out}")
    return image, {"width_mm":400., "height_mm":600., "area_mm2":240000.}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out")
    args = parser.parse_args()
    generate(args.out)


if __name__ == "__main__":
    main()
