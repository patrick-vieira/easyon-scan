"""Detect references and measure the light paper outline in source-image coordinates."""
from dataclasses import dataclass, field
import cv2 as cv
import numpy as np
from PIL import Image, ImageOps
from shapely.geometry import Polygon
from .markers import dictionary, corners_mm, ROI, WIDTH_MM, HEIGHT_MM
from .calibration import free_homography, project

class ScanError(ValueError):
    """The photograph cannot be measured reliably."""

@dataclass
class Piece:
    contour_mm: np.ndarray
    holes_mm: list
    notches_mm: list = field(default_factory=list)
    grain_lines_mm: list = field(default_factory=list)

    internal_lines_mm: list = field(default_factory=list)

    @property
    def polygon(self):
        return Polygon(self.contour_mm, self.holes_mm)


@dataclass
class Measurement:
    contour_mm: np.ndarray
    area_mm2: float
    width_mm: float
    height_mm: float
    residual_px: float
    residual_mm: float
    preview: np.ndarray
    quality_checks: list = None
    pieces: list = None
    markers: dict = None

    reference: str = "aruco"
    estimated_accuracy_class: str = "aruco-experimental"
    warnings: list = field(default_factory=list)

    def summary(self):
        result = {k: getattr(self, k) for k in
                ("area_mm2", "width_mm", "height_mm", "residual_px", "residual_mm", "quality_checks")}
        result.update(reference=self.reference, estimated_accuracy_class=self.estimated_accuracy_class, warnings=self.warnings or [r["message"] for r in self.quality_checks or [] if r["status"] == "warn"])
        result["piece_count"] = len(self.pieces or [])
        result["pieces"] = [{"area_mm2": p.polygon.area,
                             "hole_count": len(p.holes_mm),
                             "notch_count": len(p.notches_mm),
                             "grain_count": len(p.grain_lines_mm)} for p in self.pieces or []]
        if hasattr(self, "objects"):
            result.update(objects=self.objects, check_reference=self.check_reference, scale_mm_per_pixel=self.scale_mm_per_pixel, mode="multi-object")
        return result


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


def measure(source, layout="free", profile=None, *, quality_only=False, marker_size=None, layout_file=None, save_layout=None, reference=None, reference_frontal=False, debug_overlay=None, all_objects=False, check_reference=None, grid_pitch_mm=5.):
    if layout not in ("free", "fixed"):
        raise ValueError("Layout must be free or fixed")
    if reference and (layout != "free" or marker_size or layout_file or save_layout):
        raise ValueError("References require free single-photo mode without marker layout options")
    from .layout import marker_sizes, load_layout
    sizes = marker_sizes(marker_size)
    saved = load_layout(layout_file) if layout_file else None
    if saved is not None:
        if layout != "free":
            raise ValueError("Saved layouts require free mode")
        for k, size in sizes.items():
            if k in saved and not np.isclose(np.linalg.norm(saved[k][1]-saved[k][0]), size):
                raise ValueError("Declared marker size conflicts with saved layout")
    if layout == "fixed" and sizes:
        raise ValueError("Custom sizes require free layout")
    image = load_photo(source) if not isinstance(source, np.ndarray) else source
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ScanError("Expected a colour photograph")
    if profile is not None:
        from .lens import undistort
        image = undistort(image, profile)
    if check_reference and not all_objects:
        raise ValueError("Check reference requires multi-object mode")
    if all_objects:
        from .multi import measure_all
        result = measure_all(image, reference, check_reference, grid_pitch_mm)
        if debug_overlay and not cv.imwrite(str(debug_overlay), result.preview):
            raise OSError("Could not write diagnostic overlay")
        return result.quality_checks if quality_only else result
    gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
    if reference:
        from .references import detect, get_reference
        try:
            inverse, corners, warnings, pattern_mask, overlay = detect(image, reference, reference_frontal, details=True)
        except ScanError as exc:
            if debug_overlay and getattr(exc, "overlay", None) is not None:
                cv.imwrite(str(debug_overlay), exc.overlay)
            raise
        if debug_overlay:
            if not cv.imwrite(str(debug_overlay), overlay):
                raise OSError("Could not write diagnostic overlay")
        if profile is None:
            warnings.append("No lens profile: uncorrected lens distortion may affect dimensions.")
        try:
            result = _measure_calibrated(image, gray, corners, "free", profile is not None, quality_only, reference_data=(inverse, warnings, pattern_mask))
        except ScanError as exc:
            exc.overlay = overlay
            raise
        if not quality_only:
            result.reference = reference
            result.estimated_accuracy_class = get_reference(reference).accuracy
            result.warnings = warnings
            result.residual_mm = result.residual_px = None
            result.markers = {}
        return result
    params = cv.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv.aruco.CORNER_REFINE_APRILTAG
    corners, ids, _ = cv.aruco.ArucoDetector(dictionary(), params).detectMarkers(gray)
    found = {} if ids is None else {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners)}
    if len(found) < 4 or (layout == "fixed" and not all(i in found for i in range(4))):
        raise ScanError("Need at least four ArUco markers, fully visible (fixed: IDs 0–3)")
    if len(found) != len(ids):
        raise ScanError("Duplicate marker IDs")
    selected = sorted(found) if layout == "free" else list(range(4))
    if saved is not None and any(i not in saved for i in selected):
        raise ScanError("Detected marker missing from saved layout")
    photo = np.concatenate([found[i] for i in selected]).astype(np.float32)
    photo = cv.cornerSubPix(gray, photo.reshape(-1, 1, 2), (5, 5), (-1, -1),
                            (cv.TERM_CRITERIA_EPS | cv.TERM_CRITERIA_COUNT, 40, .001)).reshape(-1, 2).astype(float)
    try:
        known = np.concatenate([saved[i] for i in selected]) if saved is not None else None
        result = _measure_calibrated(image, gray, photo, layout, profile is not None, quality_only,
                                     [sizes.get(i, 90.) for i in selected] if sizes else None, known)
        if not quality_only:
            result.markers = dict(zip(selected, result.markers.reshape(-1, 4, 2)))
            if save_layout:
                from .layout import save_layout as write_layout
                write_layout(save_layout, result.markers)
        return result
    except (np.linalg.LinAlgError, FloatingPointError) as exc:
        raise ScanError("Degenerate marker layout") from exc


