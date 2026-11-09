"""Synthetic segment-pair evaluation with a tolerance declared by the caller."""
import math
from statistics import median


def _same(candidate, truth, tolerance):
    a, b = candidate['a'], candidate['b']
    x, y = truth['a'], truth['b']
    for first, second in ((a, b), (b, a)):
        if (first['edge_id'] == x['edge_id'] and second['edge_id'] == y['edge_id']
                and max(abs(first[k]-x[k]) for k in ('start_mm', 'end_mm')) <= tolerance
                and max(abs(second[k]-y[k]) for k in ('start_mm', 'end_mm')) <= tolerance):
            return True
    return False


def evaluate_proposals(proposals, truth, *, endpoint_tolerance_mm, review_seconds=()):
    """Rank per source segment; report top-1 precision and top-3 recall denominators."""
    if not math.isfinite(endpoint_tolerance_mm) or endpoint_tolerance_mm < 0:
        raise ValueError('Declare nonnegative endpoint_tolerance_mm before evaluation')
    if any(t < 0 or not math.isfinite(t) for t in review_seconds):
        raise ValueError('Review times must be finite and nonnegative')
    ranked = sorted(proposals, key=lambda p: (-p['score'], p['id']))
    groups = {}
    for p in ranked:
        groups.setdefault((p['a']['edge_id'], p['a']['start_mm'], p['a']['end_mm']), []).append(p)
    top1 = [items[0] for items in groups.values()]
    top3 = [p for items in groups.values() for p in items[:3]]
    def count(candidates):
        remaining = list(truth)
        correct = []
        orientation_errors = construction_errors = 0
        for p in candidates:
            hit = next((i for i, t in enumerate(remaining) if _same(p, t, endpoint_tolerance_mm)), None)
            if hit is None:
                continue
            t = remaining.pop(hit)
            if p['orientation'] != t['orientation']:
                orientation_errors += 1
            elif p['construction'] != t['construction']:
                construction_errors += 1
            else:
                correct.append(p)
        return len(correct), orientation_errors, construction_errors
    tp1, o1, c1 = count(top1)
    tp3, o3, c3 = count(top3)
    times = sorted(review_seconds)
    p95 = times[math.ceil(.95 * len(times))-1] if times else None
    return {'endpoint_tolerance_mm': endpoint_tolerance_mm,
            'top1': {'tp': tp1, 'fp': len(top1)-tp1, 'predicted': len(top1),
                     'precision': tp1/len(top1) if top1 else None,
                     'orientation_errors': o1, 'construction_errors': c1},
            'top3': {'tp': tp3, 'fn': len(truth)-tp3, 'truth': len(truth),
                     'recall': tp3/len(truth) if truth else None,
                     'orientation_errors': o3, 'construction_errors': c3},
            'review_seconds': {'count': len(times), 'median': median(times) if times else None, 'p95': p95}}
