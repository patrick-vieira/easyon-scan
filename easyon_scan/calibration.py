"""Metric planar calibration with unknown rigid marker poses."""
import cv2 as cv
import numpy as np

SQUARE = np.array([[0., 0.], [90., 0.], [90., 90.], [0., 90.]])


def project(points, h):
    return cv.perspectiveTransform(np.asarray(points, float)[None], h)[0]


def free_homography(photo):
    """Fix the first marker as the coordinate gauge; jointly fit all corners.

    Five damped Gauss–Newton iterations optimize image→plane homography and
    the other markers' rigid poses. Residuals and damping are in millimetres.
    """
    groups = photo.reshape(-1, 4, 2)
    h = cv.getPerspectiveTransform(groups[0].astype(np.float32), SQUARE.astype(np.float32))
    poses = []
    centered = SQUARE - SQUARE.mean(0)
    for points in groups[1:]:
        mapped = project(points, h)
        u, _, vt = np.linalg.svd(centered.T @ (mapped - mapped.mean(0)))
        rotation = u @ vt
        if np.linalg.det(rotation) < 0:
            u[:, -1] *= -1
            rotation = u @ vt
        angle = np.arctan2(rotation[0, 1], rotation[0, 0])
        poses.extend([angle, *(mapped.mean(0) - SQUARE.mean(0) @ rotation)])
    state = np.r_[h.ravel()[:8] / h[2, 2], poses]

    def unpack(p):
        matrix = np.r_[p[:8], 1.].reshape(3, 3)
        world = [SQUARE]
        for angle, x, y in p[8:].reshape(-1, 3):
            c, s = np.cos(angle), np.sin(angle)
            world.append(SQUARE @ np.array([[c, s], [-s, c]]) + [x, y])
        return matrix, np.concatenate(world)

    def residual(p):
        matrix, world = unpack(p)
        return (project(photo, matrix) - world).ravel()

    for _ in range(5):
        r = residual(state)
        steps = 1e-6 * np.maximum(1., np.abs(state))
        steps[6:8] = 1e-9
        jac = np.column_stack([(residual(state + np.eye(1, len(state), i)[0]*step)-r)/step
                               for i, step in enumerate(steps)])
        scale = np.maximum(np.linalg.norm(jac, axis=0), 1e-12)
        j = jac / scale
        for damping in (1e-8, 1e-5, .001, .1, 10):
            delta = np.linalg.solve(j.T @ j + damping*np.eye(len(state)), -j.T @ r) / scale
            if np.linalg.norm(residual(state + delta)) < np.linalg.norm(r):
                state += delta
                break
    inverse, world = unpack(state)
    residual_mm = float(np.sqrt(np.mean(np.sum((project(photo, inverse)-world)**2, axis=1))))
    return inverse, world, residual_mm
