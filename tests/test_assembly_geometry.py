"""Synthetic contract checks; no real-photo or CAD accuracy claim."""
import copy
import io

import ezdxf
import pytest

from easyon_scan.assembly import dxf_to_assembly
from easyon_scan.assembly_v1 import migrate_v0, validate_assembly_v1
from easyon_scan.assembly_geometry import add_pattern_geometry, remove_pattern_geometry


def source(*, hole=False):
    dxf = ezdxf.new()
    dxf.header['$INSUNITS'] = 4
    dxf.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
    if hole:
        dxf.modelspace().add_lwpolyline([(20, 20), (30, 20), (30, 30), (20, 30)], close=True)
    stream = io.StringIO()
    dxf.write(stream)
    return migrate_v0(dxf_to_assembly(stream.getvalue().encode()))


def errors(doc):
    return [i for i in validate_assembly_v1(doc, length_tolerance_mm=0) if i['level'] == 'error']


def test_roundtrip_is_pure_and_preserves_all_common_fields():
    original = source(hole=True)
    snapshot = copy.deepcopy(original)
    result = add_pattern_geometry(original)
    assert original == snapshot
    assert not errors(result)
    assert remove_pattern_geometry(result) == original
    panel = result['pattern_geometry']['panels'][0]
    assert {c['kind'] for c in panel['contours']} == {'outer', 'hole'}
    assert all(e['allowance_mm'] is None for e in panel['edges'])
    assert all(p['grading'] == [] for p in panel['points'])
    result['panels'][0]['cutline'][0][0] = -42
    assert original == snapshot
    with pytest.raises(ValueError):
        add_pattern_geometry(result)
    with pytest.raises(ValueError):
        add_pattern_geometry({'schema': 'garment-assembly/v0'})


@pytest.mark.parametrize('mutation,code', [
    (lambda p: p['edges'][0].update(end_point_id=p['points'][2]['id']), 'geometry_open_contour'),
    (lambda p: p['points'].append(copy.deepcopy(p['points'][0])), 'geometry_duplicate_id'),
    (lambda p: p['edges'][0].update(start_point_id='missing'), 'geometry_open_contour'),
    (lambda p: p['contours'][0]['point_ids'].__setitem__(0, 'missing'), 'geometry_reference'),
    (lambda p: p['edges'][0].update(source_edge_id='missing'), 'geometry_edge_reference'),
    (lambda p: p['fold_edge_ids'].append('missing'), 'geometry_reference'),
    (lambda p: p.update(grain=[0, 0]), 'geometry_grain_zero'),
    (lambda p: p['edges'][0].update(allowance_mm=-1), 'structure'),
])
def test_invalid_geometry_reports_ids(mutation, code):
    doc = add_pattern_geometry(source())
    panel = doc['pattern_geometry']['panels'][0]
    mutation(panel)
    issues = errors(doc)
    assert code in {i['code'] for i in issues}
    assert all('panel' in i for i in issues)


def test_crossing_and_degenerate_contours():
    doc = add_pattern_geometry(source())
    panel = doc['pattern_geometry']['panels'][0]
    panel['points'][1]['xy_mm'], panel['points'][2]['xy_mm'] = (
        panel['points'][2]['xy_mm'], panel['points'][1]['xy_mm'])
    assert 'geometry_self_intersection' in {i['code'] for i in errors(doc)}


def test_notches_allowances_grading_and_unknown_fold():
    base = source()
    base['panels'][0]['on_fold'] = True
    doc = add_pattern_geometry(base)
    panel = doc['pattern_geometry']['panels'][0]
    assert panel['fold_edge_ids'] is None
    panel['notches'] = [dict(id='n1', edge_id=panel['edges'][0]['id'], at_mm=10, kind='v_slit')]
    panel['edges'][0]['allowance_mm'] = 0
    panel['points'][0]['grading'] = [dict(size='L', delta_mm=[2, -3])]
    assert not errors(doc)
    assert remove_pattern_geometry(doc) == base
    panel['notches'][0]['at_mm'] = 101
    assert 'geometry_notch_range' in {i['code'] for i in errors(doc)}
    panel['points'][0]['grading'] *= 2
    assert 'geometry_grading_size' in {i['code'] for i in errors(doc)}


