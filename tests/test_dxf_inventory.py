"""Tests for dxf_inventory module."""
import tempfile
import pytest
import ezdxf
from pathlib import Path

from easyon_scan.dxf_inventory import (
    analyze_dxf,
    classify_layer,
    inventory,
    LAYER_CLASSIFICATION,
    NOTCH_TYPES,
    UNIT_NAMES,
)


class TestClassifyLayer:
    """Test layer classification function."""

    def test_cutline_layer_1(self):
        classification, notch_type = classify_layer("1")
        assert classification == "cutline"
        assert notch_type is None

    def test_sewline_layer_14(self):
        classification, notch_type = classify_layer("14")
        assert classification == "sewline"
        assert notch_type is None

    def test_sewline_text_layers(self):
        for layer in ["SEWLINE", "SEW", "STITCH", "STITCHLINE"]:
            classification, _ = classify_layer(layer)
            assert classification == "sewline"

    def test_notch_layer_4(self):
        classification, notch_type = classify_layer("4")
        assert classification == "notch"
        assert notch_type == "v_slit"

    def test_notch_layers_80_83(self):
        for layer, expected_type in NOTCH_TYPES.items():
            classification, notch_type = classify_layer(layer)
            assert classification == "notch"
            assert notch_type == expected_type

    def test_notch_text_layer(self):
        classification, _ = classify_layer("NOTCH")
        assert classification == "notch"

    def test_grain_layer_7(self):
        classification, _ = classify_layer("7")
        assert classification == "grain"

    def test_grain_text_layer(self):
        classification, _ = classify_layer("GRAIN")
        assert classification == "grain"

    def test_hole_candidate_layer_11(self):
        classification, _ = classify_layer("11")
        assert classification == "hole-candidate"

    def test_ignored_layers(self):
        for layer in ["8", "13", "15", "INTERNAL"]:
            classification, _ = classify_layer(layer)
            assert classification == "ignored"

    def test_unknown_layer(self):
        classification, _ = classify_layer("UNKNOWN_LAYER")
        assert classification == "unknown"

    def test_case_insensitive(self):
        classification, _ = classify_layer("sewline")
        assert classification == "sewline"
        classification, _ = classify_layer("Grain")
        assert classification == "grain"


