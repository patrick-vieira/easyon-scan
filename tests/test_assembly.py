"""Synthetic planar geometry only; these tests make no CAD compatibility claim."""
import copy
import io
import json
from importlib.resources import files

import ezdxf
import numpy as np
import pytest
from shapely.geometry import Polygon

from easyon_scan.assembly import dxf_to_assembly, validate_assembly
from easyon_scan.dxf import dxf_bytes
from easyon_scan.scan import Measurement, Piece


def encoded(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode('utf-8')


def synthetic():
    rectangle = np.array([[0,0],[100,0],[100,100],[0,100]], float)
    ell = np.array([[200,0],[300,0],[300,40],[240,40],[240,100],[200,100]], float)
    pieces = []
    for outer in (rectangle, ell):
        x = outer[0,0]
        pieces.append(Piece(outer, [np.array([[x+10,10],[x+20,10],[x+20,20],[x+10,20]])],
                            [np.array([[x+40,0],[x+45,5],[x+50,0]])],
                            [np.array([[x+25,25],[x+25,75]])]))
    return Measurement(rectangle, 0, 0, 0, 0, 0, None, pieces=pieces)


@pytest.mark.parametrize('mode', ['plain', 'aama'])
def test_round_trip(mode):
    result = dxf_to_assembly(dxf_bytes(synthetic(), mode=mode, size='M', piece_names=['Front','Back']))
    assert result['review']['status'] == 'pending'
    assert result['seams'] == result['openings'] == []
    assert len(result['panels']) == 2
    assert [Polygon(p['cutline'], p['holes']).area for p in result['panels']] == pytest.approx([9900,6300])
    assert [len(p['edges']) for p in result['panels']] == [5,7]
    for panel in result['panels']:
        assert len(panel['holes']) == 1
        assert panel['grain'] is not None
        assert sum(len(e['notches']) for e in panel['edges']) == 1
        assert panel['provenance']['handle']
    assert validate_assembly(result) == []
    if mode == 'aama':
        assert [p['name'] for p in result['panels']] == ['Front','Back']
        assert result['source']['size'] == 'M'


@pytest.mark.parametrize('code,scale', [(5,10),(1,25.4),(2,304.8),(6,1000)])
def test_units(code, scale):
    doc = ezdxf.new()
    doc.units = code
    doc.modelspace().add_lwpolyline([(0,0),(100/scale,0),(100/scale,50/scale),(0,50/scale)], close=True)
    assembly = dxf_to_assembly(encoded(doc))
    assert Polygon(assembly['panels'][0]['cutline']).area == pytest.approx(5000)
    assert assembly['source']['insunits_code'] == code


def test_unitless():
    doc = ezdxf.new()
    doc.units = 0
    doc.modelspace().add_lwpolyline([(0,0),(10,0),(10,10),(0,10)], close=True)
    with pytest.raises(ValueError, match='Unitless'):
        dxf_to_assembly(encoded(doc))
    assembly = dxf_to_assembly(encoded(doc), assume_units='cm')
    assert Polygon(assembly['panels'][0]['cutline']).area == 10000
    assert assembly['source']['units'] == 'unitless'
    assert assembly['source']['assumed_units'] == 'cm'


def test_self_intersection():
    doc = ezdxf.new()
    doc.units = 4
    doc.modelspace().add_lwpolyline([(0,0),(100,100),(0,100),(100,0)], close=True)
    assembly = dxf_to_assembly(encoded(doc))
    assert 'self_intersection' in {i['code'] for i in validate_assembly(assembly)}


def test_sewlines():
    raw = dxf_bytes(np.array([[0,0],[100,0],[100,100],[0,100]]))
    panel = dxf_to_assembly(raw, seam_allowance_mm=10)['panels'][0]
    assert panel['sewline_status'] == 'hypothesis_offset'
    assert Polygon(panel['sewline']).area == pytest.approx(6400)
    doc = ezdxf.new()
    doc.units = 4
    doc.modelspace().add_lwpolyline([(0,0),(100,0),(100,100),(0,100)], close=True)
    doc.modelspace().add_lwpolyline([(10,10),(90,10),(90,90),(10,90)], close=True, dxfattribs={'layer':'SEWLINE'})
    assembly = dxf_to_assembly(encoded(doc), seam_allowance_mm=5)
    assert assembly['panels'][0]['sewline_status'] == 'from_dxf'
    assert not [i for i in validate_assembly(assembly) if i['level'] == 'error']


def test_validation():
    assembly = dxf_to_assembly(dxf_bytes(synthetic()))
    bad = copy.deepcopy(assembly)
    bad['panels'][0]['edges'][0]['notches'] = [dict(at_mm=100000, kind='notch')]
    bad['panels'][0]['edges'][0]['length_mm'] += 10
    assert {'notch_range','edge_perimeter'} <= {i['code'] for i in validate_assembly(bad)}
    edge = assembly['panels'][0]['edges'][0]['id']
    candidate = dict(id='s1',a=edge,b='unknown',orientation='unknown',matches=[],construction=None,
                     allowance_mm=None,ease_mm=None,confidence=0,evidence=[])
    assembly['seams'] = [candidate,dict(candidate,id='s2')]
    assert {'seam_reference','edge_reused'} <= {i['code'] for i in validate_assembly(assembly)}
    assert validate_assembly({'schema':'wrong'})[0]['level'] == 'error'


def test_curves_and_transform():
    doc = ezdxf.new()
    doc.units = 4
    block = doc.blocks.new('curve')
    entity = block.add_lwpolyline([(0,0,1),(100,0,0)], format='xyb', close=True)
    doc.modelspace().add_blockref('curve',(20,30))
    assembly = dxf_to_assembly(encoded(doc))
    panel = assembly['panels'][0]
    assert len(panel['cutline']) > 10
    assert panel['provenance']['handle'] == entity.dxf.handle
    assert min(x for x,y in panel['cutline']) == pytest.approx(20)


def test_schema_json():
    schema = json.loads(files('easyon_scan').joinpath('schemas/garment-assembly-v0.schema.json').read_text())
    assert schema['$schema'] == 'https://json-schema.org/draft/2020-12/schema'
    assert schema['properties']['schema']['const'] == 'garment-assembly/v0'


def test_cli(tmp_path, monkeypatch, capsys):
    from easyon_scan.cli import main
    path = tmp_path/'input.dxf'
    out = tmp_path/'assembly.json'
    report = tmp_path/'report.txt'
    path.write_bytes(dxf_bytes(synthetic()))
    monkeypatch.setattr('sys.argv', ['easyon-scan','assembly',str(path),'--out',str(out),'--report',str(report),'--size','M'])
    main()
    assert json.loads(out.read_text())['source']['size'] == 'M'
    assert '2 panels' in capsys.readouterr().out
    assert '0 issues' in report.read_text()


def test_cli_validation_exit(tmp_path, monkeypatch):
    from easyon_scan.cli import main
    doc = ezdxf.new()
    doc.units = 4
    doc.modelspace().add_lwpolyline([(0,0),(100,100),(0,100),(100,0)],close=True)
    path = tmp_path/'bad.dxf'
    path.write_bytes(encoded(doc))
    out = tmp_path/'bad.json'
    monkeypatch.setattr('sys.argv',['easyon-scan','assembly',str(path),'--out',str(out)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert out.exists()