def test_bad_structure_and_missing_panel_do_not_crash():
    doc = add_pattern_geometry(source())
    doc['pattern_geometry']['panels'][0]['points'][0]['xy_mm'] = [float('nan'), 0]
    assert 'structure' in {i['code'] for i in errors(doc)}
    doc = add_pattern_geometry(source())
    doc['pattern_geometry']['panels'] = []
    assert 'geometry_panel_reference' in {i['code'] for i in errors(doc)}


def test_converter_maps_legacy_notches_grain_and_source_edges():
    base = source()
    panel = base['panels'][0]
    panel['grain'] = [0., 1.]
    panel['edges'][0]['notches'] = [dict(at_mm=20., kind='v_slit')]
    converted = add_pattern_geometry(base)
    result = converted['pattern_geometry']['panels'][0]
    assert result['grain'] == [0., 1.]
    assert result['notches'][0]['at_mm'] == 20.
    assert result['notches'][0]['kind'] == 'v_slit'
    assert result['edges'][0]['source_edge_id'] == panel['edges'][0]['id']
    assert not errors(converted)


def test_hole_outside_outer_is_rejected():
    doc = add_pattern_geometry(source(hole=True))
    panel = doc['pattern_geometry']['panels'][0]
    hole = next(c for c in panel['contours'] if c['kind'] == 'hole')
    for point in panel['points']:
        if point['id'] in hole['point_ids']:
            point['xy_mm'][0] += 200
    assert 'geometry_invalid_polygon' in {i['code'] for i in errors(doc)}


@pytest.mark.parametrize('edge_index,at_mm', [(0, 0.), (0, 100.), (1, 0.), (1, 100.), (3, 100.)])
def test_converter_preserves_notches_at_source_edge_boundaries(edge_index, at_mm):
    base = source()
    edge = base['panels'][0]['edges'][edge_index]
    edge['notches'] = [dict(at_mm=at_mm, kind='notch')]
    result = add_pattern_geometry(base)['pattern_geometry']['panels'][0]
    assert len(result['notches']) == 1
    notch = result['notches'][0]
    target = next(e for e in result['edges'] if e['id'] == notch['edge_id'])
    assert target['source_edge_id'] == edge['id']
    assert notch['at_mm'] == at_mm


def test_multisegment_edge_notches_at_internal_and_final_boundaries():
    from easyon_scan.assembly_geometry import add_molde_geometry
    dxf = ezdxf.new()
    dxf.header['$INSUNITS'] = 4
    dxf.modelspace().add_lwpolyline([(0, 0), (40, 0), (100, 0), (100, 100), (0, 100)], close=True)
    stream = io.StringIO()
    dxf.write(stream)
    base = migrate_v0(dxf_to_assembly(stream.getvalue().encode()))
    edge = base['panels'][0]['edges'][0]
    assert edge['end_vertex'] - edge['start_vertex'] == 2
    edge['notches'] = [dict(at_mm=40., kind='notch'), dict(at_mm=100., kind='u')]
    converted = add_pattern_geometry(base)
    panel = converted['pattern_geometry']['panels'][0]
    assert [(n['at_mm'], n['kind']) for n in panel['notches']] == [(0., 'notch'), (60., 'u')]
    assert len({n['id'] for n in panel['notches']}) == 2
    assert not errors(converted)
    ak = add_molde_geometry(converted)['pattern_geometry']['panels'][0]['molde_geometria']['dados']
    assert ak['arestas'][0]['curva'] == {'pontos': [[40., 0.]]}
    assert ak['arestas'][0]['id'] == edge['id']
    assert [notch['posicao_mm'] for notch in ak['piques']] == [40., 100.]


