# Garment assembly v1 (experimental)

`garment-assembly/v1` is a separate planar contract. Call `migrate_v0` explicitly on pending v0 with no confirmed seams or openings. The migrator copies the source, creates `role: unknown`, nullable semantic labels and sewline intervals, and keeps legacy proposals as immutable snapshots with a content hash. Exact duplicate match coordinates in legacy proposals are collapsed without changing their correspondence. It never approves a seam. v0 and its CLI remain available.

All `segment_ref` endpoints are local cutline arc lengths in millimetres. A measured sewline interval is only valid with `sewline_status: from_dxf`; it maps proportionally to the edge interval. This approximation needs human inspection, especially on curved or offset geometry. Confirmed plain and gather pairs require measured intervals, declared signed ease (`a - b`), a known orientation, at least two monotonic matches, and an explicit `length_tolerance_mm` passed to validation. Proposals may remain uncertain. A content hash detects accidental edits to a proposal; review decisions carry its original hash, reviewer, timestamp and reason. An accepted seam must equal the proposal in a/b segments, orientation, matches, construction, allowance and ease. A deliberate edit requires a complete `after` pair snapshot on the accept decision; the original hash stays unchanged. Only one decision per proposal, operation or panel is allowed. `confirm_operation` and `close_panel` reference existing `operation_id` and `panel_id` respectively and carry no proposal hash. Hashes do not authenticate a person. Human approval requires a `close_panel` decision for every whole panel. The review workflow must check destination completeness before closing; the validator does not infer intended free edges.

`operations` reserves `dart`, `pleat`, `gather`, `hem`, `binding`, `zipper`, `lining_join`, `fold`. Only `hem` and `fold` segment reservation and decision checks are implemented. The others are rejected by validation until their geometry and construction rules are implemented. `gather` is supported as a confirmed pair construction with positive signed ease, not as a standalone operation. Openings remain an unconfirmed v0 legacy shape in this initial contract and cannot represent a reviewed opening yet. No simulation, grading or 3D output is provided.

`evaluate_proposals` compares ranked segment pairs with a blind gabarito using caller-declared endpoint tolerance. A candidate counts as top-1 when it ranks first at either participating edge (`a` or `b`; each edge/start/end interval has its own ranking); top-3 uses the same either-participating-edge rule. Ranking sorts by descending score, then ascending candidate ID to break ties. Each candidate can match at most one gabarito pair when counting geometry, orientation, internal matches and construction. Internal matches use the same millimetre tolerance, and swapping A/B requires swapping the match columns; the orientation relation stays the same. The orientation and construction error counts are differences between maximum matching counts at each correctness level; those maxima may use different candidate-to-gabarito assignments. It reports top-1 precision, top-3 recall, numerators, denominators, orientation, internal match and construction errors, and median/p95 review time. Empty denominators yield null rates. These synthetic contract metrics establish no real-photo or CAD-system accuracy; see [precision notes](precision-notes.md) and [benchmark](benchmark.md).

## Optional point geometry (SCAN-33, experimental)

The additive `pattern_geometry` field has its own `schema: pattern-geometry/v1`
and `units: mm`. Existing v1 documents remain valid without it. Its panels refer
to the common v1 panel IDs. Each contains points (`id`, `xy_mm`, `kind`,
`grading`), straight edges linked through `start_point_id` / `end_point_id`, and
ordered outer/hole contours with point and edge IDs. The closing point is not
repeated: the final edge must link back to the first point. Entity IDs are unique
across the extension; references are resolved within their panel. `kind` on a
point is `corner`, `curve`, or `unknown`; it is metadata on the sampled polyline,
not a spline representation. `source_edge_id` links a sampled straight segment
to its common v1 edge, or is null for holes or newly authored edges.

Piques are `notches` with ID, edge ID, `at_mm` measured from its start point and
a declared v0 kind (`notch`, `v_slit`, `t`, `castle`, `check`, `u`). `grain` is a nonzero direction vector or null for unknown.
`fold_edge_ids` is null when the fold location is unknown, an empty list for no
fold, or a list of distinct declared outer-contour edge IDs. Hole edges cannot serve
as fold edges or reference an outer common v1 source edge. Each edge has `allowance_mm`: null means
unknown, zero means a known zero allowance, and a positive number is a declared
allowance. Each point has zero or more grading rules of the form
`{"size": "L", "delta_mm": [2, -3]}`. An empty rule list means no declared
rule; it never implies zero movement. Size labels must be unique per point.
Rules are stored only; this slice does not apply grading or produce 3D geometry.

