"""Explicit, review-only garment-assembly/v1 contract; dimensions are millimetres."""
import copy
import hashlib
import json
import math
from importlib.resources import files

from shapely.geometry import LineString, Point, Polygon

from .assembly import validate_assembly


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _issue(code, message, panel=None):
    return dict(level='error', code=code, panel=panel, message=message)


def migrate_v0(doc):
    """Copy a valid v0 document; legacy proposals remain suggestions only."""
    if doc.get('schema') != 'garment-assembly/v0':
        raise ValueError('Expected garment-assembly/v0')
    base = copy.deepcopy(doc)
    legacy = base.pop('proposals', [])
    base.pop('none_option', None)
    base.pop('rejected_pairs', None)
    for panel in base['panels']:
        for edge in panel['edges']:
            edge.pop('label', None)
    if any(i['level'] == 'error' for i in validate_assembly(base)):
        raise ValueError('Invalid v0 assembly')
    if base['seams'] or base['openings'] or base['review']['status'] != 'pending':
        raise ValueError('Migration requires pending v0 without confirmed construction')
    result = copy.deepcopy(base)
    result['schema'] = 'garment-assembly/v1'
    result['landmarks'] = []
    result['operations'] = []
    result['proposals'] = []
    result['review']['decisions'] = []
    for panel in result['panels']:
        panel['role'] = 'unknown'
        for edge in panel['edges']:
            edge['semantic_label'] = next((e.get('label') for p in doc['panels'] for e in p['edges'] if e['id'] == edge['id']), None)
            edge['sewline_interval_mm'] = None
    for old in legacy:
        a = {'edge_id': old['a'], 'start_mm': 0., 'end_mm': _edge(result, old['a'])['length_mm']}
        b = {'edge_id': old['b'], 'start_mm': 0., 'end_mm': _edge(result, old['b'])['length_mm']}
        proposal = dict(id=old['id'], a=a, b=b, orientation=old['orientation'],
                        matches=copy.deepcopy(old['matches']), construction=old.get('construction'),
                        allowance_mm={'a': None, 'b': None}, ease_mm=None,
                        score=old['score'], signals=copy.deepcopy(old['signals']),
                        model_version=old['model_version'], created_at=None, status='proposed')
        proposal['content_sha256'] = _digest(proposal)
        result['proposals'].append(proposal)
    return result


def _edge(doc, edge_id):
    for panel in doc['panels']:
        for edge in panel['edges']:
            if edge['id'] == edge_id:
                return edge
    raise ValueError(f'Unknown edge {edge_id}')


def _length(doc, ref):
    edge = _edge(doc, ref['edge_id'])
    interval = edge['sewline_interval_mm']
    if interval is None:
        return None
    return (ref['end_mm'] - ref['start_mm']) / edge['length_mm'] * (interval[1] - interval[0])