def test_converter_forwards_declared_seam_tolerance(tmp_path):
    import json
    from easyon_scan.synthetic_garment import generate_fixture
    generate_fixture(tmp_path, sizes=('M',))
    doc = json.loads((tmp_path / 'M.assembly-v1.json').read_text())
    seam = doc['seams'][0]
    seam['ease_mm'] += .25
    decision = next(d for d in doc['review']['decisions'] if d.get('proposal_id') == seam['proposal_id'])
    decision['after'] = {k: copy.deepcopy(v) for k, v in seam.items() if k not in ('id', 'proposal_id')}
    assert add_pattern_geometry(doc, length_tolerance_mm=.5)['pattern_geometry']
    assert add_pattern_geometry(doc)['pattern_geometry']
    with pytest.raises(ValueError, match='Invalid garment'):
        add_pattern_geometry(doc, length_tolerance_mm=.1)
    with pytest.raises(ValueError, match='nonnegative'):
        add_pattern_geometry(doc, length_tolerance_mm=-1)


@pytest.mark.parametrize('mutation,code', [
    (lambda p, hole, outer: p.update(fold_edge_ids=[outer, outer]), 'geometry_duplicate_reference'),
    (lambda p, hole, outer: p.update(fold_edge_ids=[hole]), 'geometry_fold_reference'),
    (lambda p, hole, outer: next(e for e in p['edges'] if e['id'] == hole).update(
        source_edge_id=next(e['source_edge_id'] for e in p['edges'] if e['id'] == outer)), 'geometry_edge_reference'),
])
def test_fold_and_hole_references(mutation, code):
    doc = add_pattern_geometry(source(hole=True))
    panel = doc['pattern_geometry']['panels'][0]
    hole = next(c['edge_ids'][0] for c in panel['contours'] if c['kind'] == 'hole')
    outer = next(c['edge_ids'][0] for c in panel['contours'] if c['kind'] == 'outer')
    mutation(panel, hole, outer)
    assert code in {i['code'] for i in errors(doc)}


def test_extension_notch_kind_uses_v0_vocabulary():
    doc = add_pattern_geometry(source())
    panel = doc['pattern_geometry']['panels'][0]
    panel['notches'] = [dict(id='n1', edge_id=panel['edges'][0]['id'], at_mm=0., kind='invented')]
    assert 'structure' in {i['code'] for i in errors(doc)}


def test_converter_snaps_floating_endpoint_without_losing_notch():
    base = source()
    edge = base['panels'][0]['edges'][0]
    edge['notches'] = [dict(at_mm=100. - 1e-10, kind='notch')]
    converted = add_pattern_geometry(base)
    notches = converted['pattern_geometry']['panels'][0]['notches']
    assert len(notches) == 1
    assert notches[0]['at_mm'] == 100.
    assert not errors(converted)


@pytest.mark.parametrize('shifted_edge', [0, 1])
def test_notches_preserved_with_v0_accepted_arc_length_discrepancy(shifted_edge):
    base = source()
    e0, e1 = base['panels'][0]['edges'][:2]
    d = 2e-4
    if shifted_edge == 0:
        e0['to_mm'] += d
        e0['length_mm'] += d
    else:
        e1['from_mm'] -= d
    e0['notches'] = [dict(at_mm=e0['length_mm'], kind='t')]
    e1['notches'] = [dict(at_mm=0., kind='u')]
    assert not errors(base)
    converted = add_pattern_geometry(base)
    panel = converted['pattern_geometry']['panels'][0]
    assert len(panel['notches']) == sum(len(e['notches']) for e in base['panels'][0]['edges']) == 2
    assert [(n['at_mm'], n['kind']) for n in panel['notches']] == [(100., 't'), (0., 'u')]
    for notch, original in zip(panel['notches'], [e0, e1]):
        mapped = next(e for e in panel['edges'] if e['id'] == notch['edge_id'])
        assert mapped['source_edge_id'] == original['id']
    assert remove_pattern_geometry(converted) == base
    assert not errors(converted)


