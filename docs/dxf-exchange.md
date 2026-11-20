# Experimental DXF geometry exchange

`easyon_scan.dxf_adapter` adds APIs without changing the existing CLI:

- `read_dxf_geometry(path_or_bytes, assume_units=None, tolerance_mm=0.1)` returns
  `entities` and a `report` with units, scale to mm, layer/type counts, expanded
  blocks, curve counts and explicit losses.
- `geometry_dxf_bytes(geometry)` exports those entities in mm with layer names.
- `dxf_to_pattern_geometry(..., length_tolerance_mm=1.0)` returns `(assembly_v1, report)` with the optional
  `pattern-geometry/v1` extension. It applies the existing assembly layer policy
  directly from the original DXF; open generic entities do not become standalone panels.
- `pattern_geometry_dxf_bytes(assembly_v1, length_tolerance_mm=1.0)` returns `(bytes, report)` for the
  extension's closed outer/hole contours. Each panel gets a generated piece block with outer layer 1 and hole layer 11. Arbitrary panel IDs never become layer names; block scope keeps holes separate even for overlapping panels.

Unitless DXF requires an explicit assumption. Unknown units and assumptions that
contradict declared units are refused. Supported source units are inches, feet,
mm, cm and metres. Supported geometry is planar LINE, POINT, LWPOLYLINE,
POLYLINE (excluding meshes), ARC, CIRCLE, ELLIPSE and SPLINE. Curves are flattened
at the declared tolerance in final mm, including block magnification. Nested
blocks are expanded; recursive blocks and nonplanar geometry are reported and
omitted. Text, attributes, widths and unsupported entities do not survive as
semantic data. Export uses points and polylines, preserving layer names rather
than original entity classes or block hierarchy.

The v1 conversion is geometry-only: stable JSON IDs, grading, review decisions,
construction, notch and grain metadata are not encoded by its export. Preserve
the source assembly JSON for those fields. The v1 import preserves original SHA, units, explicit unit assumptions, entity handles, and supported text metadata including Size and Fold. Conflicting metadata, multiple sizes, and nested block scopes are refused. Single-level AAMA piece blocks retain their individual scope. Its curve tolerance is the existing assembly reader’s fixed 0.1 mm; the generic reader has a configurable chord tolerance. The exposed `length_tolerance_mm` controls v1 validation, independently of curve flattening. No grading or construction is
inferred. This does not establish general AAMA/ASTM compliance or compatibility
with Audaces or another CAD system.

Evidence: `tests/test_dxf_adapter.py` uses synthetic inputs for mm-scale
round trips within 0.5 mm, including five source units, transformed nested
blocks, curves, and a v1 panel with a hole. These assertions establish only the
tested exchange behavior; they do not validate real-photo measurement accuracy.