`easyon_scan.assembly_geometry.add_pattern_geometry(v1, length_tolerance_mm=1.0)` creates a deep copy,
retaining all common v1 data. It samples the existing outer/hole polylines,
transfers source edge references, piques and grain, and leaves point types,
allowances and grading unknown. A common `on_fold: true` yields an unknown fold
location rather than guessing an edge. Existing extension data is rejected to
prevent silent replacement. The optional length tolerance is forwarded to v1
validation and defaults to 1 mm. Piques at a source edge endpoint stay on that
source edge; an internal sampled boundary belongs to the following segment.
A fixed 1e-8 mm numerical comparison snaps nearly equal sampled endpoints;
this is separate from the declared seam length tolerance. `remove_pattern_geometry(document)` deep-copies and
drops only this field: extension edits are deliberately not written back into
legacy geometry or reviewed proposal snapshots. Thus common v1 fields survive
the round trip exactly, but extension-only fields are lost on removal.

`validate_assembly_v1` checks the optional extension structure and reports
`panel` and `entity_id` on semantic geometry errors: open contours, duplicate
IDs, invalid references, zero edges, self-intersections, invalid hole placement,
piques outside edges and repeated grading sizes. Geometry edits are a separate
experimental representation and are not a reviewed manufacturing instruction.
The evidence is synthetic contract tests in `tests/test_assembly_geometry.py`;
no real-photo accuracy or CAD interoperability is established.

The converter clamps each pique's declared global arc position to the measured
vertex interval of its own source edge, accommodating the v0 validator's
perimeter-based arc/vertex tolerance without transferring a pique to a neighbour.
It preserves pique count exactly or raises `ValueError`; internal sampled
boundaries belong to the following segment, while a source edge endpoint stays
on that source's final segment. Floating boundary differences up to `1e-8` mm
are snapped. Sampled edge lengths at or below `1e-8` mm are rejected both during
conversion and extension validation. These are numerical contract thresholds,
not claims of physical measurement precision.

The v0 adapter uses one accumulated perimeter for both vertex positions and edge
cuts. Projected piques snap to existing arc positions within eight ULPs of the
perimeter (at least `1e-12` mm), retaining exact original corner coordinates and
avoiding duplicate closure/corner vertices from floating arithmetic. Extension
pique offsets clamp against the segment's direct Euclidean length, the same
quantity used by validation. A seeded regression covers 200 synthetic noninteger
quadrilaterals with corner piques and final-edge endpoint piques.

### MoldeGeometria projection (SCAN-33)

`add_molde_geometry(v1)` attaches one AK DEV-2063 scan geometry to each
`pattern_geometry.panels[].molde_geometria`. Each projection is a
`{unidade, origem, margem_conhecida, dados}` object derived from the original
v1 panel and source. Export that object by itself for AK's
`validar(objeto["dados"])` or `para_garment_assembly(objeto)`. AK's
`de_garment_assembly(v1, painel=...)` instead takes a v1 assembly and selects
one panel in a multi-panel document.

Coordinates are in millimetres with y up. The projection keeps source edge
IDs and puts intermediate cutline vertices in `curva.pontos`. It carries
notch positions and original kinds, label, direction of grain, fold flag,
unclosed hole and sewline rings, quantity text and source size. Panel and
edge provenance, original labels, sewline intervals and assembly source are
in `dados.scan`. A label also supplies the edge `tipo` when it exactly
matches an AK edge type. Other labels use `tipo: livre` and retain the
original text. A notch at the end of the closing edge stays on that edge.
If an edge ID matches a generated point ID such as `P1`, the projection
adds underscores to the point ID until it is distinct. The AK converter
rejects that collision when converting directly from v1.
The original v1 panels, review, construction data and
Scan point geometry remain in the assembly. `margem_conhecida` is false
because the source contour is the cutline. Scan extension allowances and
grading stay in `pattern_geometry`.

An external check used the synthetic AK fixture `garment_assembly_v1_exemplo.json` and
the DEV-2063 converter at `f1bc3082b0932dd3170f66a908316f2555ff70af` with an
absolute numeric tolerance of 1e-9. The AK validator accepted its geometry
and the AK reverse converter recovered the fixture panel. The Scan suite
uses local synthetic inputs to check the field mappings. Interoperability was
observed for that fixture and does not establish compatibility across all
Scan inputs. AK produces a
new single-panel assembly pending review and recalculates edge distances.
The projection normalizes the grain direction to build a 100 mm segment and
uses an empty grade when the source size is blank. The original vector and
size stay in the v1 data. AK also
requires consecutive edge indices and at least three source edges, and
can reject notches accepted by Scan's looser arc-length tolerance.

`add_molde_geometry(v1, profile="provisional")` continues to write the
legacy `scan-molde/v1-provisional` format. The standalone legacy
`to_molde_geometria(geometry_panel)` and `from_molde_geometria(molde)`
functions still round-trip that format. Both formats can be validated in
existing assemblies. `remove_molde_geometry` removes either projection only.
Use `remove_pattern_geometry` before passing a full assembly to older strict
v1 validators that lack the extension. v0 requires explicit `migrate_v0`.
