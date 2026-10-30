"""Detect references and measure the light paper outline in source-image coordinates."""
from dataclasses import dataclass
import cv2 as cv
import numpy as np
from PIL import Image, ImageOps
from shapely.geometry import Polygon
from .markers import dictionary, corners_mm, ROI, WIDTH_MM, HEIGHT_MM
from .calibration import free_homography, project

class ScanError(ValueError):
    """The photograph cannot be measured reliably."""

@dataclass
class Measurement:
    contour_mm: np.ndarray
    area_mm2: float
    width_mm: float
    height_mm: float
    residual_px: float
    residual_mm: float
    preview: np.ndarray

    def summary(self):
        return {k: getattr(self, k) for k in
                ("area_mm2", "width_mm", "height_mm", "residual_px", "residual_mm")}


def load_photo(source):
    """Apply phone EXIF orientation; source is a path or binary stream."""
    with Image.open(source) as image:
        if image.width * image.height > 40_000_000:
            raise ScanError("Photo exceeds 40 megapixels")
        return cv.cvtColor(np.asarray(ImageOps.exif_transpose(image).convert("RGB")), cv.COLOR_RGB2BGR)


def sample(channel,points):
    return np.concatenate([cv.remap(channel,p[:,0].astype(np.float32).reshape(-1,1),p[:,1].astype(np.float32).reshape(-1,1),cv.INTER_LINEAR).ravel() for p in np.array_split(points,max(1,int(np.ceil(len(points)/30000))))])

def cruzamento(channel,points,level):
    p=points.copy()
    for _ in range(5):
        value=sample(channel,p)
        gx=(sample(channel,p+[.25,0])-sample(channel,p-[.25,0]))/.5
        gy=(sample(channel,p+[0,.25])-sample(channel,p-[0,.25]))/.5
        grad=np.c_[gx,gy]; denom=np.maximum(gx*gx+gy*gy,1.)
        step=(np.asarray(level)-value)[:,None]*grad/denom[:,None] if np.ndim(level) else (level-value)[:,None]*grad/denom[:,None]
        norm=np.linalg.norm(step,axis=1); step*=np.minimum(1,.5/np.maximum(norm,1e-8))[:,None]
        p+=step
    return p


