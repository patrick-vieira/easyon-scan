"""Metric planar calibration with unknown rigid marker poses."""
import cv2 as cv
import numpy as np

SQUARE = np.array([[0., 0.], [90., 0.], [90., 90.], [0., 90.]])


def project(points, h):
    return cv.perspectiveTransform(np.asarray(points, float)[None], h)[0]


def free_homography(photo, sizes=None):
    """Fix the first marker as the coordinate gauge; jointly fit all corners.

    Gauss–Newton iterations optimize image→plane homography and the other
    markers' rigid poses. Residuals are in millimetres.
    """
    groups = photo.reshape(-1, 4, 2)
    from .layout import square
    squares = [square(s) for s in (sizes if sizes is not None else [90.] * len(groups))]
    first = squares[0]
    h = cv.getPerspectiveTransform(groups[0].astype(np.float32), first.astype(np.float32))
    poses = []
    for points, local in zip(groups[1:], squares[1:]):
        centered = local - local.mean(0)
        mapped = project(points, h)
        u, _, vt = np.linalg.svd(centered.T @ (mapped - mapped.mean(0)))
        rotation = u @ vt
        if np.linalg.det(rotation) < 0:
            u[:, -1] *= -1
            rotation = u @ vt
        angle = np.arctan2(rotation[0, 1], rotation[0, 0])
        poses.extend([angle, *(mapped.mean(0) - local.mean(0) @ rotation)])
    state = np.r_[h.ravel()[:8] / h[2, 2], poses]

    def unpack(p):
        matrix = np.r_[p[:8], 1.].reshape(3, 3)
        world = [first]
        for local, (angle, x, y) in zip(squares[1:], p[8:].reshape(-1, 3)):
            c, s = np.cos(angle), np.sin(angle)
            world.append(local @ np.array([[c, s], [-s, c]]) + [x, y])
        return matrix, np.concatenate(world)

    def residual(p):
        matrix, world = unpack(p)
        return (project(photo, matrix) - world).ravel()

    for _ in range(60):
        r = residual(state)
        old_norm = np.linalg.norm(r)
        steps = 1e-6 * np.maximum(1., np.abs(state))
        steps[6:8] = 1e-9
        jac = np.column_stack([(residual(state + np.eye(1, len(state), i)[0]*step)-r)/step
                               for i, step in enumerate(steps)])
        scale = np.maximum(np.linalg.norm(jac, axis=0), 1e-12)
        j = jac / scale
        accepted = False
        # Solve the Jacobian directly: normal equations square its condition
        # number, and even tiny fixed damping can stall a well-posed fit.
        singular = np.linalg.svd(j, compute_uv=False)
        dampings = (0., 1e-8, 1e-5, .001, .1, 10)
        if singular[-1] <= singular[0] * 1e-12:
            dampings = dampings[1:]
        for damping in dampings:
            if damping:
                system = np.vstack((j, np.sqrt(damping) * np.eye(len(state))))
                target = np.r_[-r, np.zeros(len(state))]
            else:
                system, target = j, -r
            delta = np.linalg.lstsq(system, target, rcond=None)[0] / scale
            candidate_norm = np.linalg.norm(residual(state + delta))
            if np.isfinite(candidate_norm) and candidate_norm < old_norm:
                old_world = unpack(state)[1]
                state += delta
                accepted = True
                break
        if not accepted:
            break
        displacement = np.max(np.linalg.norm(unpack(state)[1] - old_world, axis=1))
        if (old_norm - candidate_norm < 1e-10 * max(1., old_norm)
                and displacement < 1e-5):
            break
    inverse, world = unpack(state)
    residual_mm = float(np.sqrt(np.mean(np.sum((project(photo, inverse)-world)**2, axis=1))))
    return inverse, world, residual_mm
