"""Synthetic segment-pair evaluation with a tolerance declared by the caller."""
import math
from statistics import median


def _aligned(candidate, truth, tolerance):
    """Return candidate matches in truth A/B order, or None for different segments."""
    for swapped in (False, True):
        first, second = (candidate['b'], candidate['a']) if swapped else (candidate['a'], candidate['b'])
        if all(ref['edge_id'] == target['edge_id'] and
               all(abs(ref[k] - target[k]) <= tolerance for k in ('start_mm', 'end_mm'))
               for ref, target in ((first, truth['a']), (second, truth['b']))):
            matches = candidate.get('matches', [])
            return sorted(({'a_mm': m['b_mm'], 'b_mm': m['a_mm']} if swapped else m
                           for m in matches), key=lambda m: m['a_mm'])
    return None


def _matches(candidate, truth, tolerance):
    matches = _aligned(candidate, truth, tolerance)
    expected = sorted(truth.get('matches', []), key=lambda m: m['a_mm'])
    return (matches is not None and len(matches) == len(expected) and
            all(abs(a[k] - b[k]) <= tolerance for a, b in zip(matches, expected)
                for k in ('a_mm', 'b_mm')))


def _same(candidate, truth, tolerance):
    return _aligned(candidate, truth, tolerance) is not None


def ranked_by_edge(proposals):
    """Rank candidates at each canonical (edge, start, end) segment key."""
    groups = {}
    for candidate in sorted(proposals, key=lambda p: (-p['score'], p['id'])):
        keys = {(ref['edge_id'], ref['start_mm'], ref['end_mm'])
                for ref in (candidate['a'], candidate['b'])}
        for key in sorted(keys):
            groups.setdefault(key, []).append(candidate)
    return groups


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
            if level >= 3 and not _matches(candidate, target, tolerance):
                continue
            if level >= 4 and candidate['construction'] != target['construction']:
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
        geometry, orientation, matches, construction = (
            _matched_count(candidates, truth, endpoint_tolerance_mm, level)
            for level in (1, 2, 3, 4)
        )
        orientation_errors = geometry - orientation
        construction_errors = matches - construction
        return candidates, geometry, orientation, matches, construction, orientation_errors, construction_errors
    one = evaluate(1)
    three = evaluate(3)
    def metrics(result, top):
        candidates, geometry, orientation, matches, construction, orientation_errors, construction_errors = result
        value = {'tp': construction, 'geometry_tp': geometry, 'orientation_tp': orientation,
                 'matches_tp': matches, 'matches_errors': orientation - matches, 'construction_tp': construction, 'orientation_errors': orientation_errors,
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
            'rank_rule': 'score_desc_then_id_asc; selected when in top N at either canonical segment',
            'denominator_rule': 'top1: unique selected candidates; top3: truth pairs; one-to-one matching',
            'score_ties': {str(edge): [{'score': score, 'ids': [p['id'] for p in items if p['score'] == score]}
                                  for score in sorted({p['score'] for p in items}, reverse=True)
                                  if sum(p['score'] == score for p in items) > 1]
                           for edge, items in groups.items()
                           if len({p['score'] for p in items}) < len(items)},
            'top1': metrics(one, 1), 'top3': metrics(three, 3),
            'review_seconds': {'count': len(times), 'median': median(times) if times else None, 'p95': p95}}
