"""DXF inventory diagnostic report for garment-assembly/v0 conversion."""
import base64
import json
import re
from collections import defaultdict
from pathlib import Path

import ezdxf

# Layer classification table consistent with assembly.py
# Maps layer names/numbers to their treatment by the adapter
LAYER_CLASSIFICATION = {
    # cutline: layer 1 and contour layers
    "1": "cutline",
    # sewline: layer 14 and sew-related layers
    "14": "sewline",
    "SEWLINE": "sewline",
    "SEW": "sewline",
    "STITCH": "sewline",
    "STITCHLINE": "sewline",
    # notch: layer 4 and 80-83
    "4": "notch",
    "80": "notch",
    "81": "notch",
    "82": "notch",
    "83": "notch",
    "NOTCH": "notch",
    # grain: layer 7
    "7": "grain",
    "GRAIN": "grain",
    # hole-candidate: layer 11
    "11": "hole-candidate",
    # ignored: layers 8, 13, 15, INTERNAL
    "8": "ignored",
    "13": "ignored",
    "15": "ignored",
    "INTERNAL": "ignored",
}

# Notch type mapping for layers 80-83
NOTCH_TYPES = {
    "NOTCH": "notch",
    "4": "v_slit",
    "80": "t",
    "81": "castle",
    "82": "check",
    "83": "u",
}

# Unit codes and names
UNIT_NAMES = {
    0: "unitless",
    1: "in",
    2: "ft",
    4: "mm",
    5: "cm",
    6: "m",
}

# Recognized text patterns (case-insensitive)
RECOGNIZED_TEXT_PATTERNS = [
    re.compile(r"^\s*(Piece Name)\s*:\s*(.*?)\s*$", re.I),
    re.compile(r"^\s*(Size Name)\s*:\s*(.*?)\s*$", re.I),
    re.compile(r"^\s*(Size)\s*:\s*(.*?)\s*$", re.I),
    re.compile(r"^\s*(Quantity)\s*:\s*(.*?)\s*$", re.I),
    re.compile(r"^\s*(Fold)\s*:\s*(.*?)\s*$", re.I),
]


def classify_layer(layer_name: str) -> tuple:
    """Classify a layer according to assembly.py policy.

    Returns (classification, notch_type) where notch_type is None unless
    the layer is a notch type layer.
    """
    layer_upper = layer_name.upper()
    classification = LAYER_CLASSIFICATION.get(layer_upper, "unknown")
    notch_type = None
    if classification == "notch" and layer_upper in NOTCH_TYPES:
        notch_type = NOTCH_TYPES[layer_upper]
    return classification, notch_type


def analyze_dxf(path_or_bytes) -> dict:
    """Analyze a DXF file and return diagnostic information.

    Args:
        path_or_bytes: Path to DXF file or bytes content

    Returns:
        Dictionary with diagnostic report
    """
    if isinstance(path_or_bytes, bytes):
        raw = path_or_bytes
    else:
        raw = Path(path_or_bytes).read_bytes()

    doc = ezdxf.decode_base64(base64.b64encode(raw))

    # 1. Units
    insunits_code = int(doc.header.get("$INSUNITS", 0))
    units_name = UNIT_NAMES.get(insunits_code, "unknown")

    # 2. Blocks referenced by INSERT in modelspace
    block_insertions = defaultdict(int)
    for entity in doc.modelspace():
        if entity.dxftype() == "INSERT":
            block_name = entity.dxf.name
            count = entity.mcount if entity.mcount > 1 else 1
            block_insertions[block_name] += count

    # 3. Entity counts by layer and type
    # Also track closed vs open polylines/splines
    layer_entities = defaultdict(lambda: defaultdict(int))
    layer_closed_open = defaultdict(lambda: {"closed": 0, "open": 0})

    # 4. Block text analysis
    block_texts = defaultdict(lambda: {"recognized": {}, "other": 0})

    # 5. Layer classification summary
    layer_classification_counts = defaultdict(lambda: defaultdict(int))

    def is_closed_entity(entity) -> bool:
        """Check if entity is closed."""
        kind = entity.dxftype()
        if kind == "CIRCLE":
            return True
        if kind in ("LWPOLYLINE", "POLYLINE"):
            return bool(entity.closed)
        if kind == "SPLINE":
            return bool(entity.closed)
        return False

    def analyze_space(space, scope="model"):
        """Analyze entities in a space (modelspace or block)."""
        for entity in space:
            kind = entity.dxftype()

            if kind == "INSERT":
                # Process block contents
                for insert in entity.multi_insert() if entity.mcount > 1 else [entity]:
                    analyze_space(insert.block(), f"insert:{insert.dxf.handle}")
                continue

            if kind in ("TEXT", "MTEXT"):
                value = entity.dxf.text if kind == "TEXT" else entity.plain_text()
                recognized = False
                for pattern in RECOGNIZED_TEXT_PATTERNS:
                    match = pattern.match(value)
                    if match:
                        key = match.group(1).lower()
                        val = match.group(2)
                        block_texts[scope]["recognized"][key] = val
                        recognized = True
                        break
                if not recognized:
                    block_texts[scope]["other"] += 1
                continue

            # Count entity types by layer
            layer = entity.dxf.layer
            layer_entities[layer][kind] += 1

            # Track closed/open for polylines/splines and circles
            if kind in ("LWPOLYLINE", "POLYLINE", "SPLINE"):
                if is_closed_entity(entity):
                    layer_closed_open[layer]["closed"] += 1
                else:
                    layer_closed_open[layer]["open"] += 1
            elif kind == "CIRCLE":
                layer_closed_open[layer]["closed"] += 1

            # Classify layer
            classification, _ = classify_layer(layer)
            layer_classification_counts[layer][classification] += 1

    analyze_space(doc.modelspace())

    # Build the report
    report = {
        "units": {
            "code": insunits_code,
            "name": units_name,
        },
        "blocks": {
            name: count for name, count in block_insertions.items()
        },
        "layers": {},
        "block_texts": {},
        "layer_classification": {},
    }

    # Layer entity details
    all_layers = set(layer_entities.keys()) | set(layer_closed_open.keys())
    for layer in all_layers:
        report["layers"][layer] = {
            "entities": dict(layer_entities[layer]),
            "closed_vs_open": dict(layer_closed_open[layer]) if layer in layer_closed_open else {"closed": 0, "open": 0},
        }

    # Block texts (only scopes with recognized texts or other texts)
    for scope, data in block_texts.items():
        if data["recognized"] or data["other"] > 0:
            report["block_texts"][scope] = {
                "recognized": data["recognized"],
                "other_count": data["other"],
            }

    # Layer classification summary
    for layer in all_layers:
        classification, notch_type = classify_layer(layer)
        if classification == "unknown" and layer_closed_open[layer]["closed"]:
            # assembly.py turns closed contours on any other layer into cutlines.
            classification = "cutline"
        report["layer_classification"][layer] = {
            "classification": classification,
            "notch_type": notch_type,
        }

    return report