def test_near_zero_edge_rejected():
    doc = add_pattern_geometry(source())
    points = doc['pattern_geometry']['panels'][0]['points']
    points[1]['xy_mm'] = [points[0]['xy_mm'][0] + 1e-12, points[0]['xy_mm'][1]]
    assert 'geometry_zero_edge' in {i['code'] for i in errors(doc)}


def test_noninteger_quadrilaterals_and_corner_notches_are_convertible():
    import random
    import math
    rng = random.Random(33)
    for _ in range(200):
        vertices = [(rng.uniform(0, 10), rng.uniform(0, 10)),
                    (rng.uniform(90, 110), rng.uniform(0, 10)),
                    (rng.uniform(90, 110), rng.uniform(90, 110)),
                    (rng.uniform(0, 10), rng.uniform(90, 110))]
        dxf = ezdxf.new()
        dxf.header['$INSUNITS'] = 4
        dxf.modelspace().add_lwpolyline(vertices, close=True)
        dxf.modelspace().add_point(vertices[1], dxfattribs={'layer': 'NOTCH'})
        stream = io.StringIO()
        dxf.write(stream)
        base = migrate_v0(dxf_to_assembly(stream.getvalue().encode()))
        panel = base['panels'][0]
        assert len(panel['cutline']) == 5
        assert panel['cutline'][0] == panel['cutline'][-1]
        assert all(math.dist(a, b) > 1e-8 for a, b in zip(panel['cutline'], panel['cutline'][1:]))
        # Also exercise endpoint subtraction after several accumulated segments.
        edge = panel['edges'][-1]
        edge['notches'].append(dict(at_mm=edge['length_mm'], kind='u'))
        converted = add_pattern_geometry(base)
        assert len(converted['pattern_geometry']['panels'][0]['notches']) == 2
        assert not errors(converted)


def test_provisional_molde_profile_roundtrip_and_purity():
    from easyon_scan.assembly_geometry import (
        add_molde_geometry, remove_molde_geometry,
        to_molde_geometria, from_molde_geometria,
    )
    base = add_pattern_geometry(source(hole=True))
    panel = base['pattern_geometry']['panels'][0]
    panel['points'][0]['grading'] = [dict(size='L', delta_mm=[2, -3])]
    panel['edges'][0]['allowance_mm'] = 4.5
    panel['grain'] = [0., 1.]
    snapshot = copy.deepcopy(base)
    result = add_molde_geometry(base, profile="provisional")
    assert base == snapshot
    assert not errors(result)
    assert remove_molde_geometry(result) == base
    assert remove_pattern_geometry(result) == remove_pattern_geometry(base)
    profile = result['pattern_geometry']['panels'][0]['molde_geometria']
    profile_snapshot = copy.deepcopy(profile)
    assert from_molde_geometria(profile) == panel
    assert to_molde_geometria(from_molde_geometria(profile)) == profile
    assert profile == profile_snapshot
    assert profile['fio'] == [0., 1.]
    assert profile['regras_graduacao'][0]['grading'][0]['delta_mm'] == [2, -3]
    assert profile['furos']
    assert all(profile[k] is None for k in (
        'grade', 'linhas_internas', 'quantidade', 'incrementos', 'tamanhos'))
    assert all('source_edge_id' not in e for e in profile['arestas'])
    assert profile['dados']['scan']['source_edges']
    with pytest.raises(ValueError, match='overwritten'):
        add_molde_geometry(result)
    with pytest.raises(ValueError, match='migrate_v0'):
        add_molde_geometry({'schema': 'garment-assembly/v0'})


@pytest.mark.parametrize('mutation,code', [
    (lambda p: p['edges'][0].update(end_point_id=p['points'][2]['id']), 'geometry_open_contour'),
    (lambda p: p['points'].append(copy.deepcopy(p['points'][0])), 'geometry_duplicate_id'),
    (lambda p: p['edges'][0].update(start_point_id='missing'), 'geometry_reference'),
])
def test_molde_extension_keeps_geometry_error_ids(mutation, code):
    from easyon_scan.assembly_geometry import add_molde_geometry
    doc = add_molde_geometry(source())
    panel = doc['pattern_geometry']['panels'][0]
    mutation(panel)
    issues = [i for i in errors(doc) if i['code'] == code]
    assert issues
    assert all(i['panel'] == panel['panel_id'] and i['entity_id'] for i in issues)


