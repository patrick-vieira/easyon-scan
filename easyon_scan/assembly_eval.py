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


def ranked_by_edge(proposals):
    """Rank every candidate at both participating edges with a stable tie break."""
    groups = {}
    for candidate in sorted(proposals, key=lambda p: (-p['score'], p['id'])):
        for ref in (candidate['a'], candidate['b']):
            key = (ref['edge_id'], ref['start_mm'], ref['end_mm'])
            groups.setdefault(key, []).append(candidate)
    by_edge = {}
    for (edge_id, _, _), items in groups.items():
        by_edge.setdefault(edge_id, []).extend(items)
    for edge_id, items in by_edge.items():
        by_edge[edge_id] = sorted({item['id']: item for item in items}.values(),
                                  key=lambda p: (-p['score'], p['id']))
    return by_edge


def _matched_count(candidates, truth, tolerance, level):
    """Maximum one-to-one matches at the requested correctness level."""
    owners = {}

    def assign(target_index, seen):
        target = truth[target_index]
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in seen or not _same(candidate, target, tolerance):
                continue
            if level >= 2 and candidate['orientation'] != target['orientation']:
                continue
            if level >= 3 and candidate['construction'] != target['construction']:
                continue
            seen.add(candidate_index)
            if candidate_index not in owners or assign(owners[candidate_index], seen):
                owners[candidate_index] = target_index
                return True
        return False

    for target_index in range(len(truth)):
        assign(target_index, set())
    return len(owners)


def evaluate_proposals(proposals, truth, *, endpoint_tolerance_mm, review_seconds=()):
    """Report geometric ranking separately from orientation and construction."""
    if not math.isfinite(endpoint_tolerance_mm) or endpoint_tolerance_mm < 0:
        raise ValueError('Declare nonnegative endpoint_tolerance_mm before evaluation')
    if any(t < 0 or not math.isfinite(t) for t in review_seconds):
        raise ValueError('Review times must be finite and nonnegative')
    groups = ranked_by_edge(proposals)
    def evaluate(limit):
        selected = {p['id']: p for items in groups.values() for p in items[:limit]}
        candidates = list(selected.values())
        geometry, orientation, construction = (
            _matched_count(candidates, truth, endpoint_tolerance_mm, level)
            for level in (1, 2, 3)
        )
        orientation_errors = geometry - orientation
        construction_errors = orientation - construction
        return candidates, geometry, orientation, construction, orientation_errors, construction_errors
    one = evaluate(1)
    three = evaluate(3)
    def metrics(result, top):
        candidates, geometry, orientation, construction, orientation_errors, construction_errors = result
        value = {'tp': construction, 'geometry_tp': geometry, 'orientation_tp': orientation,
                 'construction_tp': construction, 'orientation_errors': orientation_errors,
                 'construction_errors': construction_errors}
        if top == 1:
            value.update(fp=len(candidates)-construction, predicted=len(candidates),
                         precision=construction/len(candidates) if candidates else None,
                         geometry_precision=geometry/len(candidates) if candidates else None)
        else:
            value.update(fn=len(truth)-construction, truth=len(truth),
                         recall=construction/len(truth) if truth else None,
                         geometry_recall=geometry/len(truth) if truth else None)
        return value
    times = sorted(review_seconds)
    p95 = times[math.ceil(.95 * len(times))-1] if times else None
    return {'endpoint_tolerance_mm': endpoint_tolerance_mm,
            'rank_rule': 'score_desc_then_id_asc; selected when in top N at either edge',
            'score_ties': {edge: [{'score': score, 'ids': [p['id'] for p in items if p['score'] == score]}
                                  for score in sorted({p['score'] for p in items}, reverse=True)
                                  if sum(p['score'] == score for p in items) > 1]
                           for edge, items in groups.items()
                           if len({p['score'] for p in items}) < len(items)},
            'top1': metrics(one, 1), 'top3': metrics(three, 3),
            'review_seconds': {'count': len(times), 'median': median(times) if times else None, 'p95': p95}}
