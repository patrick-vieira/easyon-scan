"""Conservative metric recognition of the documented ink convention."""
import cv2 as cv
import numpy as np


def notches(ring):
    # Simplification removes pixel staircases before looking for a small V.
    points = cv.approxPolyDP(ring.astype(np.float32), .45, True).reshape(-1, 2)
    polygon_sign = np.sign(cv.contourArea(points, oriented=True))
    remove, marks = set(), []
    for i, tip in enumerate(points):
        a, b = points[i-1], points[(i+1) % len(points)]
        base = np.linalg.norm(b-a)
        if not 4 <= base <= 12:
            continue
        cross = float(np.linalg.det(np.array([tip-a, b-tip])))
        depth = abs(cross)/base
        if cross*polygon_sign >= 0 or not 3 <= depth <= 10:
            continue
        t = np.dot(tip-a, b-a)/base**2
        before, after = points[i-2], points[(i+2) % len(points)]
        # The base must continue a straight paper edge on both sides.
        distance = lambda p: abs(float(np.linalg.det(np.array([b-a, p-a]))))/base
        if .25 <= t <= .75 and distance(before)/max(np.linalg.norm(before-a), 1) < .06 and distance(after)/max(np.linalg.norm(after-b), 1) < .06:
            remove.add(i)
            marks.append(np.array([a, tip, b], float))
    return (np.delete(points, sorted(remove), axis=0).astype(float) if marks else ring), marks


def grain(ring):
    center = ring.mean(0)
    _, _, axes = np.linalg.svd(ring-center, full_matrices=False)
    xy = (ring-center) @ axes.T
    length, width = np.ptp(xy, axis=0)
    if not 40 <= length <= 400 or not 6 <= width <= 16 or length/width < 5:
        return False
    # Filled double arrow: thin shaft and symmetric wide heads at BOTH ends.
    lo, hi = xy[:, 0].min(), xy[:, 0].max()
    middle = xy[(xy[:, 0] > lo+length*.2) & (xy[:, 0] < hi-length*.2), 1]
    if len(middle) < 2 or not .6 <= np.ptp(middle) <= 3:
        return False
    for selected in (xy[:, 0] < lo+12, xy[:, 0] > hi-12):
        if np.ptp(xy[selected, 1]) < width*.8:
            return False
    return True
