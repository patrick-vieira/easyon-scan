"""Deterministic, review-only seam candidates for garment-assembly/v0."""
import copy
import math
import re

from shapely.geometry import LineString

from .assembly import validate_assembly

MODEL_VERSION = 'rules-v0.1'
WORDS = {
    'front': ('frente', 'front'), 'back': ('costas', 'back'),
    'side': ('lateral', 'side'), 'shoulder': ('ombro', 'shoulder'),
    'armhole': ('cava', 'armhole'), 'sleeve': ('manga', 'sleeve'),
    'inseam': ('entrepernas', 'inseam'), 'crotch': ('gancho', 'crotch'),
    'neckline': ('decote', 'neckline'),
}
FREE = {'hem', 'bainha', 'fold', 'dobra', 'opening', 'abertura'}


def _tokens(value):
    return set(re.findall(r'[a-zà-ÿ]+', (value or '').lower()))


def _roles(value):
    words = _tokens(value)
    return {role for role, names in WORDS.items() if words.intersection(names)}


def _notches(edge, reverse=False):
    length = edge['length_mm']
    result = sorted(n['at_mm'] / length for n in edge['notches']) if length > 0 else []
    return sorted(1 - x for x in result) if reverse else result


def _sew_length(panel, edge):
    if panel['sewline_status'] != 'from_dxf':
        return None
    cut = LineString(panel['cutline'])
    sew = LineString(panel['sewline'])
    start = cut.interpolate(edge['from_mm'])
    end = cut.interpolate(edge['to_mm'])
    a, b = sew.project(start), sew.project(end)
    distance = abs(b - a)
    return distance if distance > 0 else None


def propose_assembly(doc):
    """Return v0 plus lateral proposals; scores are review priority, not probability."""
    if doc.get('schema') != 'garment-assembly/v0' or 'proposals' in doc:
        raise ValueError('Expected an unmodified garment-assembly/v0 document')
    base = copy.deepcopy(doc)
    base.pop('rejected_pairs', None)
    for panel in base['panels']:
        for edge in panel['edges']:
            edge.pop('label', None)
    issues = validate_assembly(base)
    if any(i['level'] == 'error' for i in issues):
        raise ValueError('Invalid assembly: ' + ', '.join(sorted({i['code'] for i in issues if i['level'] == 'error'})))
    source = doc['source']
    if source['units'] == 'unitless' or source.get('assumed_units'):
        raise ValueError('Uncertain units require human confirmation')
    panels = doc['panels']
    used = {end for seam in doc['seams'] for end in (seam['a'], seam['b'])}
    rejected = {tuple(sorted((x['a'], x['b']))) for x in doc.get('rejected_pairs', [])}
    entries = [(p, e) for p in panels for e in p['edges'] if e['id'] not in used]
    candidates = []
    for i, (pa, a) in enumerate(entries):
        for pb, b in entries[i + 1:]:
            if pa['id'] == pb['id'] or tuple(sorted((a['id'], b['id']))) in rejected:
                continue
            ar, br = _roles(pa['name'] + ' ' + a.get('label', '')), _roles(pb['name'] + ' ' + b.get('label', ''))
            if _tokens(pa['name'] + ' ' + a.get('label', '')).intersection(FREE) or _tokens(pb['name'] + ' ' + b.get('label', '')).intersection(FREE):
                continue
            signals = []
            score = 0.0
            compatible = bool((('front' in ar and 'back' in br) or ('back' in ar and 'front' in br)) and (ar & br & {'side', 'shoulder', 'armhole', 'inseam', 'crotch', 'neckline'}))
            if compatible:
                score += .15
            signals.append(dict(kind='names', source='panel.name/edge.label', raw={'a': sorted(ar), 'b': sorted(br)}, contribution=.15 if compatible else 0.0))
            la, lb = _sew_length(pa, a), _sew_length(pb, b)
            measured = la is not None and lb is not None
            xa, xb = (la, lb) if measured else (a['length_mm'], b['length_mm'])
            ratio = abs(xa - xb) / max(xa, xb)
            length_score = .15 * max(0., 1 - ratio / .1) if measured else 0.0
            score += length_score
            signals.append(dict(kind='length', source='sewline.from_dxf' if measured else 'cutline.triage_only', raw={'a_mm': xa, 'b_mm': xb, 'relative_difference': ratio}, contribution=round(length_score, 6), warning=None if measured else 'Cutline length is triage only; ease and allowance unknown'))
            na = _notches(a)
            same, reverse = _notches(b), _notches(b, True)
            def error(other):
                return sum(abs(x-y) for x, y in zip(na, other)) / len(na) if na and len(na) == len(other) else math.inf
            es, er = error(same), error(reverse)
            best = min(es, er)
            orientation = 'unknown'
            if best <= .05 and abs(es - er) > .02:
                orientation = 'same' if es < er else 'reversed'
            notch_score = (.25 if len(na) >= 2 else .10) * max(0., 1-best/.05) if best < math.inf else 0.0
            score += notch_score
            signals.append(dict(kind='notches', source='edges.notches.at_mm', raw={'a_normalized': na, 'b_normalized': same, 'b_reversed': reverse, 'error': None if best == math.inf else best}, contribution=round(notch_score, 6)))
            mirror = bool(_tokens(pa['name']) & {'left', 'esquerda'} and _tokens(pb['name']) & {'right', 'direita'} or _tokens(pa['name']) & {'right', 'direita'} and _tokens(pb['name']) & {'left', 'esquerda'})
            if mirror:
                score += .05
            signals.append(dict(kind='mirror', source='panel.name', raw=mirror, contribution=.05 if mirror else 0.0))
            if score <= 0:
                continue
            matches = [{'a_mm': 0., 'b_mm': 0. if orientation != 'reversed' else b['length_mm']}, {'a_mm': a['length_mm'], 'b_mm': b['length_mm'] if orientation != 'reversed' else 0.}]
            if orientation != 'unknown' and best <= .05:
                for notch in a['notches']:
                    fraction = notch['at_mm'] / a['length_mm']
                    targets = _notches(b, orientation == 'reversed')
                    if targets:
                        target = min(targets, key=lambda x: abs(x-fraction))
                        if abs(target-fraction) <= .05:
                            matches.append({'a_mm': notch['at_mm'], 'b_mm': (1-target if orientation == 'reversed' else target)*b['length_mm']})
                matches.sort(key=lambda x: x['a_mm'])
            candidates.append(dict(id=f'proposal:{a["id"]}:{b["id"]}', a=a['id'], b=b['id'], orientation=orientation, matches=matches, construction=None, score=round(score, 6), score_label='review_priority', signals=signals, model_version=MODEL_VERSION, status='proposed'))
    candidates.sort(key=lambda p: (-p['score'], p['a'], p['b']))
    counts = {e['id']: 0 for _, e in entries}
    selected = []
    for proposal in candidates:
        if counts[proposal['a']] < 3 and counts[proposal['b']] < 3:
            selected.append(proposal)
            counts[proposal['a']] += 1
            counts[proposal['b']] += 1
    result = copy.deepcopy(doc)
    result['proposals'] = selected
    result['none_option'] = [{'edge_id': eid, 'label': 'nenhum par'} for eid in sorted(counts)]
    return result
