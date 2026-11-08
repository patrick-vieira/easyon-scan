"""Conservative image heuristics, not a certificate of dimensional accuracy."""
import cv2 as cv
import numpy as np


def checks(gray, photo, homography, residual_mm, calibrated):
    groups = photo.reshape(-1, 4, 2)
    focus, whites, blacks = [], [], []
    for group in groups:
        patch = cv.warpPerspective(gray, cv.getPerspectiveTransform(group.astype('float32'),
            np.array([[0, 0], [89, 0], [89, 89], [0, 89]], 'float32')), (90, 90))
        focus.append(float(cv.Laplacian(patch, cv.CV_64F).var()))
        whites.append(float(np.percentile(patch, 90)))
        blacks.append(float(np.percentile(patch, 10)))
    rows = []
    def add(name, value, status, message):
        rows.append(dict(name=name, value=float(value), status=status, message=message))
    size = float(np.min(np.linalg.norm(np.roll(groups, -1, axis=1)-groups, axis=2)))
    add('marker_size_px', size, 'reject' if size < 35 else 'pass', 'Move closer; keep each marker edge at least 35 pixels.')
    add('focus', min(focus), 'reject' if min(focus) < 100 else 'pass', 'Hold the phone steady and focus on the marker cards.')
    contrast = min(np.array(whites)-blacks)
    add('exposure', contrast, 'reject' if contrast < 60 else 'pass', 'Reduce glare or increase diffuse light; preserve black and white marker detail.')
    # Exact black/white pixels are expected on printed cards; clipping alone is ambiguous.
    mask = np.zeros(gray.shape, np.uint8)
    cv.fillConvexPoly(mask, cv.convexHull(photo.astype('float32')).astype('int32'), 1)
    values = gray[mask > 0]
    clipping = np.mean((values <= 1) | (values >= 254))
    add('clipping_fraction', clipping, 'warn' if clipping > .8 else 'pass', 'Check exposure and glare; clipped paper may hide edges.')
    # Inspect the brightest population across the working hull as well as cards.
    # A broad shadow on the paper can miss all four reference cards.
    light = values[values > (float(np.percentile(values, 10)) + float(np.percentile(values, 95)))/2]
    paper_spread = 0. if not len(light) else float((np.percentile(light, 95)-np.percentile(light, 5))/max(np.percentile(light, 95), 1))
    spread = max(paper_spread, (max(whites)-min(whites))/max(max(whites), 1))
    add('shadows', spread, 'reject' if spread > .55 else ('warn' if spread > .25 else 'pass'), 'Use diffuse lighting from both sides and remove shadows across the pattern.')
    center = photo.mean(0)
    inverse = np.linalg.inv(homography)
    world = cv.perspectiveTransform(center[None, None].astype(float), inverse)[0, 0]
    def mapped(p):
        return cv.perspectiveTransform(np.array(p, float)[None], homography)[0]
    p = mapped([world, world+[1, 0], world+[0, 1]])
    singular = np.linalg.svd((p[1:]-p[0]).T, compute_uv=False)
    ratio = singular[0]/singular[1]
    add('plane_tilt_proxy', ratio, 'reject' if ratio > 3 else 'pass', 'Photograph more directly overhead; strong foreshortening reduces reliability.')
    add('marker_consistency_mm', residual_mm, 'reject' if residual_mm > 2 else ('warn' if residual_mm > 1 else 'pass'), 'Possible lens distortion or inconsistent marker scale: use a camera profile, verify declared card sizes and keep them coplanar.')
    add('scale_verification', 0, 'warn', 'Verify every black square matches its declared size with a ruler. Uniform printer scaling cannot be detected from this photo.')
    if not calibrated:
        add('lens_profile', 0, 'warn', 'No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.')
    return rows
