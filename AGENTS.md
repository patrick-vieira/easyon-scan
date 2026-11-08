# Coding agent guide

easyon-scan is a public Apache-2.0 Python 3.11+ project that measures flat paper
sewing patterns from photographs using ArUco markers or other references and
exports millimetre DXF outlines. It also includes experimental multi-photo,
multi-object and sketch workflows and a local web interface. It is an
experimental tool, not a precision-guaranteed product; real-photo accuracy and
compatibility with specific fashion CAD systems have not been validated.

## Look here first

- [README.md](README.md): setup, workflows and limitations.
- [docs/precision-notes.md](docs/precision-notes.md): remaining error hypotheses.
- [docs/real-photo-protocol.md](docs/real-photo-protocol.md): independent physical comparisons.
- [docs/benchmark.md](docs/benchmark.md): synthetic results, metrics and reproduction.

## Module map

Paths below are relative to `easyon_scan/` unless stated otherwise.

| Module / directory | Responsibility |
| --- | --- |
| `annotations.py` | Recognizes the documented triangular notch and double-arrow grain ink conventions. |
| `calibration.py` | Fits a metric planar homography jointly with unknown rigid marker poses. |
| `cli.py` | Parses CLI commands and dispatches measurement, printing, calibration and web workflows. |
| `dxf.py` | Exports outlines, holes and annotations as plain DXF or experimental AAMA/ASTM-style blocks. |
| `layout.py` | Validates per-ID marker sizes and reads/writes versioned metric marker layouts. |
| `lens.py` | Prints ChArUco boards, fits camera profiles and corrects matching-resolution images. |
| `markers.py` | Defines ArUco geometry and prints actual-size free/fixed marker cards. |
| `multi.py` | Segments multiple objects, fits local square grids and reports dimensions and arm extents. |
| `quality.py` | Computes experimental focus, lighting, tilt and marker-consistency checks. |
| `references.py` | Catalogues nominal reference dimensions and detects isolated reference silhouettes. |
| `scan.py` | Loads oriented photos, detects references and extracts metric pieces, holes and previews. |
| `sketch.py` | Traces sketches with declared scale, calibrates kit axes and produces tiled PDFs and SVG previews. |
| `stitch.py` | Registers shared markers across photos and merges contours through a metric mosaic. |
| `synthetic.py` | Generates deterministic synthetic scenes, defects, annotations and ground-truth manifests. |
| `web.py` | Builds the stateless Flask upload/camera UI with in-memory processing and localized diagnostics. |
| `templates/` | Jinja pages and includes for upload, capture, options, results and camera JavaScript. |
| `static/` | Styles the responsive local web interface. |
| `tests/` (repo root) | Synthetic regression tests for measurement, references, calibration, exports, CLI and web flows. |
| `scripts/` (repo root) | Runs synthetic benchmarks and compares scanner JSON with independent physical CSV measurements. |
| `docs/` (repo root) | Records limitations, benchmark results, real-photo protocol and local explainer media. |

## Commands

Run from the repository root. Installation (when setup is authorized):

```sh
python -m venv .venv
.venv/bin/pip install -e '.[test]'
```

The console entry point is `easyon_scan.cli:main`. Its subcommands are `sheet`,
`measure`, `web`, `calibrate` and `synthetic`. Three CLI examples using only
generated inputs (choose a fresh output directory for each synthetic batch):

```sh
.venv/bin/easyon-scan sheet --out markers.pdf
.venv/bin/easyon-scan synthetic --out cases --count 3 --seed 2026
.venv/bin/easyon-scan measure cases/case-0000.png --layout fixed --out pattern.dxf --preview preview.png
```

Start the web app:

```sh
.venv/bin/easyon-scan web
```

This calls `easyon_scan.web.create_app(layout=...)` and serves
<http://127.0.0.1:8765> with debugging disabled. Uploads and exports stay in
request memory; retain that privacy behavior.

Tests (the full suite includes tests marked `slow`):

```sh
.venv/bin/pytest -q
.venv/bin/pytest -q -m slow
.venv/bin/pytest -q -m 'not slow'
```

Reproduce the benchmarks described in [docs/benchmark.md](docs/benchmark.md):

```sh
.venv/bin/python scripts/benchmark.py --count 3 --seed 2026
.venv/bin/python scripts/benchmark_references.py
```

Read script output options before running: `benchmark.py` writes its report to
`docs/benchmark.md` by default. `benchmark_references.py` prints reference rows.
`scripts/real_photo_report.py` supports the independent comparison protocol;
keep real captures and their derived reports outside the repository.

## Hard rules

- Real photos from users or customers must NEVER enter this repository: no
  fixtures, artifacts or documentation images, including derived previews.
  Tests use synthetic images only; use `easyon_scan/synthetic.py` for scenes.
- Never add customer data, machine-local absolute paths, secrets or credentials.
- Never claim measurement precision beyond
  [docs/precision-notes.md](docs/precision-notes.md) and
  [docs/benchmark.md](docs/benchmark.md); cite these documents when discussing
  accuracy. Synthetic dimension errors are not real-photo guarantees, and
  marker residuals do not establish physical accuracy.
- Keep DXF export marked experimental unless the specific behavior is proven
  by a test. A round-trip test establishes only its assertions, not general
  AAMA/ASTM compliance or compatibility with a particular CAD system.

## Workflow

- Cards live in EasyON project **SCAN**. Use the assigned card; never invent numbers.
- One card = one branch: `hokage/SCAN-<n>` or `<team>/SCAN-<n>`.
- Commit messages start with `SCAN-<n>:`.
- Open a PR targeting `main` with tests green.
- Executors do not run git. The orchestrator handles git operations and commits.
