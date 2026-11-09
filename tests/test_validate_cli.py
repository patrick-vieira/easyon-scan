"""Tests for the validate CLI subcommand."""
import json
from pathlib import Path

import pytest

from easyon_scan.assembly import dxf_to_assembly, validate_assembly
from easyon_scan.cli import main
from easyon_scan.dxf import dxf_bytes


def _make_valid_assembly():
    """Create a valid assembly document from a synthetic DXF."""
    import numpy as np
    from easyon_scan.scan import Measurement, Piece
    rectangle = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float)
    piece = Piece(contour_mm=rectangle, holes_mm=[], notches_mm=[], grain_lines_mm=[])
    measurement = Measurement(
        contour_mm=rectangle, area_mm2=10000, width_mm=100, height_mm=100,
        residual_px=0.1, residual_mm=0.1, preview=np.zeros((100, 100, 3), dtype=np.uint8),
        pieces=[piece]
    )
    dxf_data = dxf_bytes(measurement)
    return dxf_to_assembly(dxf_data)


def test_validate_valid_document(tmp_path, monkeypatch, capsys):
    """Test validation of a valid assembly document."""
    assembly = _make_valid_assembly()
    assembly_file = tmp_path / "valid.json"
    assembly_file.write_text(json.dumps(assembly, indent=2), encoding="utf-8")
    
    # Verify the document is actually valid (no errors, but may have warnings like grain_missing)
    issues = validate_assembly(assembly)
    errors = [i for i in issues if i['level'] == 'error']
    assert len(errors) == 0, f"Expected no errors in test document, got: {issues}"
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(assembly_file)])
    main()
    
    captured = capsys.readouterr()
    assert "0 errors" in captured.out
    # The synthetic DXF produces a grain_missing warning, so we expect warnings
    assert "warning" in captured.out.lower()


def test_validate_json_mode_valid(tmp_path, monkeypatch, capsys):
    """Test JSON output mode with a valid document."""
    assembly = _make_valid_assembly()
    assembly_file = tmp_path / "valid.json"
    assembly_file.write_text(json.dumps(assembly, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(assembly_file), "--json"])
    main()
    
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    # Document is valid (no errors) but may have warnings
    assert result["valid"] is True
    # The synthetic DXF produces grain_missing warnings
    assert all(i["level"] != "error" for i in result["issues"])


def test_validate_invalid_json(tmp_path, monkeypatch, capsys):
    """Test validation with invalid JSON file."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(bad_file)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    
    captured = capsys.readouterr()
    assert "Invalid JSON" in captured.out or "json" in captured.out.lower()


def test_validate_invalid_json_json_mode(tmp_path, monkeypatch, capsys):
    """Test JSON output mode with invalid JSON file."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(bad_file), "--json"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["valid"] is False
    assert len(result["issues"]) == 1
    assert result["issues"][0]["level"] == "error"
    assert result["issues"][0]["code"] == "json_parse"