def _measure_calibrated(image, gray, photo, layout, calibrated=False, quality_only=False, sizes=None, known=None, reference_data=None):
    if reference_data is not None:
        inverse, warnings, pattern_mask = reference_data
        homography = np.linalg.inv(inverse)
        world = project(photo, inverse)
        residual_mm = 0.
    elif layout == "free" and known is None:
        inverse, world, residual_mm = free_homography(photo) if sizes is None else free_homography(photo, sizes)
        homography = np.linalg.inv(inverse)
    else:
        world = known if known is not None else np.concatenate([corners_mm(i) for i in range(4)])
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
    if reference_data is not None:
        h, w = gray.shape
        roi = np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]])
    # A projective horizon must not cross the working hull. Check before
    # perspectiveTransform (which maps zero denominators to zero).
    denominators = np.c_[roi, np.ones(len(roi))] @ inverse[2]
    if (not np.isfinite(denominators).all() or
            np.min(np.abs(denominators)) < 1e-8 * np.max(np.abs(denominators)) or
            np.any(denominators > 0) != np.all(denominators > 0)):
        raise ScanError("Degenerate homography across working area")
    metric_roi = project(roi, inverse)
    hull_area = cv.contourArea(roi.astype(np.float32))
    metric_area = cv.contourArea(metric_roi.astype(np.float32))
    if (not np.isfinite(metric_roi).all() or not 1000 <= metric_area <= 100_000_000
            or np.max(np.ptp(metric_roi, axis=0)) > 100_000 or hull_area < 100):
        raise ScanError("Extreme working hull geometry; reposition markers and retake photo")
    from .quality import checks
    quality = checks(gray, photo, homography, residual_mm, calibrated) if reference_data is None else [dict(name="reference", status="warn", value=None, message=w) for w in warnings]
    if quality_only:
        return quality
    rejected = [row for row in quality if row["status"] == "reject"]
    if rejected:
        error = ScanError("Photo quality: " + "; ".join(row["name"] + ": " + row["message"] for row in rejected))
        error.checks = quality
        raise error
    a = np.maximum(np.floor(roi.min(0)-3).astype(int), 0)
    b = np.minimum(np.ceil(roi.max(0)+3).astype(int), [gray.shape[1]-1, gray.shape[0]-1])
    crop = gray[a[1]:b[1]+1, a[0]:b[0]+1]
    valid = np.zeros(crop.shape, np.uint8)
    cv.fillConvexPoly(valid, np.rint(roi-a).astype(np.int32), 255)
    if layout == "free":
        for marker in photo.reshape(-1, 4, 2):
            expanded = marker.mean(0) + (marker-marker.mean(0))*(1.03 if reference_data is not None else 1.25)
            cv.fillConvexPoly(valid, np.rint(expanded-a).astype(np.int32), 0)
    if reference_data is not None:
        mask = pattern_mask[a[1]:b[1]+1, a[0]:b[0]+1].copy()
        from .references import foreground
        _, colour_distance = foreground(image, with_distance=True, segment=False)
        threshold = float(np.median(colour_distance[pattern_mask>0]))/2
        if np.std(np.concatenate((gray[0],gray[-1],gray[:,0],gray[:,-1]))) > 10:
            colour_distance = pattern_mask
            threshold = 127.5
    else:
        values = crop[valid > 0]
        otsu, _ = cv.threshold(values, 0, 255, cv.THRESH_BINARY+cv.THRESH_OTSU)
        dark, light = values[values <= otsu], values[values > otsu]
        if not len(dark) or not len(light) or np.median(light)-np.median(dark) < 30:
            raise ScanError("Need light paper on a contrasting dark table")
        threshold = (float(np.median(dark))+float(np.median(light)))/2
        mask = ((crop > threshold) & (valid > 0)).astype(np.uint8)*255
    contours, hierarchy = cv.findContours(mask, cv.RETR_TREE, cv.CHAIN_APPROX_NONE)
    if not contours:
        raise ScanError("No paper outline found")
    clearance = cv.distanceTransform(valid, cv.DIST_L2, 3)
    candidates = []
    touching = False
    for index, contour in enumerate(contours):
        if hierarchy[0, index, 3] != -1:
            continue
        area = cv.contourArea(contour)
        # Broad bounds allow concave sewing patterns; no rectangle assumption.
        if not .005 <= area / hull_area <= .85:
            continue
        convex_area = cv.contourArea(cv.convexHull(contour))
        if convex_area <= 0 or area / convex_area < .35:
            raise ScanError("Ambiguous paper outline; separate pieces and remove light objects")
        outline = contour.reshape(-1, 2)
        if np.min(clearance[outline[:, 1], outline[:, 0]]) < 2:
            touching = True
            continue
        candidates.append(index)
    if touching:
        raise ScanError("Paper touches marker cards or working area edge; surround the whole pattern with a gap")
    if not candidates:
        raise ScanError("No plausible paper outline found")
    channel = colour_distance.astype(np.float32) if reference_data is not None else gray.astype(np.float32)

    def refine(index):
        pixels = contours[index].reshape(-1, 2).astype(float)+a
        pixels = cruzamento(channel, pixels, threshold)
        ring = project(pixels, inverse)
        if len(ring) < 20 or not np.isfinite(ring).all():
            raise ScanError("Invalid or ambiguous outline")
        return ring

    pieces = []
    for index in candidates:
        from .annotations import notches, grain
        outer, notch_marks = notches(refine(index))
        grain_marks = []
        if layout == "fixed" and min((outer[:,0]-x0).min(), (x1-outer[:,0]).min(),
                                      (outer[:,1]-y0).min(), (y1-outer[:,1]).min()) < 3:
            raise ScanError("Paper touches working area edge; leave at least 3 mm clearance")
        holes = []
        child = hierarchy[0, index, 2]
        while child != -1:
            if hierarchy[0, child, 2] != -1:
                raise ScanError("Ambiguous nested outlines; remove light objects from holes")
            ring = refine(child)
            (grain_marks if grain(ring) else holes).append(ring)
            child = hierarchy[0, child, 0]
        piece = Piece(outer, holes, notch_marks, grain_marks)
        if not piece.polygon.is_valid or piece.polygon.area < 100:
            raise ScanError("Invalid or too small paper outline")
        pieces.append(piece)
    pieces.sort(key=lambda p: tuple(p.contour_mm.min(0)))
    for i, piece in enumerate(pieces):
        if any(piece.polygon.distance(other.polygon) < 3 for other in pieces[:i]):
            raise ScanError("Ambiguous nearby outlines; separate pieces by at least 3 mm")
    mm = np.concatenate([p.contour_mm for p in pieces])
    rings = [ring for p in pieces for ring in [p.contour_mm, *p.holes_mm]]
    if layout == "free":
        origin = mm.min(0)-20
        span = mm.max(0)-origin+20
        scale = min(1., 4000. / np.max(span))
        extent = np.clip(np.ceil(span * scale).astype(int), 1, 4000)
        shift = np.array([[scale,0.,-origin[0]*scale],[0.,scale,-origin[1]*scale],[0.,0.,1.]])
        preview = cv.warpPerspective(image, shift @ inverse, tuple(extent))
        cv.polylines(preview, [np.rint((ring-origin)*scale).astype(np.int32) for ring in rings], True, (0,0,255), 2)
        width, height = sorted(cv.minAreaRect(mm.astype(np.float32))[1])
    else:
        preview = cv.warpPerspective(image, inverse, (WIDTH_MM, HEIGHT_MM))
        cv.polylines(preview, [np.rint(ring).astype(np.int32) for ring in rings], True, (0,0,255), 2)
        width, height = np.ptp(mm, axis=0)
    return Measurement(pieces[0].contour_mm, float(sum(p.polygon.area for p in pieces)), float(width), float(height), residual, residual_mm, preview, quality, pieces, world)
