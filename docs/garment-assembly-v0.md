# Garment assembly (experimental, v0)

`garment-assembly/v0` is an explicit planar intermediate format between DXF
and future assembly tools. Nothing here simulates cloth, drapes garments or
creates 3D geometry. The agent only **proposes** seams; a human must confirm
construction, pairing, orientation, allowance and ease. The adapter always emits
`review.status: "pending"`, empty `seams`, and empty `openings`. Similar edge
lengths are not evidence of an intended seam. There is no implicit `plain`
construction and no automatic approval.

## Usage

```sh
easyon-scan assembly pattern.dxf --out assembly.json --size M --report report.txt
# Only for a DXF whose unit header is unitless:
easyon-scan assembly pattern.dxf --assume-units cm --out assembly.json
# An explicit geometric hypothesis, not a recovered construction line:
easyon-scan assembly pattern.dxf --out assembly.json --seam-allowance-mm 10
```

Python: `dxf_to_assembly(path_or_bytes, *, size=None, assume_units=None,
seam_allowance_mm=None)` and `validate_assembly(doc)` in `easyon_scan.assembly`.
The CLI writes JSON even when geometry validation reports errors, prints counts,
and exits 1 for validation errors (0 with warnings only). Input/read errors exit
2. The optional UTF-8 report includes counts and each diagnostic. The importer
never modifies the source file.

## Coordinates and source

All coordinates, arc lengths, allowances and ease are millimetres in the DXF XY
frame, with no normalization or axis flip. This adapter expects planar geometry;
3D entities and nonplanar inputs are outside v0's supported contract.
`source.file_sha256` hashes the original bytes. `source.units` records the unit
read from `$INSUNITS`: `in` (1), `ft` (2), `mm` (4), `cm` (5), `m` (6), or
`unitless` (0). Factors to mm are respectively 25.4, 304.8, 1, 10 and 1000.
Unitless input requires `assume_units`; the declaration is retained separately
as `source.assumed_units`, while `insunits_code` remains 0. Other codes fail.
`source.size` is the selected label, the common block `Size Name:` or legacy
`Size:` label, or null. Different size labels fail with "one size per file in
v0" unless `--size` selects one; that option includes matching block scopes
and excludes other labeled sizes. Unlabeled scopes remain included. This is
only a block selection, not grading or interpretation of external rule files.

The packaged [JSON Schema](../easyon_scan/schemas/garment-assembly-v0.schema.json)
uses draft 2020-12. Required fields and structures are defined there. IDs are
stable for a given entity traversal, not across edits to the original DXF.

## Panels and provenance

Each panel has `id`, `name`, `cutline`, nullable `sewline`, `sewline_status`,
nullable `grain`, boolean `on_fold`, `holes`, `edges`, and `provenance`.
`quantity` is an optional verbatim string from `Quantity:`; it does not infer
cut counts or left/right semantics.
Rings repeat the first `[x,y]` point at the end. Holes are rings inside the
outer ring, independent of winding. `grain` is a unit direction `[dx,dy]`;
its sign has no construction meaning. Missing grain is null. `on_fold` is true
only for `Fold:Y` text in the piece scope; layer 6 and geometric symmetry do
not imply a fold. `provenance` contains original DXF `handle`, `layer`, and
`dxftype`; edges retain their outline entity's provenance.

Plain exports use closed LWPOLYLINEs per outline layer, same-layer holes,
NOTCH triangles and GRAIN ink polygons. INTERNAL lines do not become seams.
AAMA-style exports use inserted piece BLOCKs, closed POLYLINEs on layer 1,
holes on 11, notch POINTs on 4, grain LINEs on 7 and `Piece Name:`/`Size:` text.
Block insertion transforms are applied and original block entity handles are
retained (handles can therefore repeat across block instances). Nested INSERTs
are rejected. Generic closed LWPOLYLINEs, POLYLINEs, SPLINEs and CIRCLEs are
supported. Open geometry is ignored, except recognized annotations. Arc and
bulge segments within closed paths are flattened with ezdxf at **0.1 mm**
chord-distance tolerance in source coordinates converted to mm; transformed
block scaling can scale this tolerance. Standalone open ARC/SPLINE curves are
not stitched into closed outlines. Nested islands and overlapping panels require
manual cleanup; containment alone is not a complete CAD topology interpreter.

