"""Versioned metric marker layouts and per-ID black-square sizes."""
import json
from pathlib import Path
import numpy as np


def marker_sizes(value=None):
    if isinstance(value, str):
        try:
            value = dict(part.split('=') for part in value.split(','))
        except ValueError as exc:
            raise ValueError('Marker sizes must be ID=mm,ID=mm') from exc
    result = {int(k): float(v) for k, v in (value or {}).items()}
    if any(not 0 <= k < 50 or not np.isfinite(v) or v <= 0 for k, v in result.items()):
        raise ValueError('Marker IDs must be 0–49 and sizes finite positive millimetres')
    return result


def square(size):
    return np.array([[0., 0.], [size, 0.], [size, size], [0., size]])


def save_layout(path, markers):
    Path(path).write_text(json.dumps({'version': 1, 'units': 'mm', 'markers':
                                    {str(k): np.asarray(v).tolist() for k, v in markers.items()}}, indent=2))


def load_layout(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('units') != 'mm':
        raise ValueError('Unsupported layout version or units')
    result = {}
    if not isinstance(data.get('markers'), dict):
        raise ValueError('Layout must contain a markers object')
    for key, points in data['markers'].items():
        k = int(key)
        p = np.asarray(points, float)
        if not 0 <= k < 50 or p.shape != (4, 2) or not np.isfinite(p).all():
            raise ValueError('Invalid layout marker')
        edges = np.roll(p, -1, axis=0)-p
        size = np.linalg.norm(edges, axis=1)
        if size.min() <= 0 or not np.allclose(size, size[0], rtol=1e-4) or not np.allclose(np.sum(edges*np.roll(edges,-1,axis=0),axis=1), 0, atol=1e-4*size[0]**2) or (edges[0,0]*edges[1,1]-edges[0,1]*edges[1,0]) <= 0:
            raise ValueError('Layout markers must be ordered square corners')
        result[k] = p
    if not result:
        raise ValueError('Empty layout')
    return result
