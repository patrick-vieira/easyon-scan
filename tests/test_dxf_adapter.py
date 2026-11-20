"""Synthetic exchange checks only; not external CAD compatibility evidence."""
import io
import math

import ezdxf
import pytest
from shapely.geometry import LineString, Polygon

from easyon_scan.dxf_adapter import (read_dxf_geometry, geometry_dxf_bytes,
                                     pattern_geometry_dxf_bytes, dxf_to_pattern_geometry)


def raw(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode()


@pytest.mark.parametrize("code,factor", [(1, 25.4), (2, 304.8), (4, 1), (5, 10), (6, 1000)])
def test_unit_roundtrip_dimensions_within_half_mm(code, factor):
    doc = ezdxf.new()
    doc.units = code
    doc.modelspace().add_lwpolyline([(0, 0), (100/factor, 0), (100/factor, 50/factor), (0, 50/factor)], close=True)
    imported = read_dxf_geometry(raw(doc))
    assert imported['report']['units']['scale_to_mm'] == factor
    again = read_dxf_geometry(geometry_dxf_bytes(imported))
    points = again['entities'][0]['points_mm']
    assert Polygon(points).bounds == pytest.approx((0, 0, 100, 50), abs=.5)
    assert again['report']['units']['code'] == 4
    assert not imported['report']['losses']


def test_ambiguous_unsupported_and_contradictory_units():
    doc = ezdxf.new()
    doc.units = 0
    with pytest.raises(ValueError, match='Ambiguous'):
        read_dxf_geometry(raw(doc))
    assert read_dxf_geometry(raw(doc), assume_units='cm')['report']['units']['assumed']
    doc.units = 4
    with pytest.raises(ValueError, match='contradicts'):
        read_dxf_geometry(raw(doc), assume_units='in')
    doc.units = 3
    with pytest.raises(ValueError, match='Unsupported'):
        read_dxf_geometry(raw(doc))


def test_curves_blocks_layers_and_losses():
    doc = ezdxf.new()
    doc.units = 5
    block = doc.blocks.new('PIECE')
    block.add_circle((0, 0), 2)
    block.add_text('Piece Name: synthetic')
    nested = doc.blocks.new('NESTED')
    nested.add_blockref('PIECE', (3, 0), dxfattribs={'xscale': 2, 'yscale': 2})
    doc.modelspace().add_blockref('NESTED', (1, 2), dxfattribs={'layer': '1'})
    result = read_dxf_geometry(raw(doc))
    assert result['report']['blocks'] == {'NESTED': 1, 'PIECE': 1}
    assert result['report']['curves'] == {'CIRCLE': 1}
    assert result['report']['layers']['1']['CIRCLE'] == 1
    assert any(x['entity_type'] == 'TEXT' for x in result['report']['losses'])
    entity = result['entities'][0]
    assert entity['layer'] == '1' and entity['closed']
    assert Polygon(entity['points_mm']).bounds == pytest.approx((0, -20, 80, 60), abs=.5)
    again = read_dxf_geometry(geometry_dxf_bytes(result))
    assert Polygon(entity['points_mm']).hausdorff_distance(Polygon(again['entities'][0]['points_mm'])) < .5


def test_arc_bulge_spline_line_and_point_roundtrip():
    doc = ezdxf.new()
    doc.units = 4
    m = doc.modelspace()
    m.add_arc((0, 0), 50, 0, 180)
    m.add_lwpolyline([(0, 0, 1), (100, 0, 0)], format='xyb')
    m.add_spline([(0, 0), (20, 30), (50, 0)])
    m.add_line((0, 0), (50, 30))
    m.add_point((10, 10))
    result = read_dxf_geometry(raw(doc))
    again = read_dxf_geometry(geometry_dxf_bytes(result))
    assert len(again['entities']) == 5
    for first, second in zip(result['entities'], again['entities']):
        assert second['points_mm'] == first['points_mm']
    arc = result['entities'][0]['points_mm']
    ideal = LineString([(50*math.cos(i*math.pi/1000), 50*math.sin(i*math.pi/1000)) for i in range(1001)])
    assert ideal.hausdorff_distance(LineString(arc)) < .5


def test_nonplanar_and_recursive_blocks_reported():
    doc = ezdxf.new()
    doc.units = 4
    doc.modelspace().add_line((0, 0, 1), (10, 0, 1))
    block = doc.blocks.new('RECURSIVE')
    block.add_blockref('RECURSIVE', (0, 0))
    doc.modelspace().add_blockref('RECURSIVE', (0, 0))
    result = read_dxf_geometry(raw(doc))
    assert not result['entities']
    assert any('nonplanar' in x['reason'] for x in result['report']['losses'])
    assert any('recursive' in x['reason'] for x in result['report']['losses'])


def test_pattern_geometry_explicit_export_roundtrip():
    from easyon_scan.assembly import dxf_to_assembly
    from easyon_scan.assembly_v1 import migrate_v0
    from easyon_scan.assembly_geometry import add_pattern_geometry
    doc = ezdxf.new()
    doc.units = 4
    doc.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 80), (0, 80)], close=True)
    doc.modelspace().add_lwpolyline([(20, 20), (30, 20), (30, 30), (20, 30)], close=True, dxfattribs={'layer': '11'})
    source = add_pattern_geometry(migrate_v0(dxf_to_assembly(raw(doc))))
    exported, report = pattern_geometry_dxf_bytes(source)
    restored, import_report = dxf_to_pattern_geometry(exported)
    assert import_report['units']['code'] == 4
    a, b = source['panels'][0], restored['panels'][0]
    assert Polygon(a['cutline'], a['holes']).hausdorff_distance(Polygon(b['cutline'], b['holes'])) < .5
    assert len(b['holes']) == 1
    assert report['scale_to_mm'] == 1 and report['losses']


