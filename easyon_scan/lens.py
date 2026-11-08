"""ChArUco camera calibration; profiles apply to one oriented image resolution."""
import json
from pathlib import Path
import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .markers import dictionary


def board():
    return cv.aruco.CharucoBoard((6, 8), .03, .022, dictionary())


def board_pdf(out):
    page = Image.new('RGB', (2100, 2970), 'white')
    page.paste(Image.fromarray(board().generateImage((1800, 2400))).convert('RGB'), (150, 200))
    ImageDraw.Draw(page).text((150, 2700), 'Print actual size: squares 30 mm. Verify with a ruler.', fill='black', font=ImageFont.load_default(size=28))
    page.save(out, 'PDF', resolution=254.)


def validate_profile(value):
    try:
        k = np.asarray(value['camera_matrix'], float)
        d = np.asarray(value['distortion'], float).ravel()
        size = value['image_size']
        if (value['schema_version'] != 1 or k.shape != (3, 3) or d.size not in (4, 5, 8, 12, 14)
                or not np.isfinite(k).all() or not np.isfinite(d).all()
                or k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1])
                or len(size) != 2 or any(type(x) is not int or x <= 0 for x in size)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('Invalid camera profile') from None
    return k, d, tuple(size)


def load_profile(source):
    value = source if isinstance(source, dict) else json.loads(Path(source).read_text())
    validate_profile(value)
    return value


def undistort(image, profile):
    k, d, size = validate_profile(load_profile(profile))
    if size != (image.shape[1], image.shape[0]):
        raise ValueError('Profile resolution differs from photo; use the same camera, lens, zoom and oriented resolution')
    return cv.undistort(image, k, d)


def calibrate(photos, phone):
    from .scan import load_photo
    if len(photos) < 6:
        raise ValueError('Need at least six ChArUco photos with varied tilts and positions across the frame')
    objects, pixels, sizes = [], [], set()
    detector = cv.aruco.CharucoDetector(board())
    for path in photos:
        image = load_photo(path)
        sizes.add((image.shape[1], image.shape[0]))
        corners, ids, _, _ = detector.detectBoard(cv.cvtColor(image, cv.COLOR_BGR2GRAY))
        if ids is None or len(ids) < 12:
            raise ValueError(f'{path}: need at least 12 ChArUco corners; show more of the board in focus')
        obj, img = board().matchImagePoints(corners, ids)
        objects.append(obj)
        pixels.append(img)
    if len(sizes) != 1:
        raise ValueError('Calibration photos must have the same oriented resolution')
    size = sizes.pop()
    centers = np.array([p.reshape(-1, 2).mean(0) for p in pixels])
    if np.max(np.ptp(centers, axis=0) / size) < .1:
        raise ValueError('Move the board across the frame and vary its tilt; views lack coverage')
    rms, k, d, _, _ = cv.calibrateCamera(objects, pixels, size, None, None, flags=cv.CALIB_FIX_K3)
    if not np.isfinite(rms) or rms > 2:
        raise ValueError('Calibration reprojection error exceeds 2 px; retake sharper, varied views')
    result = dict(schema_version=1, phone=phone, image_size=list(size),
                  camera_matrix=k.tolist(), distortion=d.ravel().tolist(), rms_px=float(rms), views=len(photos))
    validate_profile(result)
    return result