def test_validate_schema_error(tmp_path, monkeypatch, capsys):
    """Test validation with a document that has schema errors."""
    # Create a document with missing required fields
    bad_doc = {"schema": "garment-assembly/v0"}  # Missing many required fields
    
    # Verify this actually produces errors
    issues = validate_assembly(bad_doc)
    errors = [i for i in issues if i['level'] == 'error']
    assert len(errors) > 0, "Test document should produce schema errors"
    
    bad_file = tmp_path / "bad_schema.json"
    bad_file.write_text(json.dumps(bad_doc, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(bad_file)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    
    captured = capsys.readouterr()
    assert "error" in captured.out.lower()
    assert "1 errors" in captured.out or "errors" in captured.out


def test_validate_schema_error_json_mode(tmp_path, monkeypatch, capsys):
    """Test JSON mode with schema errors."""
    bad_doc = {"schema": "garment-assembly/v0"}  # Missing many required fields
    
    bad_file = tmp_path / "bad_schema.json"
    bad_file.write_text(json.dumps(bad_doc, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(bad_file), "--json"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["valid"] is False
    assert len(result["issues"]) > 0
    assert any(i["level"] == "error" for i in result["issues"])


def test_validate_geometric_error(tmp_path, monkeypatch, capsys):
    """Test validation with a document that has geometric errors."""
    # Create a valid assembly first
    assembly = _make_valid_assembly()
    
    # Modify it to create a geometric error (self-intersecting polygon)
    assembly["panels"][0]["cutline"] = [[0, 0], [100, 100], [0, 100], [100, 0], [0, 0]]
    
    # Verify this produces a geometric error
    issues = validate_assembly(assembly)
    errors = [i for i in issues if i['level'] == 'error']
    assert len(errors) > 0, "Test document should produce geometric errors"
    assert any("self_intersection" in i['code'] for i in errors), f"Expected self_intersection error, got: {errors}"
    
    bad_file = tmp_path / "bad_geom.json"
    bad_file.write_text(json.dumps(assembly, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(bad_file)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    
    captured = capsys.readouterr()
    assert "error" in captured.out.lower()


def test_validate_warning_only(tmp_path, monkeypatch, capsys):
    """Test validation with a document that has only warnings (no errors)."""
    # Create a valid assembly
    assembly = _make_valid_assembly()
    
    # Remove grain to create a warning
    assembly["panels"][0]["grain"] = None
    
    # Verify this produces only a warning
    issues = validate_assembly(assembly)
    errors = [i for i in issues if i['level'] == 'error']
    warnings = [i for i in issues if i['level'] == 'warning']
    assert len(errors) == 0, f"Expected no errors, got: {errors}"
    assert len(warnings) > 0, f"Expected warnings, got: {issues}"
    assert any("grain" in i['code'] for i in warnings), f"Expected grain warning, got: {warnings}"
    
    warn_file = tmp_path / "warnings.json"
    warn_file.write_text(json.dumps(assembly, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(warn_file)])
    main()  # Should not raise SystemExit since only warnings
    
    captured = capsys.readouterr()
    assert "0 errors" in captured.out
    assert "1 warnings" in captured.out or "warning" in captured.out.lower()


def test_validate_warning_only_json_mode(tmp_path, monkeypatch, capsys):
    """Test JSON mode with warnings only."""
    assembly = _make_valid_assembly()
    assembly["panels"][0]["grain"] = None  # Remove grain to create warning
    
    warn_file = tmp_path / "warnings.json"
    warn_file.write_text(json.dumps(assembly, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(warn_file), "--json"])
    main()  # Should not raise SystemExit
    
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["valid"] is True  # Valid because no errors
    assert len(result["issues"]) > 0
    assert all(i["level"] == "warning" for i in result["issues"])


def test_validate_exit_code_0_on_warnings(tmp_path, monkeypatch):
    """Test that exit code is 0 when only warnings are present."""
    assembly = _make_valid_assembly()
    assembly["panels"][0]["grain"] = None  # Remove grain to create warning
    
    warn_file = tmp_path / "warnings.json"
    warn_file.write_text(json.dumps(assembly, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(warn_file)])
    
    # Should not raise SystemExit
    main()


def test_validate_exit_code_1_on_errors(tmp_path, monkeypatch):
    """Test that exit code is 1 when errors are present."""
    bad_doc = {"schema": "garment-assembly/v0"}  # Missing required fields
    
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps(bad_doc, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(bad_file)])
    
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_validate_human_output_format(tmp_path, monkeypatch, capsys):
    """Test human-readable output format."""
    assembly = _make_valid_assembly()
    assembly["panels"][0]["grain"] = None  # Add a warning
    
    # Verify the warning exists
    issues = validate_assembly(assembly)
    warnings = [i for i in issues if i['level'] == 'warning']
    assert len(warnings) > 0
    
    warn_file = tmp_path / "warnings.json"
    warn_file.write_text(json.dumps(assembly, indent=2), encoding="utf-8")
    
    monkeypatch.setattr("sys.argv", ["easyon-scan", "validate", str(warn_file)])
    main()
    
    captured = capsys.readouterr()
    # Check format: "level: code (panel): message"
    assert "warning:" in captured.out.lower()
    assert "grain" in captured.out.lower()
