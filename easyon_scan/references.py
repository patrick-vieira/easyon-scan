"""No-printer catalogue. Dimensions are nominal, never photo accuracy guarantees."""
from dataclasses import dataclass
import cv2 as cv
import numpy as np

@dataclass(frozen=True)
class Reference:
    width: float
    height: float
    accuracy: str
    warning: str = ""
    radius: float = 0.

# ISO 216 A4; ANSI/ASME Y14.1 US Letter (inches × 25.4).
# Sources: iso.org/standard/36631.html; asme.org/codes-standards/find-codes-standards/y14-1-drawing-sheet-size-format
CATALOGUE = {
    "a4": Reference(210., 297., "reference-sheet-experimental"),
    "letter": Reference(215.9, 279.4, "reference-sheet-experimental"),
    # ISO/IEC 7810 ID-1 nominal dimensions and corner radius.
    # Source: iso.org/standard/70483.html (ISO/IEC 7810:2019).
    "card": Reference(53.98, 85.60, "small-reference-experimental",
        "Small card: error grows with distance from the reference; avoid extrapolation.", 3.18),
    # Manufacturer body dimensions, excluding cases. Portrait width × height.
    # support.apple.com/en-us/111831 (iPhone 15); /111830 (15 Pro).
    "phone:iphone-15": Reference(71.6, 147.6, "small-reference-experimental"),
    "phone:iphone-15-pro": Reference(70.6, 146.6, "small-reference-experimental"),
    # samsung.com/global/galaxy/galaxy-s24/specs/
    "phone:galaxy-s24": Reference(70.6, 147., "small-reference-experimental"),
    # motorola.com: motorola edge 50 fusion specifications (161.91 × 73.06 mm).
    "phone:edge-50-fusion": Reference(73.06, 161.91, "small-reference-experimental"),
    # BIC J26 Maxi product dimensions vary by listing; nominal envelope only.
    # Source: bic.com J26 Maxi product catalogue, approximately 25 × 82 mm.
    "bic": Reference(25., 82., "approximate-scale-only",
        "WARNING: BIC J26 dimensions are APPROXIMATE. Scale only; NO perspective correction. Frontal photo required."),
}

def get_reference(name):
    if name not in CATALOGUE:
        raise ValueError("Unknown reference; choose " + "|".join(CATALOGUE))
    return CATALOGUE[name]


def detect(gray, name, frontal=False):
    """Leftmost isolated light silhouette; portrait, body edges fully visible.

    Rounded silhouettes use intersections of fitted straight side segments, not
    the inset corner arc endpoints. No dimensions are inferred from screen edges.
    """
    from .scan import ScanError
    spec = get_reference(name)
    if name == "bic" and not frontal:
        raise ScanError("BIC requires explicit frontal=True / --reference-frontal; no perspective correction")
    threshold, mask = cv.threshold(gray, 0, 255, cv.THRESH_BINARY + cv.THRESH_OTSU)
    contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
    contours = [c for c in contours if cv.contourArea(c) > 200]
    if len(contours) < 2:
        raise ScanError("Need an isolated light reference on the left and pattern to its right")
    contour = min(contours, key=lambda c: cv.boundingRect(c)[0])
    x, y, w, h = cv.boundingRect(contour)
    if x < 3 or y < 3 or x+w >= gray.shape[1]-3 or y+h >= gray.shape[0]-3:
        raise ScanError("Reference must be fully visible with dark clearance")
    hull = cv.convexHull(contour)
    quad = cv.approxPolyDP(hull, .025*cv.arcLength(hull, True), True).reshape(-1, 2).astype(float)
    if len(quad) != 4 or not cv.isContourConvex(quad.astype(np.float32)):
        raise ScanError("Reference needs four clear sides")
    quad = quad[np.argsort(np.arctan2(quad[:,1]-quad[:,1].mean(), quad[:,0]-quad[:,0].mean()))]
    quad = np.roll(quad, -np.argmin(quad.sum(axis=1)), axis=0)
    # Fit only central side pixels; rounded corners must not shorten the body.
    from .scan import cruzamento
    points = contour.reshape(-1, 2).astype(float)
    light = np.median(gray[mask > 0])
    dark = np.median(gray[mask == 0])
    points = cruzamento(gray.astype(np.float32), points, (light+dark)/2)
    lines = []
    for a, b in zip(quad, np.roll(quad, -1, axis=0)):
        delta = b-a
        t = (points-a) @ delta / (delta @ delta)
        distance = np.abs(delta[0]*(points[:,1]-a[1])-delta[1]*(points[:,0]-a[0]))/np.linalg.norm(delta)
        side = points[(t > .2) & (t < .8) & (distance < max(3., .025*np.linalg.norm(delta)))]
        if len(side) < 8:
            raise ScanError("Reference sides too small or unclear")
        vx, vy, px, py = cv.fitLine(side.astype(np.float32), cv.DIST_L2, 0, .01, .01).ravel()
        lines.append(np.array([-vy, vx, vy*px-vx*py]))
    corners = []
    for i in range(4):
        p = np.cross(lines[i-1], lines[i])
        if abs(p[2]) < 1e-8:
            raise ScanError("Degenerate reference sides")
        corners.append(p[:2]/p[2])
    corners = np.array(corners)
    if min(np.linalg.norm(np.roll(corners,-1,axis=0)-corners, axis=1)) < 35:
        raise ScanError("Reference too small: move closer")
    if np.linalg.norm(corners[1]-corners[0]) > np.linalg.norm(corners[2]-corners[1]):
        raise ScanError("Place reference in portrait orientation")
    if any(cv.boundingRect(c)[0] <= x+w+5 for c in contours if c is not contour):
        raise ScanError("Place all pattern pieces to the right of the reference with a dark gap")
    world = np.float32([[0,0],[spec.width,0],[spec.width,spec.height],[0,spec.height]])
    inverse = cv.getPerspectiveTransform(corners.astype(np.float32), world)
    if name == "bic":
        scale = spec.height / np.linalg.norm(corners[3]-corners[0])
        v = (corners[1]-corners[0]); v /= np.linalg.norm(v)
        inverse = np.array([[v[0]*scale,v[1]*scale,0],[-v[1]*scale,v[0]*scale,0],[0,0,1.]])
    warnings = ["Experimental reference estimate; ArUco is the most accurate option. No real-photo accuracy validation.",
                "Reference and pattern must be flat and coplanar; verify nominal dimensions physically.",
                "Four sides provide no independent calibration residual; extrapolation and lens distortion can amplify errors."]
    if spec.warning:
        warnings.append(spec.warning)
    if name.startswith("phone:"):
        warnings.append("Remove cases/bumpers. Detect the BODY outline, not screen edges; display plain white to the body edge. Curved screens/bezel contrast can invalidate detection. Small reference: error grows with distance.")
    if not frontal and name != "bic":
        warnings.append("Keep the camera close to overhead; severe perspective can make side identification unreliable.")
    return inverse, corners, warnings