def validate_assembly_v1(doc, *, length_tolerance_mm):
    """Validate v1 structure and supported plain/gather construction semantics."""
    issues = []
    if not isinstance(length_tolerance_mm, (int, float)) or not math.isfinite(length_tolerance_mm) or length_tolerance_mm < 0:
        raise ValueError('Declare a finite nonnegative length_tolerance_mm')
    schema = json.loads(files('easyon_scan').joinpath('schemas/garment-assembly-v1.schema.json').read_text())
    def structure(value, spec, path='$'):
        if '$ref' in spec:
            spec = schema['$defs'][spec['$ref'].split('/')[-1]]
        kind = spec.get('type')
        kinds = kind if isinstance(kind, list) else [kind]
        valid = {'object': lambda x: isinstance(x, dict), 'array': lambda x: isinstance(x, list),
                 'string': lambda x: isinstance(x, str), 'null': lambda x: x is None,
                 'number': lambda x: type(x) in (int, float) and math.isfinite(x),
                 'integer': lambda x: type(x) is int, 'boolean': lambda x: type(x) is bool}
        if kind and not any(valid[k](value) for k in kinds):
            issues.append(_issue('structure', f'{path}: invalid type'))
            return
        if 'const' in spec and value != spec['const'] or 'enum' in spec and value not in spec['enum']:
            issues.append(_issue('structure', f'{path}: invalid value'))
        if isinstance(value, dict):
            for key in spec.get('required', []):
                if key not in value:
                    issues.append(_issue('structure', f'{path}: missing {key}'))
            if spec.get('additionalProperties') is False:
                for key in value.keys() - spec.get('properties', {}).keys():
                    issues.append(_issue('structure', f'{path}: unexpected {key}'))
            for key, child in spec.get('properties', {}).items():
                if key in value:
                    structure(value[key], child, path + '.' + key)
        elif isinstance(value, list):
            if not spec.get('minItems', 0) <= len(value) <= spec.get('maxItems', math.inf):
                issues.append(_issue('structure', f'{path}: invalid length'))
            for n, item in enumerate(value):
                structure(item, spec.get('items', {}), f'{path}[{n}]')
        elif type(value) in (int, float):
            if not spec.get('minimum', -math.inf) <= value <= spec.get('maximum', math.inf):
                issues.append(_issue('structure', f'{path}: invalid range'))
    structure(doc, schema)
    if issues:
        return issues
    base = copy.deepcopy(doc)
    base['schema'] = 'garment-assembly/v0'
    for key in ('landmarks', 'proposals', 'operations'):
        base.pop(key)
    base['seams'] = []
    base['openings'] = []
    base['review'].pop('decisions')
    for panel in base['panels']:
        panel.pop('role')
        for edge in panel['edges']:
            edge.pop('semantic_label')
            edge.pop('sewline_interval_mm')
    issues.extend(validate_assembly(base))
    edges = {e['id']: (p, e) for p in doc['panels'] for e in p['edges']}
    proposals = {p['id']: p for p in doc['proposals']}
    if len(proposals) != len(doc['proposals']):
        issues.append(_issue('duplicate_id', 'Duplicate proposal ID'))
    for panel in doc['panels']:
        for edge in panel['edges']:
            interval = edge['sewline_interval_mm']
            if interval is not None:
                if panel['sewline_status'] != 'from_dxf' or not 0 <= interval[0] < interval[1] <= LineString(panel['sewline']).length:
                    issues.append(_issue('sewline_interval', 'Sewline interval must lie on a measured sewline', panel['id']))
    used = []
    def check_ref(ref, owner, consume=False):
        eid = ref['edge_id']
        if eid not in edges:
            issues.append(_issue('segment_reference', f'{owner}: unknown edge {eid}'))
            return False
        panel, edge = edges[eid]
        if not 0 <= ref['start_mm'] < ref['end_mm'] <= edge['length_mm']:
            issues.append(_issue('segment_range', f'{owner}: segment outside edge', panel['id']))
            return False
        if consume:
            for other, prior in used:
                if eid == other['edge_id'] and max(ref['start_mm'], other['start_mm']) < min(ref['end_mm'], other['end_mm']) - 1e-9:
                    issues.append(_issue('segment_reused', f'{owner} overlaps {prior}', panel['id']))
            used.append((ref, owner))
        return True
    def check_pair(pair, owner, confirmed=False):
        if not (check_ref(pair['a'], owner, confirmed) and check_ref(pair['b'], owner, confirmed)):
            return
        if pair['a']['edge_id'] == pair['b']['edge_id']:
            issues.append(_issue('same_segment', f'{owner}: identical edge'))
        matches = pair['matches']
        if len(matches) < 2 or pair['orientation'] == 'unknown' and confirmed:
            issues.append(_issue('matches', f'{owner}: two matches and known orientation required'))
        for m in matches:
            if not pair['a']['start_mm'] <= m['a_mm'] <= pair['a']['end_mm'] or not pair['b']['start_mm'] <= m['b_mm'] <= pair['b']['end_mm']:
                issues.append(_issue('match_range', f'{owner}: match outside segment'))
        for x, y in zip(matches, matches[1:]):
            direction = 1 if pair['orientation'] == 'same' else -1
            if x['a_mm'] >= y['a_mm'] or (pair['orientation'] != 'unknown' and direction * (y['b_mm'] - x['b_mm']) <= 0):
                issues.append(_issue('match_order', f'{owner}: nonmonotonic matches'))
        if confirmed:
            la, lb = _length(doc, pair['a']), _length(doc, pair['b'])
            if la is None or lb is None:
                issues.append(_issue('sewline_missing', f'{owner}: measured sewline intervals required'))
            elif pair['construction'] == 'plain':
                if pair['ease_mm'] is None or abs((la - lb) - pair['ease_mm']) > length_tolerance_mm:
                    issues.append(_issue('ease', f'{owner}: signed a minus b sewline difference exceeds tolerance'))
            elif pair['construction'] == 'gather':
                if pair['ease_mm'] is None or pair['ease_mm'] <= 0 or abs((la - lb) - pair['ease_mm']) > length_tolerance_mm:
                    issues.append(_issue('ease', f'{owner}: gather requires positive a minus b ease'))
            else:
                issues.append(_issue('construction', f'{owner}: unsupported construction'))
    for proposal in doc['proposals']:
        payload = {k: v for k, v in proposal.items() if k != 'content_sha256'}
        if _digest(payload) != proposal['content_sha256']:
            issues.append(_issue('proposal_changed', f"Proposal {proposal['id']} content hash mismatch"))
        check_pair(proposal, proposal['id'])
    decisions = doc['review']['decisions']
    for decision in decisions:
        proposal = proposals.get(decision['proposal_id'])
        if proposal is None or decision['before_sha256'] != proposal['content_sha256']:
            issues.append(_issue('decision_reference', 'Decision must refer to original proposal hash'))
        if not decision['reviewer'] or not decision['at'] or not decision['reason']:
            issues.append(_issue('decision_trace', 'Decision requires reviewer, time and reason'))
    accepted = {d['proposal_id'] for d in decisions if d['action'] == 'accept'}
    for seam in doc['seams']:
        if seam['proposal_id'] not in accepted:
            issues.append(_issue('unconfirmed_seam', 'Seam requires an accept decision'))
        check_pair(seam, seam['id'], True)
    for op in doc['operations']:
        if op['type'] not in ('hem', 'fold'):
            issues.append(_issue('operation_unsupported', f"{op['type']} is not implemented"))
        for ref in op['segments']:
            check_ref(ref, op['id'], True)
        if not any(d['action'] == 'confirm_operation' and d.get('operation_id') == op['id'] for d in decisions):
            issues.append(_issue('unconfirmed_operation', f"Operation {op['id']} lacks decision"))
    if doc['review']['status'] == 'human_approved':
        closed = {d['panel_id'] for d in decisions if d['action'] == 'close_panel'}
        if closed != {p['id'] for p in doc['panels']}:
            issues.append(_issue('panel_approval', 'All complete panels require close_panel decisions'))
    return issues