class TestAnalyzeDXF:
    """Test DXF analysis with synthetic files."""

    def test_unitless_dxf(self, tmp_path):
        """Test DXF without $INSUNITS explicitly set to 0."""
        doc = ezdxf.new()
        # Explicitly set $INSUNITS to 0 for unitless
        doc.header["$INSUNITS"] = 0
        dxf_path = tmp_path / "unitless.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        assert report["units"]["code"] == 0
        assert report["units"]["name"] == "unitless"

    def test_mm_units_dxf(self, tmp_path):
        """Test DXF with millimeter units."""
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 4  # mm
        dxf_path = tmp_path / "mm.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        assert report["units"]["code"] == 4
        assert report["units"]["name"] == "mm"

    def test_all_unit_codes(self, tmp_path):
        """Test all supported unit codes."""
        for code, expected_name in UNIT_NAMES.items():
            doc = ezdxf.new()
            doc.header["$INSUNITS"] = code
            dxf_path = tmp_path / f"units_{code}.dxf"
            doc.saveas(dxf_path)

            report = analyze_dxf(dxf_path)
            assert report["units"]["code"] == code
            assert report["units"]["name"] == expected_name

    def test_unknown_unit_code(self, tmp_path):
        """Test DXF with unknown unit code."""
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 99  # Unknown
        dxf_path = tmp_path / "unknown_units.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        assert report["units"]["code"] == 99
        assert report["units"]["name"] == "unknown"

    def test_block_insertions(self, tmp_path):
        """Test detection of block insertions."""
        doc = ezdxf.new()

        # Create a block
        block = doc.blocks.new("PIECE_BLOCK")
        block.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)

        # Insert block twice
        doc.modelspace().add_blockref("PIECE_BLOCK", (0, 0))
        doc.modelspace().add_blockref("PIECE_BLOCK", (200, 0))

        dxf_path = tmp_path / "blocks.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        assert report["blocks"]["PIECE_BLOCK"] == 2

    def test_layer_entity_counts(self, tmp_path):
        """Test entity counting by layer."""
        doc = ezdxf.new()

        # Add entities to different layers
        doc.modelspace().add_lwpolyline(
            [(0, 0), (100, 0), (100, 100), (0, 100)],
            close=True,
            dxfattribs={"layer": "1"}
        )
        doc.modelspace().add_lwpolyline(
            [(0, 0), (50, 0), (50, 50)],
            close=False,
            dxfattribs={"layer": "1"}
        )
        doc.modelspace().add_line(
            (0, 0), (100, 100),
            dxfattribs={"layer": "14"}
        )

        dxf_path = tmp_path / "layers.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        assert report["layers"]["1"]["entities"]["LWPOLYLINE"] == 2
        assert report["layers"]["1"]["closed_vs_open"]["closed"] == 1
        assert report["layers"]["1"]["closed_vs_open"]["open"] == 1
        assert report["layers"]["14"]["entities"]["LINE"] == 1

    def test_recognized_texts(self, tmp_path):
        """Test recognition of specific text patterns."""
        doc = ezdxf.new()

        # Create a block with recognized texts
        block = doc.blocks.new("PIECE_A")
        block.add_text("Piece Name: Front Panel", dxfattribs={"layer": "0"})
        block.add_text("Size Name: M", dxfattribs={"layer": "0"})
        block.add_text("Quantity: 2", dxfattribs={"layer": "0"})
        block.add_text("Fold: Y", dxfattribs={"layer": "0"})

        # Add some other text
        block.add_text("Some other text", dxfattribs={"layer": "0"})

        doc.modelspace().add_blockref("PIECE_A", (0, 0))

        dxf_path = tmp_path / "texts.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        scope = list(report["block_texts"].keys())[0]
        texts = report["block_texts"][scope]

        assert texts["recognized"]["piece name"] == "Front Panel"
        assert texts["recognized"]["size name"] == "M"
        assert texts["recognized"]["quantity"] == "2"
        assert texts["recognized"]["fold"] == "Y"
        assert texts["other_count"] == 1

    def test_layer_classification_summary(self, tmp_path):
        """Test layer classification in report."""
        doc = ezdxf.new()

        # Add entities to various layers
        for layer in ["1", "14", "4", "80", "7", "8", "13"]:
            doc.modelspace().add_line(
                (0, 0), (10, 10),
                dxfattribs={"layer": layer}
            )

        dxf_path = tmp_path / "classification.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)

        assert report["layer_classification"]["1"]["classification"] == "cutline"
        assert report["layer_classification"]["14"]["classification"] == "sewline"
        assert report["layer_classification"]["4"]["classification"] == "notch"
        assert report["layer_classification"]["80"]["classification"] == "notch"
        assert report["layer_classification"]["80"]["notch_type"] == "t"
        assert report["layer_classification"]["7"]["classification"] == "grain"
        assert report["layer_classification"]["8"]["classification"] == "ignored"
        assert report["layer_classification"]["13"]["classification"] == "ignored"

    def test_mtext_recognition(self, tmp_path):
        """Test MTEXT recognition."""
        doc = ezdxf.new()

        block = doc.blocks.new("PIECE_B")
        block.add_mtext("Piece Name: Back Panel", dxfattribs={"layer": "0"})
        block.add_mtext("Size: L", dxfattribs={"layer": "0"})

        doc.modelspace().add_blockref("PIECE_B", (0, 0))

        dxf_path = tmp_path / "mtext.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        scope = list(report["block_texts"].keys())[0]
        texts = report["block_texts"][scope]

        assert texts["recognized"]["piece name"] == "Back Panel"
        assert texts["recognized"]["size"] == "L"

    def test_closed_splines(self, tmp_path):
        """Test closed spline detection."""
        doc = ezdxf.new()

        # Create a closed spline
        spline = doc.modelspace().add_spline(dxfattribs={"layer": "1"})
        spline.fit_points = [(0, 0), (50, 50), (100, 0)]
        spline.dxf.flags = spline.dxf.flags | 1  # Closed flag

        # Create an open spline
        spline2 = doc.modelspace().add_spline(dxfattribs={"layer": "1"})
        spline2.fit_points = [(0, 0), (50, 50), (100, 0)]

        dxf_path = tmp_path / "splines.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        assert report["layers"]["1"]["closed_vs_open"]["closed"] == 1
        assert report["layers"]["1"]["closed_vs_open"]["open"] == 1

    def test_circle_as_closed(self, tmp_path):
        """Test that circles are counted as closed."""
        doc = ezdxf.new()
        doc.modelspace().add_circle((50, 50), 25, dxfattribs={"layer": "1"})

        dxf_path = tmp_path / "circle.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)
        assert report["layers"]["1"]["closed_vs_open"]["closed"] == 1