def format_report(report: dict) -> str:
    """Format report as human-readable text."""
    lines = []

    # Units
    lines.append("=" * 60)
    lines.append("DXF INVENTORY REPORT")
    lines.append("=" * 60)
    lines.append("")
    lines.append("UNITS")
    lines.append("-" * 40)
    lines.append(f"  $INSUNITS code: {report['units']['code']}")
    lines.append(f"  Name: {report['units']['name']}")
    lines.append("")

    # Blocks
    lines.append("BLOCKS REFERENCED BY INSERT")
    lines.append("-" * 40)
    if report["blocks"]:
        for name, count in sorted(report["blocks"].items()):
            lines.append(f"  {name}: {count} insertion(s)")
    else:
        lines.append("  (none)")
    lines.append("")

    # Layer classification
    lines.append("LAYER CLASSIFICATION (per assembly.py policy)")
    lines.append("-" * 40)
    if report["layer_classification"]:
        for layer in sorted(report["layer_classification"].keys()):
            info = report["layer_classification"][layer]
            notch_info = f" (type: {info['notch_type']})" if info["notch_type"] else ""
            lines.append(f"  Layer '{layer}': {info['classification']}{notch_info}")
    else:
        lines.append("  (none)")
    lines.append("")

    # Entities by layer
    lines.append("ENTITIES BY LAYER")
    lines.append("-" * 40)
    if report["layers"]:
        for layer in sorted(report["layers"].keys()):
            info = report["layers"][layer]
            lines.append(f"  Layer '{layer}':")
            if info["entities"]:
                for etype, count in sorted(info["entities"].items()):
                    lines.append(f"    {etype}: {count}")
            else:
                lines.append("    (no entities)")
            if info["closed_vs_open"]["closed"] > 0 or info["closed_vs_open"]["open"] > 0:
                lines.append(f"    Closed polylines/splines: {info['closed_vs_open']['closed']}")
                lines.append(f"    Open polylines/splines: {info['closed_vs_open']['open']}")
    else:
        lines.append("  (none)")
    lines.append("")

    # Block texts
    lines.append("TEXTS IN BLOCKS")
    lines.append("-" * 40)
    if report["block_texts"]:
        for scope in sorted(report["block_texts"].keys()):
            info = report["block_texts"][scope]
            lines.append(f"  Scope '{scope}':")
            if info["recognized"]:
                lines.append("    Recognized texts:")
                for key, value in sorted(info["recognized"].items()):
                    lines.append(f"      {key}: {value}")
            lines.append(f"    Other texts: {info['other_count']}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def inventory(path_or_bytes, json_output: bool = False) -> str:
    """Generate DXF inventory report.

    Args:
        path_or_bytes: Path to DXF file or bytes content
        json_output: If True, return JSON; otherwise human-readable text

    Returns:
        Report as string (JSON or formatted text)
    """
    report = analyze_dxf(path_or_bytes)
    if json_output:
        return json.dumps(report, indent=2, ensure_ascii=False)
    return format_report(report)