def measure(source, layout="free"):
    if layout not in ("free", "fixed"):
        raise ValueError("Layout must be free or fixed")
    image = load_photo(source) if not isinstance(source, np.ndarray) else source
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ScanError("Expected a colour photograph")
    gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
    params = cv.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv.aruco.CORNER_REFINE_APRILTAG
    corners, ids, _ = cv.aruco.ArucoDetector(dictionary(), params).detectMarkers(gray)
    found = {} if ids is None else {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners)}
    if len(found) < 4 or (layout == "fixed" and not all(i in found for i in range(4))):
        raise ScanError("Need at least four ArUco markers, fully visible (fixed: IDs 0–3)")
    if len(found) != len(ids):
        raise ScanError("Duplicate marker IDs")
    selected = sorted(found) if layout == "free" else list(range(4))
    photo = np.concatenate([found[i] for i in selected]).astype(np.float32)
    photo = cv.cornerSubPix(gray, photo.reshape(-1, 1, 2), (5, 5), (-1, -1),
                            (cv.TERM_CRITERIA_EPS | cv.TERM_CRITERIA_COUNT, 40, .001)).reshape(-1, 2).astype(float)
    if layout == "free":
        inverse, world, residual_mm = free_homography(photo)
        homography = np.linalg.inv(inverse)
    else:
        world = np.concatenate([corners_mm(i) for i in selected])
        homography, _ = cv.findHomography(world, photo, 0)
        if homography is None:
            raise ScanError("Degenerate marker layout")
        inverse = np.linalg.inv(homography)
        residual_mm = float(np.sqrt(np.mean(np.sum((project(photo, inverse)-world)**2, axis=1))))
    if not np.isfinite(homography).all() or np.linalg.cond(homography) > 1e9:
        raise ScanError("Degenerate marker layout")
    residual = float(np.sqrt(np.mean(np.sum((project(world, homography)-photo)**2, axis=1))))
    x0, y0, x1, y1 = ROI
    if layout == "fixed":
        roi = project([[x0,y0],[x1,y0],[x1,y1],[x0,y1]], homography)
        if np.any(roi < 0) or np.any(roi[:, 0] >= gray.shape[1]) or np.any(roi[:, 1] >= gray.shape[0]):
            raise ScanError("Central working area is outside the photo")
    else:
        roi = cv.convexHull(photo.astype(np.float32)).reshape(-1, 2)
    a = np.maximum(np.floor(roi.min(0)-3).astype(int), 0)
    b = np.minimum(np.ceil(roi.max(0)+3).astype(int), [gray.shape[1]-1, gray.shape[0]-1])
    crop = gray[a[1]:b[1]+1, a[0]:b[0]+1]
    valid = np.zeros(crop.shape, np.uint8)
    cv.fillConvexPoly(valid, np.rint(roi-a).astype(np.int32), 255)
    if layout == "free":
        for marker in photo.reshape(-1, 4, 2):
            expanded = marker.mean(0) + (marker-marker.mean(0))*1.25
            cv.fillConvexPoly(valid, np.rint(expanded-a).astype(np.int32), 0)
    values = crop[valid > 0]
    otsu, _ = cv.threshold(values, 0, 255, cv.THRESH_BINARY+cv.THRESH_OTSU)
    dark, light = values[values <= otsu], values[values > otsu]
    if not len(dark) or not len(light) or np.median(light)-np.median(dark) < 30:
        raise ScanError("Need light paper on a contrasting dark table")
    threshold = (float(np.median(dark))+float(np.median(light)))/2
    mask = ((crop > threshold) & (valid > 0)).astype(np.uint8)*255
    contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
    if not contours:
        raise ScanError("No paper outline found")
    outline = max(contours, key=cv.contourArea).reshape(-1, 2)
    if layout == "free":
        clearance = cv.distanceTransform(valid, cv.DIST_L2, 3)
        if np.min(clearance[outline[:, 1], outline[:, 0]]) < 2:
            raise ScanError("Paper touches marker cards or working area edge; leave a gap")
    pixels = outline.astype(float)+a
    pixels = cruzamento(gray.astype(np.float32), pixels, threshold)
    mm = cv.perspectiveTransform(pixels[None], inverse)[0]
    if len(mm) < 20 or not np.isfinite(mm).all():
        raise ScanError("Invalid outline")
    if layout == "fixed" and min((mm[:,0]-x0).min(), (x1-mm[:,0]).min(), (mm[:,1]-y0).min(), (y1-mm[:,1]).min()) < 3:
        raise ScanError("Paper touches working area edge; leave at least 3 mm clearance")
    polygon = Polygon(mm)
    if not polygon.is_valid or polygon.area < 100:
        raise ScanError("Invalid or too small paper outline")
    if layout == "free":
        origin = mm.min(0)-20
        extent = np.ceil(mm.max(0)-origin+20).astype(int)
        shift = np.array([[1.,0.,-origin[0]],[0.,1.,-origin[1]],[0.,0.,1.]])
        preview = cv.warpPerspective(image, shift @ inverse, tuple(extent))
        cv.polylines(preview, [np.rint(mm-origin).astype(np.int32)], True, (0,0,255), 2)
        width, height = sorted(cv.minAreaRect(mm.astype(np.float32))[1])
    else:
        preview = cv.warpPerspective(image, inverse, (WIDTH_MM, HEIGHT_MM))
        cv.polylines(preview, [np.rint(mm).astype(np.int32)], True, (0,0,255), 2)
        width, height = np.ptp(mm, axis=0)
    return Measurement(mm, float(polygon.area), float(width), float(height), residual, residual_mm, preview)