def test_molde_profile_crossing_and_divergence_are_rejected():
    from easyon_scan.assembly_geometry import add_molde_geometry, from_molde_geometria
    doc = add_molde_geometry(source(), profile="provisional")
    panel = doc['pattern_geometry']['panels'][0]
    panel['points'][1]['xy_mm'], panel['points'][2]['xy_mm'] = (
        panel['points'][2]['xy_mm'], panel['points'][1]['xy_mm'])
    issues = errors(doc)
    crossing = next(i for i in issues if i['code'] == 'geometry_self_intersection')
    assert crossing['entity_id'] == panel['contours'][0]['id']
    assert 'geometry_molde_projection' in {i['code'] for i in issues}
    profile = panel['molde_geometria']
    profile['dados']['other'] = 'unsupported'
    with pytest.raises(ValueError, match='structure'):
        from_molde_geometria(profile)
    assert 'structure' in {i['code'] for i in errors(doc)}


def test_molde_profile_generated_review_fixture_preserves_common_v1(tmp_path):
    import json
    from easyon_scan.synthetic_garment import generate_fixture
    from easyon_scan.assembly_geometry import add_molde_geometry
    generate_fixture(tmp_path, sizes=('M',))
    base = json.loads((tmp_path / 'M.assembly-v1.json').read_text())
    snapshot = copy.deepcopy(base)
    converted = add_molde_geometry(base)
    assert not errors(converted)
    assert remove_pattern_geometry(converted) == base == snapshot
    assert len(converted['pattern_geometry']['panels']) == len(base['panels'])
    originals = {p['id']: p for p in base['panels']}
    for panel in converted['pattern_geometry']['panels']:
        dados = panel['molde_geometria']['dados']
        original = originals[panel['panel_id']]
        assert dados['scan']['proveniencia'] == original['provenance']
        assert list(dados['scan']['proveniencia_arestas']) == [e['id'] for e in original['edges']]
        assert dados['grade'] == {'ordem': ['M'], 'base': 'M'}


@pytest.mark.parametrize('mutation', [
    lambda m: m['pontos'][0].update(unexpected=True),
    lambda m: m['arestas'][0].update(unexpected=True),
    lambda m: m['contornos'][0].update(unexpected=True),
    lambda m: m['dados']['scan'].update(unexpected=True),
    lambda m: m['pontos'].append(copy.deepcopy(m['pontos'][0])),
    lambda m: m['arestas'].append(copy.deepcopy(m['arestas'][0])),
    lambda m: m['margem'].append(copy.deepcopy(m['margem'][0])),
    lambda m: m['regras_graduacao'].append(copy.deepcopy(m['regras_graduacao'][0])),
    lambda m: m['dados']['scan']['source_edges'].append(copy.deepcopy(m['dados']['scan']['source_edges'][0])),
    lambda m: m['arestas'][0].update(start_point_id='missing'),
    lambda m: m['contornos'][0]['point_ids'].__setitem__(0, 'missing'),
    lambda m: m['dobra'].append('missing'),
    lambda m: m['piques'].append(dict(id='n1', edge_id='missing', at_mm=0, kind='notch')),
    lambda m: m['pontos'][0].update(xy_mm=['bad', 0]),
    lambda m: m['pontos'][0].update(xy_mm=[True, 0]),
    lambda m: m['pontos'][0].update(xy_mm=[float('nan'), 0]),
    lambda m: m['margem'][0].update(allowance_mm=True),
    lambda m: m.update(fio=[0, 0]),
    lambda m: m['regras_graduacao'][0].update(grading=[dict(size='L', delta_mm=[1, 0])] * 2),
])
def test_inverse_molde_rejects_malformed_structure_and_topology(mutation):
    from easyon_scan.assembly_geometry import to_molde_geometria, from_molde_geometria
    panel = add_pattern_geometry(source())['pattern_geometry']['panels'][0]
    profile = to_molde_geometria(panel)
    mutation(profile)
    with pytest.raises(ValueError):
        from_molde_geometria(profile)


