"""Small deterministic fixture, independent of the measurement pipeline."""
import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
import json
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


# Parameters describe an image formation model, not a calibrated phone camera.
@dataclass(frozen=True)
class Parameters:
    tilt_deg: float = 0.
    radial_k1: float = 0.
    shadow: float = 0.
    curl_mm: float = 0.
    lighting: float = 0.
    printer_scale: float = 1.
    noise_sigma: float = 0.
    jpeg_quality: int = 100

    def __post_init__(self):
        ranges = {"tilt_deg": (-45, 45), "radial_k1": (-.4, .4),
                  "shadow": (0, .9), "curl_mm": (0, 20), "lighting": (0, .9),
                  "printer_scale": (.9, 1.1), "noise_sigma": (0, 50),
                  "jpeg_quality": (1, 100)}
        for name, (low, high) in ranges.items():
            value = getattr(self, name)
            if not np.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
        if not isinstance(self.jpeg_quality, int):
            raise ValueError("jpeg_quality must be an integer")


def generate_case(out=None, *, seed=0, parameters=None):
    """Return a fixed-layout BGR case and serializable nominal metric truth.

    Printer scaling affects only marker cards; the independent pattern remains
    400 x 600 mm. Curl is a smooth projected paper displacement with amplitude
    curl_mm, not a physical cloth or 3D surface simulation.
    """
    p = parameters or Parameters()
    rng = np.random.default_rng(seed)
    image, truth = generate(perspective=False)
    # Reprint each entire card about its black-square centre.
    if p.printer_scale != 1:
        for x, y in ORIGINS.values():
            px, py = (x+30)*3, (y+30)*3
            card = image[py-15:py+285, px-15:px+285].copy()
            image[py-30:py+300, px-30:px+300] = 55
            size = round(300*p.printer_scale)
            card = cv.resize(card, (size, size), interpolation=cv.INTER_NEAREST)
            left, top = px+135-size//2, py+135-size//2
            image[top:top+size, left:left+size] = card
    h, w = image.shape[:2]
    yy, xx = np.indices((h, w), dtype=np.float32)
    phase = rng.uniform(0, 2*np.pi)
    if p.curl_mm:
        # Warp only the paper and surrounding table; cards stay in their plane.
        region = (xx > 570) & (xx < 1950) & (yy > 570) & (yy < 2490)
        displacement = 3*p.curl_mm*np.sin(2*np.pi*(yy-660)/1800+phase)
        map_x = xx + np.where(region, displacement, 0).astype(np.float32)
        image = cv.remap(image, map_x, yy, cv.INTER_LINEAR)
    if p.shadow or p.lighting:
        shade = 1 - p.shadow*np.exp(-((xx/w-.55)**2/.025+(yy/h-.5)**2/.18))
        shade *= 1 + p.lighting*(.65*(xx/w-.5)+.35*(yy/h-.5))*2
        image = np.clip(image.astype(np.float32)*shade[..., None], 0, 255).astype(np.uint8)
    if p.tilt_deg:
        a = np.deg2rad(p.tilt_deg)
        # Rotation around the horizontal camera axis, focal length 2*h.
        k = np.array([[2*h, 0, w/2], [0, 2*h, h/2], [0, 0, 1.]])
        rotation = np.array([[1., 0, 0], [0, np.cos(a), 0], [0, np.sin(a), 1.]])
        # Widen framing as tilt increases to keep the full marker envelope visible.
        zoom = 1 / (1 + abs(np.sin(a))*.4)
        framing = np.array([[zoom, 0, w/2*(1-zoom)],
                            [0, zoom, h/2*(1-zoom)], [0, 0, 1.]])
        image = cv.warpPerspective(image, framing @ k @ rotation @ np.linalg.inv(k),
                                   (w, h), borderValue=(55,)*3)
    if p.radial_k1:
        # Inverse radial sampling: r is normalized by half the image diagonal.
        radius = np.hypot(w, h)/2
        nx, ny = (xx-w/2)/radius, (yy-h/2)/radius
        factor = 1+p.radial_k1*(nx*nx+ny*ny)
        image = cv.remap(image, (w/2+nx*factor*radius).astype(np.float32),
                         (h/2+ny*factor*radius).astype(np.float32), cv.INTER_LINEAR,
                         borderMode=cv.BORDER_CONSTANT, borderValue=(55,)*3)
    if p.noise_sigma:
        # Row chunks avoid a second full-size floating point RGB allocation.
        for start in range(0, h, 128):
            chunk = image[start:start+128]
            image[start:start+128] = np.clip(chunk.astype(np.float32) +
                rng.normal(0, p.noise_sigma, chunk.shape), 0, 255).astype(np.uint8)
    if p.jpeg_quality < 100:
        ok, encoded = cv.imencode('.jpg', image, [cv.IMWRITE_JPEG_QUALITY, p.jpeg_quality])
        if not ok:
            raise OSError('Could not encode JPEG')
        image = cv.imdecode(encoded, cv.IMREAD_COLOR)
    truth.update({"contour_mm": [[190.,190.], [590.,190.], [590.,790.], [190.,790.]],
                  "marker_size_mm": 90*p.printer_scale})
    metadata = {"seed": int(seed), "layout": "fixed", "parameters": asdict(p), "ground_truth": truth}
    if out is not None and not cv.imwrite(str(out), image):
        raise OSError(f"Could not write {out}")
    return image, metadata


def generate_batch(folder, *, count=10, seed=0, parameters=None):
    """Write lossless PNGs (including simulated JPEG artifacts) and a manifest.

    Child seeds are seed+i; existing case files/manifest are never overwritten.
    """
    if count < 1:
        raise ValueError('count must be positive')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / f'case-{i:04d}.png' for i in range(count)]
    manifest = folder / 'manifest.json'
    if manifest.exists() or any(path.exists() for path in paths):
        raise ValueError('Batch destination already contains case files or manifest')
    cases = []
    for i, path in enumerate(paths):
        _, metadata = generate_case(path, seed=seed+i, parameters=parameters)
        cases.append({"file": path.name, **metadata})
    document = {"schema_version": 1, "seed": seed, "cases": cases}
    manifest.write_text(json.dumps(document, indent=2)+'\n', encoding='utf-8')
    return document


def generate_pieces(*, perspective=False):
    """Two separated rectangular pieces, each with a rectangular through-hole.

    Ground truth uses the fixed marker frame. Dark interior regions represent
    visible table, not printed ink; photographs cannot establish that distinction.
    """
    image, _ = generate(perspective=False)
    image[660:2460, 660:1860] = 55
    pieces = []
    for left, top, width, height in [(190, 190, 170, 600), (390, 240, 200, 450)]:
        x, y = (left+30)*3, (top+30)*3
        image[y:y+height*3, x:x+width*3] = 240
        image[y+180:y+360, x+120:x+300] = 55
        pieces.append(dict(width_mm=width, height_mm=height, area_mm2=width*height-3600))
    if perspective:
        src = np.float32([[0,0],[2519,0],[2519,3119],[0,3119]])
        dst = np.float32([[210,100],[2460,280],[2320,3060],[80,2810]])
        image = cv.warpPerspective(image, cv.getPerspectiveTransform(src,dst),
                                   (2600,3200), borderValue=(55,)*3)
    return image, pieces


if __name__ == "__main__":
    main()