Notches in plain exports are located at the midpoint of the triangle's first
and third vertices. AAMA notch points are used directly. Marks are assigned to
the nearest outline in the same block scope, then projected to its perimeter;
projection does not establish that the symbol was intended for that piece.
Grain marks must start inside their panel; the longest point pair provides the
axis. Multiple recognized grain marks use the last direction encountered.

A separate contained closed contour on `SEWLINE`, `SEW`, `STITCH` or `STITCHLINE`
is used as `from_dxf`. More than one candidate fails rather than picking one.
Otherwise an explicitly requested positive allowance produces a Shapely inward
buffer with mitre joins, marked `hypothesis_offset`; empty/disconnected offsets
fail. Its exterior is the hypothetical sewline; hole allowances are not modeled.
Without either source, sewline is null and status is `missing`.

## ASTM-style layer and text policy

| Input in a piece block | v0 result / loss |
| --- | --- |
| Closed contour on 1 | Cutline and edges; geometry flattened at 0.1 mm. |
| Closed contour on 14 | `sewline` with `from_dxf`; open lines are not joined. |
| Pique on 4, 80–83 | Position and type retained; depth, width and angle are lost. |
| LINE on 7 | Grain direction; other grain semantics are lost. |
| `Piece Name:`, `Size Name:`, `Quantity:`, `Fold:Y` | Name, size, verbatim quantity and fold flag. Other system text is ignored. |
| 8, 13, 15 | Internal markings, drill points and plot annotations are ignored. |
| Closed contour on 11 | Treated as a hole when contained by a cutline in the same block; other 11 geometry has no supported meaning. |

The input DXF remains unchanged. Keep it for review because ignored geometry,
notch dimensions and grading data cannot be recovered from the v0 JSON.

## Edges, seams and review

Edges split at turns of **30 degrees or greater**, at outline origin and at each
projected notch. Smooth flattened curve samples do not individually imply seams.
`from_mm` and `to_mm` are increasing arc-length positions from the first cutline
vertex in its stored traversal direction; `length_mm = to_mm - from_mm`.
`start_vertex` and `end_vertex` index the stored cutline, including inserted notch
vertices and the repeated closing vertex. A notch's `at_mm` is local to its edge;
`kind` is `notch` for plain exports, `v_slit` for layer 4, and `t`, `castle`,
`check`, `u` for layers 80–83. A boundary notch belongs to the following edge,
except the closing endpoint belongs to the final edge. Holes have no seam edges
in v0.

A future seam candidate must explicitly supply:

- `id`, `a` and `b` (globally unique edge IDs), and `orientation` (`same`,
  `reversed`, or `unknown`).
- `matches`: pairs `{a_mm, b_mm}` in local edge arc lengths.
- `construction`: a declared string or null when unknown; never a default
  `plain`. `allowance_mm` and signed `ease_mm` are numeric or null.
- `confidence` between 0 and 1 and `evidence`, an array of explanation strings.

An opening, when manually authored, is `{id, edges: [edge_id, ...], kind}` with
nullable kind. These fields reserve a reviewable vocabulary; this adapter does
not generate openings or propose seams. `review` has `status` (`pending`,
`human_approved`, `rejected`), nullable `reviewer`, and an array of string `notes`.
Only a human review workflow may set approval or rejection after inspection.
The schema permits these persisted review states; generated output is pending.

## Validation and limits

`validate_assembly` returns `{level, code, panel, message}` issues (panel is null
for document-wide issues). It performs minimal structural checks without a
JSON Schema runtime dependency, checks ring closure, validity, positive area,
holes, sewline/status consistency, grain presence, notch ranges, edge indices,
consecutive edge intervals and total perimeter (max of 0.00001 mm and one part
per million). It also checks duplicate IDs, seam edge references and reuse of
an edge in multiple seam endpoints. Missing grain is a warning. It is not a
complete draft 2020-12 validator or a garment construction verifier; seam match
semantics, ease feasibility and opening semantics still require review.

Synthetic tests establish only the asserted adapter behavior, not general
AAMA/ASTM compliance or compatibility with a particular fashion CAD system.
No real-photo accuracy is established: see [precision notes](precision-notes.md)
and [synthetic benchmarks](benchmark.md). Keep real captures and derived
artifacts outside the repository, following the [physical comparison
protocol](real-photo-protocol.md).
