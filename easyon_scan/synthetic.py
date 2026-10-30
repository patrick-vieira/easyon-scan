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


def generate_free(out=None, tilt=0., seed=0, count=4):
    """Random card poses, projected with a pinhole camera tilted 0–35 degrees."""
    rng = np.random.default_rng(seed)
    image = np.full((4200, 3600, 3), 55, np.uint8)
    # Pattern centered at the origin, camera looks at this plane's origin.
    angle = np.deg2rad(tilt)
    camera = np.array([[3., np.sin(angle), 1800.], [0., 3*np.cos(angle)+2100*np.sin(angle)/1800, 2100.],
                       [0., np.sin(angle)/1800, 1.]])
    def mapped(points):
        return cv.perspectiveTransform(np.array(points, float)[None], camera)[0].astype(np.float32)
    for i in range(count):
        theta = 2*np.pi*i/count + np.pi/4 + rng.uniform(-.12, .12)
        center = np.array([500*np.cos(theta), 650*np.sin(theta)])
        phi = rng.uniform(-np.pi, np.pi)
        rotation = np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]])
        texture = np.full((300,300,3), 255, np.uint8)
        marker = cv.aruco.generateImageMarker(dictionary(), i, 270)
        texture[15:285,15:285] = cv.cvtColor(marker, cv.COLOR_GRAY2BGR)
        card = np.array([[-50,-50],[50,-50],[50,50],[-50,50]]) @ rotation.T + center
        transform = cv.getPerspectiveTransform(np.float32([[0,0],[300,0],[300,300],[0,300]]), mapped(card))
        warped = cv.warpPerspective(texture, transform, (3600,4200))
        mask = cv.warpPerspective(np.full((300,300),255,np.uint8), transform, (3600,4200))
        image[mask>0] = warped[mask>0]
    polygon = mapped([[-200,-300],[200,-300],[200,300],[-200,300]])
    paper = np.full((1800,1200), 255, np.uint8)
    transform = cv.getPerspectiveTransform(
        np.float32([[-.5,-.5],[1199.5,-.5],[1199.5,1799.5],[-.5,1799.5]]), polygon)
    mask = cv.warpPerspective(paper, transform, (3600,4200))
    image = np.rint(image*(1-mask[:,:,None]/255)+240*(mask[:,:,None]/255)).astype(np.uint8)
    if out is not None and not cv.imwrite(str(out),image):
        raise OSError(f"Could not write {out}")
    return image, {"width_mm":400., "height_mm":600., "area_mm2":240000.}