def test_nested_blocks_apply_scale_rotation_translation_and_layer_inheritance():
    doc = ezdxf.new()
    doc.units = 5
    inner = doc.blocks.new('INNER')
    inner.add_lwpolyline([(0, 0), (2, 0), (2, 1), (0, 1)], close=True)
    outer = doc.blocks.new('OUTER')
    outer.add_blockref('INNER', (3, 0), dxfattribs={'xscale': 2, 'yscale': 2})
    doc.modelspace().add_blockref('OUTER', (10, 20), dxfattribs={'rotation': 90, 'layer': 'CUT'})
    geometry = read_dxf_geometry(raw(doc))
    entity = geometry['entities'][0]
    assert entity['layer'] == 'CUT'
    assert Polygon(entity['points_mm']).bounds == pytest.approx((80, 230, 100, 270), abs=.5)
    assert geometry['report']['blocks'] == {'OUTER': 1, 'INNER': 1}
    again = read_dxf_geometry(geometry_dxf_bytes(geometry))
    assert Polygon(entity['points_mm']).hausdorff_distance(Polygon(again['entities'][0]['points_mm'])) < .5


def piece_block(doc, name, size, width=100):
    block = doc.blocks.new(name)
    block.add_lwpolyline([(0, 0), (width, 0), (width, 80), (0, 80)], close=True)
    block.add_text(f'Size: {size}')
    doc.modelspace().add_blockref(name, (0, 0))
    return block


def test_v1_refuses_multiple_sizes_and_conflicting_scope_metadata():
    doc = ezdxf.new()
    doc.units = 4
    piece_block(doc, 'S', 'S', 90)
    piece_block(doc, 'M', 'M', 100)
    with pytest.raises(ValueError, match='Multiple Size'):
        dxf_to_pattern_geometry(raw(doc))
    doc = ezdxf.new()
    doc.units = 4
    block = piece_block(doc, 'ONE', 'S')
    block.add_text('Size: M')
    with pytest.raises(ValueError, match='Conflicting metadata'):
        dxf_to_pattern_geometry(raw(doc))


def test_v1_fold_and_original_source_are_retained():
    import hashlib
    doc = ezdxf.new()
    doc.units = 0
    block = piece_block(doc, 'ONE', 'M')
    block.add_text('Fold: Y')
    payload = raw(doc)
    result, report = dxf_to_pattern_geometry(payload, assume_units='mm')
    assert result['panels'][0]['on_fold']
    assert result['pattern_geometry']['panels'][0]['fold_edge_ids'] is None
    assert result['source']['file_sha256'] == hashlib.sha256(payload).hexdigest()
    assert result['source']['assumed_units'] == 'mm'
    assert result['source']['insunits_code'] == 0
    assert result['panels'][0]['provenance']['handle'] == block[0].dxf.handle
    assert report['source'] == result['source']


@pytest.mark.parametrize('panel_id', ['7', '14', 'INTERNAL', 'a/b'])
def test_export_panel_ids_are_not_layers_and_holes_keep_scope(panel_id):
    doc = ezdxf.new()
    doc.units = 4
    for name in ('ONE', 'TWO'):
        block = piece_block(doc, name, 'M')
        block.add_lwpolyline([(10, 10), (20, 10), (20, 20), (10, 20)], close=True,
                             dxfattribs={'layer': '11'})
    source, _ = dxf_to_pattern_geometry(raw(doc))
    source['panels'][0]['id'] = panel_id
    source['pattern_geometry']['panels'][0]['panel_id'] = panel_id
    exported, report = pattern_geometry_dxf_bytes(source, length_tolerance_mm=.5)
    restored, _ = dxf_to_pattern_geometry(exported, length_tolerance_mm=.5)
    assert len(restored['panels']) == 2
    assert [len(p['holes']) for p in restored['panels']] == [1, 1]
    assert report['panel_blocks'][panel_id] == 'SCAN_PANEL_000001'


def test_v1_length_tolerance_is_explicit_and_validated():
    doc = ezdxf.new()
    doc.units = 4
    piece_block(doc, 'ONE', 'M')
    with pytest.raises(ValueError, match='length_tolerance_mm'):
        dxf_to_pattern_geometry(raw(doc), length_tolerance_mm=-1)
