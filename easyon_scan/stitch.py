"""Shared-marker registration and metric mosaic contour merging."""
import cv2 as cv
import numpy as np
from .calibration import project, free_homography
from .markers import dictionary
from .layout import marker_sizes, load_layout, save_layout as write_layout, square
from .scan import load_photo, ScanError, _measure_calibrated


def stitch(sources, *, marker_size=None, layout_file=None, save_layout=None, profile=None):
    if len(sources) < 2:
        raise ScanError('Stitching requires at least two photos')
    sizes = marker_sizes(marker_size)
    images, observations = [], []
    for source in sources:
        image = source if isinstance(source, np.ndarray) else load_photo(source)
        if profile is not None:
            from .lens import undistort
            image = undistort(image, profile)
        gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
        params = cv.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv.aruco.CORNER_REFINE_APRILTAG
        corners, ids, _ = cv.aruco.ArucoDetector(dictionary(), params).detectMarkers(gray)
        if ids is None or len(ids) < 2 or len(set(ids.ravel())) != len(ids):
            raise ScanError('Each photo needs at least two distinct fully visible markers')
        found = {}
        for ident, corners_i in zip(ids.ravel(), corners):
            points = cv.cornerSubPix(gray, corners_i.reshape(-1,1,2), (5,5), (-1,-1),
                                    (cv.TERM_CRITERIA_EPS | cv.TERM_CRITERIA_COUNT,40,.001))
            found[int(ident)] = points.reshape(4,2).astype(float)
        images.append(image)
        observations.append(found)
    edges = [{j for j, other in enumerate(observations) if j != i and len(set(obs) & set(other)) >= 2}
             for i, obs in enumerate(observations)]
    reached = {0}
    while True:
        extended = reached | set().union(*(edges[i] for i in reached))
        if extended == reached:
            break
        reached = extended
    if len(reached) != len(images):
        raise ScanError('Photo overlap graph must be connected with at least two shared markers per link')
    known = load_layout(layout_file) if layout_file else None
    detected = set().union(*(set(obs) for obs in observations))
    if known is not None and not detected <= set(known):
        raise ScanError('Detected marker missing from saved layout')
    world = {} if known is None else {k: known[k] for k in detected}
    if known is not None:
        for k, size in sizes.items():
            if k in world and not np.isclose(np.linalg.norm(world[k][1]-world[k][0]), size):
                raise ValueError('Declared marker size conflicts with saved layout')
    transforms = {}
    if known is None:
        ids = sorted(observations[0])
        h, points, _ = free_homography(np.concatenate([observations[0][i] for i in ids]), [sizes.get(i,90.) for i in ids])
        world.update(zip(ids, points.reshape(-1,4,2)))
        transforms[0] = h
    pending = set(range(len(images)))
    while pending:
        progress = False
        for index in sorted(pending):
            obs = observations[index]
            shared = sorted(set(obs) & set(world))
            if len(shared) < 2:
                continue
            h, _ = cv.findHomography(np.concatenate([obs[k] for k in shared]), np.concatenate([world[k] for k in shared]), 0)
            if h is None:
                raise ScanError('Degenerate shared markers')
            transforms[index] = h
            for k in set(obs)-set(world):
                world[k] = project(obs[k], h)
            pending.remove(index)
            progress = True
        if not progress:
            raise ScanError('Saved layout lacks shared markers')
    anchor = min(observations[0])
    # Block coordinate least squares: all photo homographies and rigid marker poses
    # participate in the same corner objective; one marker fixes the plane gauge.
    for _ in range(100):
        for index, obs in enumerate(observations):
            ids = sorted(obs)
            transforms[index], _ = cv.findHomography(np.concatenate([obs[k] for k in ids]), np.concatenate([world[k] for k in ids]), 0)
        if known is not None:
            break
        for k in world:
            if k == anchor:
                continue
            mapped = np.mean([project(obs[k], transforms[j]) for j, obs in enumerate(observations) if k in obs], axis=0)
            local = square(sizes.get(k,90.))
            u, _, vt = np.linalg.svd((local-local.mean(0)).T @ (mapped-mapped.mean(0)))
            rotation = u @ np.diag([1., np.linalg.det(u @ vt)]) @ vt
            world[k] = (local-local.mean(0)) @ rotation + mapped.mean(0)
    errors = []
    quality = []
    from .quality import checks
    for j, obs in enumerate(observations):
        ids = sorted(obs)
        photo = np.concatenate([obs[k] for k in ids])
        target = np.concatenate([world[k] for k in ids])
        error = np.sum((project(photo, transforms[j])-target)**2, axis=1)
        errors.extend(error)
        rows = checks(cv.cvtColor(images[j],cv.COLOR_BGR2GRAY), photo, np.linalg.inv(transforms[j]), float(np.sqrt(np.mean(error))), profile is not None)
        if any(row['status'] == 'reject' for row in rows):
            error = ScanError(f'Photo {j+1} failed quality checks')
            error.checks = [dict(row, photo=j+1) for row in rows]
            raise error
        quality.extend(dict(row, photo=j+1) for row in rows)
    residual = float(np.sqrt(np.mean(errors)))
    quality.append(dict(name='joint_marker_consistency_mm', value=residual, status='reject' if residual > 2 else ('warn' if residual > 1 else 'pass'), message='Joint corner RMS across all photos; verify stationary coplanar markers.'))
    if residual > 2:
        error = ScanError('Joint marker residual exceeds 2 mm')
        error.checks = quality
        raise error
    all_world = np.concatenate(list(world.values()))
    origin = all_world.min(0)-20
    span = all_world.max(0)-origin+20
    if np.max(span) > 10000 or np.prod(span) > 40_000_000:
        raise ScanError('Stitched working area too large')
    shift = np.array([[1.,0.,-origin[0]],[0.,1.,-origin[1]],[0.,0.,1.]])
    extent = tuple(np.ceil(span).astype(int))
    total = np.zeros((extent[1],extent[0],3),np.float32)
    weight = np.zeros((extent[1],extent[0]),np.float32)
    for j, image in enumerate(images):
        matrix = shift @ transforms[j]
        mask = cv.warpPerspective(np.ones(image.shape[:2],np.float32), matrix, extent)
        # Light-paper union preserves contours cut by individual photo borders.
        total = np.maximum(total, cv.warpPerspective(image.astype(np.float32), matrix, extent))
        weight += mask
    covered_hull = np.zeros(weight.shape, np.uint8)
    cv.fillConvexPoly(covered_hull, cv.convexHull((all_world-origin).astype(np.float32)).astype(np.int32), 1)
    covered_hull = cv.erode(covered_hull, np.ones((5,5),np.uint8))
    if np.any((covered_hull > 0) & (weight < .99)):
        raise ScanError('Photos leave gaps inside the marker hull; add overlapping photos')
    mosaic = np.clip(total,0,255).astype(np.uint8)
    ids = sorted(world)
    target = np.concatenate([world[k] for k in ids])
    photo = target-origin
    result = _measure_calibrated(mosaic, cv.cvtColor(mosaic,cv.COLOR_BGR2GRAY), photo, 'free', profile is not None, known=target)
    result.residual_mm = residual
    result.residual_px = float(np.sqrt(np.mean([np.mean(np.sum((project(world[k], np.linalg.inv(transforms[j]))-obs[k])**2,axis=1)) for j,obs in enumerate(observations) for k in obs])))
    result.quality_checks = quality
    result.markers = world
    if save_layout:
        write_layout(save_layout, world)
    return result