def test_inverse_molde_does_not_alias_input_or_reconstructed_fields():
    from easyon_scan.assembly_geometry import to_molde_geometria, from_molde_geometria
    panel = add_pattern_geometry(source(hole=True))['pattern_geometry']['panels'][0]
    panel['points'][0]['grading'] = [dict(size='L', delta_mm=[2, -3])]
    profile = to_molde_geometria(panel)
    snapshot = copy.deepcopy(profile)
    rebuilt = from_molde_geometria(profile)
    rebuilt['points'][0]['xy_mm'][0] += 10
    rebuilt['points'][0]['grading'][0]['delta_mm'][0] += 10
    rebuilt['contours'][0]['point_ids'].append('another')
    assert profile == snapshot
    assert panel['points'][0]['grading'][0]['delta_mm'] == [2, -3]


def test_ak_projection_maps_common_fields_and_keeps_scan_extensions():
    from easyon_scan.assembly_geometry import add_molde_geometry, remove_molde_geometry
    base = source(hole=True)
    base['source']['size'] = 'G'
    panel = base['panels'][0]
    panel.update(grain=[0., 1.], on_fold=True, quantity='2',
                 sewline=[[10, 10], [90, 10], [90, 90], [10, 90], [10, 10]],
                 sewline_status='from_dxf')
    panel['edges'][0].update(semantic_label='costura', sewline_interval_mm=[0, 80],
                             notches=[dict(at_mm=0, kind='castle'), dict(at_mm=10, kind='check')])
    base = add_pattern_geometry(base)
    ext_panel = base['pattern_geometry']['panels'][0]
    ext_panel['edges'][0]['allowance_mm'] = 10
    ext_panel['points'][0]['grading'] = [dict(size='GG', delta_mm=[2, 3])]
    result = add_molde_geometry(base)
    assert not errors(result)
    assert remove_molde_geometry(result) == base
    profile = result['pattern_geometry']['panels'][0]['molde_geometria']
    dados = profile['dados']
    assert profile == dict(unidade='mm', origem='scan', margem_conhecida=False, dados=dados)
    assert dados['grade'] == {'ordem': ['G'], 'base': 'G'}
    assert dados['pontos'][0] == dict(id='P1', x=0, y=0, tipo='pique')
    eid = panel['edges'][0]['id']
    assert dados['arestas'][0] == dict(id=eid, de='P1', para='P2', curva=None,
                                      tipo='costura', nome='costura', par=None)
    assert dados['piques'] == [dict(aresta=eid, posicao_mm=0, tipo='T', tipo_origem='castle'),
                              dict(aresta=eid, posicao_mm=10, tipo='V', tipo_origem='check')]
    assert dados['fio'] == dict(de=[50, 0], para=[50, 100])
    assert dados['dobra'] == dict(aresta=None)
    assert dados['margem'] == dict(padrao_mm=None, por_aresta={})
    assert dados['furos'] == [panel['holes'][0][:-1]]
    assert dados['quantidade'] == dict(par=False, espelhar=False, texto='2')
    assert dados['regras_graduacao'] == dados['incrementos'] == dados['tamanhos'] == {}
    assert dados['linha_costura'] == dict(pontos=panel['sewline'][:-1], estado='from_dxf')
    assert dados['scan']['fonte'] == base['source']
    assert dados['scan']['intervalos_costura_mm'][eid] == [0, 80]
    malformed = copy.deepcopy(result)
    scan = malformed['pattern_geometry']['panels'][0]['molde_geometria']['dados']['scan']
    scan['intervalos_costura_mm'][eid][0] = False
    assert 'structure' in {i['code'] for i in errors(malformed)}
    dados['scan']['fonte']['size'] = 'P'
    assert base['source']['size'] == 'G'
    assert 'geometry_molde_projection' in {i['code'] for i in errors(result)}