class TestInventoryOutput:
    """Test inventory output formats."""

    def test_human_readable_output(self, tmp_path):
        """Test human-readable output format."""
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 4
        doc.modelspace().add_line((0, 0), (100, 100), dxfattribs={"layer": "1"})

        dxf_path = tmp_path / "output.dxf"
        doc.saveas(dxf_path)

        result = inventory(dxf_path, json_output=False)
        assert "DXF INVENTORY REPORT" in result
        assert "UNITS" in result
        assert "$INSUNITS code: 4" in result
        assert "mm" in result
        assert "LAYER CLASSIFICATION" in result
        assert "ENTITIES BY LAYER" in result

    def test_json_output(self, tmp_path):
        """Test JSON output format."""
        import json

        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 4
        doc.modelspace().add_line((0, 0), (100, 100), dxfattribs={"layer": "1"})

        dxf_path = tmp_path / "output.dxf"
        doc.saveas(dxf_path)

        result = inventory(dxf_path, json_output=True)
        data = json.loads(result)
        assert data["units"]["code"] == 4
        assert data["units"]["name"] == "mm"
        assert "layers" in data
        assert "layer_classification" in data

    def test_bytes_input(self):
        """Test analyzing DXF from bytes."""
        import tempfile
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 4
        doc.modelspace().add_line((0, 0), (100, 100), dxfattribs={"layer": "1"})

        # Save to bytes via temporary file
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            doc.saveas(f.name)
            dxf_bytes = Path(f.name).read_bytes()

        report = analyze_dxf(dxf_bytes)
        assert report["units"]["code"] == 4
        assert "layers" in report


class TestComprehensiveDXF:
    """Test with a comprehensive DXF covering all layer types."""

    def test_full_dxf(self, tmp_path):
        """Test a DXF with all layer types and text patterns."""
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = 4  # mm

        # Create a piece block
        block = doc.blocks.new("FRONT")

        # Cutline (layer 1) - closed polyline
        block.add_lwpolyline(
            [(0, 0), (100, 0), (100, 150), (0, 150)],
            close=True,
            dxfattribs={"layer": "1"}
        )

        # Sewline (layer 14)
        block.add_lwpolyline(
            [(5, 5), (95, 5), (95, 145), (5, 145)],
            close=True,
            dxfattribs={"layer": "14"}
        )

        # Notch (layer 4)
        block.add_point((50, 0), dxfattribs={"layer": "4"})

        # Notch type t (layer 80)
        block.add_point((0, 75), dxfattribs={"layer": "80"})

        # Grain (layer 7)
        block.add_line((45, 70), (55, 80), dxfattribs={"layer": "7"})

        # Hole candidate (layer 11)
        block.add_circle((50, 120), 5, dxfattribs={"layer": "11"})

        # Ignored layers (8, 13)
        block.add_point((10, 10), dxfattribs={"layer": "8"})
        block.add_point((20, 20), dxfattribs={"layer": "13"})

        # Recognized texts
        block.add_text("Piece Name: Front Panel", dxfattribs={"layer": "0"})
        block.add_text("Size Name: M", dxfattribs={"layer": "0"})
        block.add_text("Quantity: 2", dxfattribs={"layer": "0"})
        block.add_text("Fold: Y", dxfattribs={"layer": "0"})

        # Other text
        block.add_text("Some note", dxfattribs={"layer": "0"})

        # Insert the block
        doc.modelspace().add_blockref("FRONT", (0, 0))

        dxf_path = tmp_path / "full.dxf"
        doc.saveas(dxf_path)

        report = analyze_dxf(dxf_path)

        # Check units
        assert report["units"]["code"] == 4
        assert report["units"]["name"] == "mm"

        # Check blocks
        assert report["blocks"]["FRONT"] == 1

        # Check layer classification
        assert report["layer_classification"]["1"]["classification"] == "cutline"
        assert report["layer_classification"]["14"]["classification"] == "sewline"
        assert report["layer_classification"]["4"]["classification"] == "notch"
        assert report["layer_classification"]["80"]["classification"] == "notch"
        assert report["layer_classification"]["80"]["notch_type"] == "t"
        assert report["layer_classification"]["7"]["classification"] == "grain"
        assert report["layer_classification"]["11"]["classification"] == "hole-candidate"
        assert report["layer_classification"]["8"]["classification"] == "ignored"
        assert report["layer_classification"]["13"]["classification"] == "ignored"

        # Check entity counts
        assert report["layers"]["1"]["entities"]["LWPOLYLINE"] == 1
        assert report["layers"]["1"]["closed_vs_open"]["closed"] == 1
        assert report["layers"]["14"]["entities"]["LWPOLYLINE"] == 1
        assert report["layers"]["4"]["entities"]["POINT"] == 1
        assert report["layers"]["80"]["entities"]["POINT"] == 1
        assert report["layers"]["7"]["entities"]["LINE"] == 1
        assert report["layers"]["11"]["entities"]["CIRCLE"] == 1

        # Check texts
        scope = list(report["block_texts"].keys())[0]
        texts = report["block_texts"][scope]
        assert texts["recognized"]["piece name"] == "Front Panel"
        assert texts["recognized"]["size name"] == "M"
        assert texts["recognized"]["quantity"] == "2"
        assert texts["recognized"]["fold"] == "Y"
        assert texts["other_count"] == 1
