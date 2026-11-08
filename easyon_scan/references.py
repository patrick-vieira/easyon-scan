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
    # BIC Mini/J25 product catalogue: nominal envelope ~22 x 62 mm.
    # Listing dimensions vary; unverified approximate source, not metrology.
    "bic-mini": Reference(22., 62., "approximate-scale-only",
        "BIC Mini J25: approximate 22 x 62 mm. Scale only; confirm with a ruler."),
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


ERRORS = {
    "reference_border": "Reference touches the image border; keep it fully visible",
    "reference_missing": "No object with the reference shape found",
    "reference_ambiguous": "Two similar reference candidates; remove the extra object",
    "pattern_missing": "Pattern not found",
    "pattern_border": "Pattern touches the image border",
}


def foreground(image, *, with_distance=False, segment=True):
    """Robust border background model in Lab; retain colour as well as luminance."""
    if image.ndim == 2:
        image = cv.cvtColor(image, cv.COLOR_GRAY2BGR)
    lab = cv.cvtColor(image, cv.COLOR_BGR2LAB).astype(np.float32)
    border = np.concatenate((lab[0], lab[-1], lab[:, 0], lab[:, -1]))
    samples = border[::max(1,len(border)//2000)].copy()
    samples = samples[samples[:,0] <= np.median(samples[:,0])+45]
    samples = samples[np.linalg.norm(samples[:,1:]-np.median(samples[:,1:],axis=0),axis=1) < 45]
    count = min(8,len(np.unique(samples,axis=0)))
    cv.setRNGSeed(0)
    _,_,centres = cv.kmeans(samples, count, None,
        (cv.TERM_CRITERIA_EPS+cv.TERM_CRITERIA_MAX_ITER,30,.1),3,cv.KMEANS_PP_CENTERS)
    distance = np.full(lab.shape[:2],255.,np.float32)
    for centre in centres:
        distance = np.minimum(distance,np.linalg.norm(lab-centre,axis=2))
    distance = np.clip(distance,0,255).astype(np.uint8)
    smooth = cv.GaussianBlur(distance, (5, 5), 0)
    threshold, mask = cv.threshold(smooth, 0, 255, cv.THRESH_BINARY+cv.THRESH_OTSU)
    cutoff = max(20, threshold)
    mask = (smooth > cutoff).astype(np.uint8)*255
    if segment and np.std(border[:,0]) > 10:
        # Graph-cut uses local colour continuity to separate objects from table glare.
        labels = np.where(smooth>cutoff,cv.GC_PR_FGD,cv.GC_PR_BGD).astype(np.uint8)
        labels[0,smooth[0]<80] = cv.GC_BGD
        labels[-1,smooth[-1]<80] = cv.GC_BGD
        labels[:,0][smooth[:,0]<80] = cv.GC_BGD
        labels[:,-1][smooth[:,-1]<80] = cv.GC_BGD
        luminance = cv.cvtColor(image,cv.COLOR_BGR2GRAY).astype(np.float32)
        texture = cv.blur(np.abs(luminance-cv.GaussianBlur(luminance,(0,0),2)),(21,21))
        labels[(texture>17)&(smooth<100)] = cv.GC_BGD
        labels[(texture<8)&(smooth>25)] = cv.GC_FGD
        labels[smooth>max(110,cutoff*2)] = cv.GC_FGD
        if np.any(labels==cv.GC_FGD) and np.any(labels==cv.GC_BGD):
            cv.grabCut(image,labels,None,np.zeros((1,65)),np.zeros((1,65)),5,cv.GC_INIT_WITH_MASK)
            mask = np.isin(labels,[cv.GC_FGD,cv.GC_PR_FGD]).astype(np.uint8)*255
    kernel = np.ones((5,5),np.uint8)
    mask = cv.morphologyEx(mask,cv.MORPH_CLOSE,kernel,iterations=2)
    mask = cv.morphologyEx(mask,cv.MORPH_OPEN,np.ones((3,3),np.uint8))
    return (mask, distance) if with_distance else mask


def detect(image, name, frontal=False, *, details=False):
    """Select an isolated reference by shape, independently of position and colour."""
    from .scan import ScanError
    spec = get_reference(name)
    if name in ("bic", "bic-mini") and not frontal:
        raise ScanError("BIC requires explicit frontal=True / --reference-frontal; no perspective correction")
    mask, distance = foreground(image, with_distance=True)
    contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
    contours = [c for c in contours if cv.contourArea(c) > max(200, mask.size*.0003)]
    overlay = cv.cvtColor(image, cv.COLOR_GRAY2BGR) if image.ndim == 2 else image.copy()
    cv.drawContours(overlay, contours, -1, (0, 180, 255), 3)
    def fail(code):
        error = ScanError(ERRORS[code])
        error.code, error.overlay = code, overlay
        raise error
    def touches(c):
        x,y,w,h = cv.boundingRect(c)
        return x < 3 or y < 3 or x+w >= mask.shape[1]-2 or y+h >= mask.shape[0]-2
    target = spec.height/spec.width
    ranked = []
    for c in contours:
        rect = cv.minAreaRect(c)
        lo, hi = sorted(rect[1])
        if lo < 10:
            continue
        q = cv.approxPolyDP(cv.convexHull(c), .025*cv.arcLength(cv.convexHull(c), True), True).reshape(-1,2)
        if len(q) == 4:
            lengths = np.linalg.norm(q-np.roll(q,-1,axis=0),axis=1)
            loq, hiq = sorted(((lengths[0]+lengths[2])/2, (lengths[1]+lengths[3])/2))
            ratio = hiq/loq
        else:
            ratio = hi/lo
        rectangularity = cv.contourArea(c)/(lo*hi)
        score = abs(np.log(ratio/target))
        if score < .24 and rectangularity > (.65 if name in ("bic", "bic-mini") else .78):
            ranked.append((score - (.35*np.log(cv.contourArea(c)) if name in ("a4", "letter") else 0), c, rect))
    ranked.sort(key=lambda item: item[0])
    if not ranked:
        # A clipped rectangular object may have lost its nominal aspect ratio.
        if any(touches(c) and cv.contourArea(c)/max(1, np.prod(cv.minAreaRect(c)[1])) > .8 for c in contours):
            fail("reference_border")
        fail("reference_missing")
    score, contour, rect = ranked[0]
    cv.drawContours(overlay, [contour], -1, (0,255,0), 5)
    if touches(contour):
        fail("reference_border")
    if len(ranked) > 1 and ranked[1][0]-score < .015:
        fail("reference_ambiguous")
    remaining = [c for c in contours if c is not contour and cv.contourArea(c) >= mask.size*.005]
    inside = [c for c in remaining if not touches(c)]
    if not inside:
        fail("pattern_border" if remaining else "pattern_missing")
    pattern = max(inside, key=cv.contourArea)
    cv.drawContours(overlay, [pattern], -1, (255,180,0), 3)
    pattern_mask = np.zeros_like(mask)
    cv.drawContours(pattern_mask, [pattern], -1, 255, -1)
    # Start with the hull quadrilateral for perspective; minAreaRect handles rounded bodies.
    hull = cv.convexHull(contour)
    quad = cv.approxPolyDP(hull, .025*cv.arcLength(hull, True), True).reshape(-1,2)
    if len(quad) != 4:
        quad = cv.boxPoints(rect)
    quad = quad.astype(float)
    quad = quad[np.argsort(np.arctan2(quad[:,1]-quad[:,1].mean(), quad[:,0]-quad[:,0].mean()))]
    quad = np.roll(quad, -np.argmin(quad.sum(axis=1)), axis=0)
    points = contour.reshape(-1,2).astype(float)
    # Refine on the segmented silhouette, avoiding internal printed edges.
    from .scan import cruzamento
    inside_mask = np.zeros_like(mask)
    cv.drawContours(inside_mask, [contour], -1, 255, -1)
    edge_level = float(np.median(distance[inside_mask>0]))/2
    points = cruzamento(distance.astype(np.float32), points, edge_level)
    lines = []
    for a,b in zip(quad, np.roll(quad,-1,axis=0)):
        d = b-a
        t = (points-a)@d/(d@d)
        dist = np.abs(d[0]*(points[:,1]-a[1])-d[1]*(points[:,0]-a[0]))/np.linalg.norm(d)
        side = points[(t>.2)&(t<.8)&(dist<max(4,.04*np.linalg.norm(d)))]
        if len(side)<4:
            fail("reference_missing")
        vx,vy,px,py = cv.fitLine(side.astype(np.float32),cv.DIST_L2,0,.01,.01).ravel()
        lines.append(np.array([-vy,vx,vy*px-vx*py]))
    corners = []
    for i in range(4):
        point = np.cross(lines[i-1],lines[i])
        if abs(point[2])<1e-8:
            fail("reference_missing")
        corners.append(point[:2]/point[2])
    corners = np.array(corners)
    if np.linalg.norm(corners[1]-corners[0]) > np.linalg.norm(corners[2]-corners[1]):
        corners = np.roll(corners,-1,axis=0)
    world = np.float32([[0,0],[spec.width,0],[spec.width,spec.height],[0,spec.height]])
    inverse = cv.getPerspectiveTransform(corners.astype(np.float32), world)
    if name in ("bic", "bic-mini"):
        scale = spec.height/np.linalg.norm(corners[3]-corners[0])
        v = corners[1]-corners[0]; v /= np.linalg.norm(v)
        inverse = np.array([[v[0]*scale,v[1]*scale,0],[-v[1]*scale,v[0]*scale,0],[0,0,1.]])
    warnings = ["Experimental reference estimate; ArUco is the most accurate option. No real-photo accuracy validation.",
                "Reference and pattern must be flat and coplanar; verify nominal dimensions physically.",
                "Four sides provide no independent calibration residual; extrapolation and lens distortion can amplify errors."]
    if spec.warning:
        warnings.append(spec.warning)
    if name.startswith("phone:"):
        warnings.append("Remove cases/bumpers. Detect the BODY outline, not screen edges; display plain white to the body edge. Curved screens/bezel contrast can invalidate detection. Small reference: error grows with distance.")
    if not frontal and name not in ("bic", "bic-mini"):
        warnings.append("Keep the camera close to overhead; severe perspective can make side identification unreliable.")
    result = (inverse, corners, warnings)
    return (*result, pattern_mask, overlay) if details else result