def test_ak_projection_rejects_invalid_ak_notch_without_changing_legacy():
    from easyon_scan.assembly_geometry import add_molde_geometry
    base = source()
    edge = base['panels'][0]['edges'][0]
    edge['to_mm'] += 2e-4
    edge['length_mm'] += 2e-4
    edge['notches'] = [dict(at_mm=edge['length_mm'], kind='t')]
    assert not errors(base)
    assert not errors(add_molde_geometry(base, profile='provisional'))
    with pytest.raises(ValueError, match=f"Notch exceeds AK edge {edge['id']}"):
        add_molde_geometry(base)


def test_ak_projection_reports_unknown_panel_reference_by_id():
    from easyon_scan.assembly_geometry import add_molde_geometry
    result = add_molde_geometry(source())
    result['pattern_geometry']['panels'][0]['panel_id'] = 'missing-panel'
    issues = errors(result)
    assert any(i['code'] == 'geometry_panel_reference' and
               i['panel'] == i['entity_id'] == 'missing-panel' for i in issues)
    assert not any(i['code'] == 'geometry_molde_projection' for i in issues)


def test_ak_projection_rejects_unknown_source_notch_kind_at_validation():
    from easyon_scan.assembly_geometry import add_molde_geometry
    base = source()
    base['panels'][0]['edges'][0]['notches'] = [dict(at_mm=0, kind='invented')]
    assert any(i['code'] == 'structure' and '.notches[0].kind' in i['message'] for i in errors(base))
    with pytest.raises(ValueError, match='Invalid garment-assembly/v1'):
        add_molde_geometry(base)


def test_ak_projection_normalizes_nonunit_grain_and_empty_size():
    from easyon_scan.assembly_geometry import add_molde_geometry
    base = source()
    base['panels'][0]['grain'] = [0, 2]
    base['source']['size'] = ''
    result = add_molde_geometry(base)
    assert not errors(result)
    assert result['panels'][0]['grain'] == [0, 2]
    dados = result['pattern_geometry']['panels'][0]['molde_geometria']['dados']
    assert dados['fio'] == dict(de=[50, 0], para=[50, 100])
    assert dados['grade'] == dict(ordem=[], base=None)
    assert dados['scan']['fonte']['size'] == ''
    result['panels'][0]['grain'] = [0, 0]
    assert 'grain_zero' in {i['code'] for i in errors(result)}


def test_ak_projection_keeps_closing_notch_on_last_edge():
    from easyon_scan.assembly_geometry import add_molde_geometry
    base = source()
    edge = base['panels'][0]['edges'][-1]
    edge['notches'] = [dict(at_mm=edge['length_mm'], kind='u')]
    result = add_molde_geometry(base)
    dados = result['pattern_geometry']['panels'][0]['molde_geometria']['dados']
    assert dados['arestas'][-1]['para'] == dados['pontos'][0]['id']
    assert dados['pontos'][0]['tipo'] == 'canto'
    assert dados['piques'] == [
        dict(aresta=edge['id'], posicao_mm=edge['length_mm'], tipo='U', tipo_origem='u'),
    ]


def test_ak_projection_uses_distinct_point_ids_when_source_edges_collide():
    from easyon_scan.assembly_geometry import add_molde_geometry
    base = source()
    base['panels'][0]['edges'][0]['id'] = 'P1'
    base['panels'][0]['edges'][1]['id'] = 'P1_'
    result = add_molde_geometry(base)
    assert not errors(result)
    dados = result['pattern_geometry']['panels'][0]['molde_geometria']['dados']
    assert [p['id'] for p in dados['pontos']] == ['P1__', 'P2', 'P3', 'P4']
    assert dados['arestas'][0]['id'] == 'P1'
    assert dados['arestas'][0]['de'] == dados['arestas'][-1]['para'] == 'P1__'
