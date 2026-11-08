# easyon-scan

Turn a phone photograph of a flat paper sewing pattern into a closed DXF outline
in millimetres. Python 3.11+, Apache-2.0. Early experimental release.

![easyon-scan in 60 seconds](docs/easyon-scan.gif)

[Watch with narration (MP4, 57 s)](docs/easyon-scan.mp4)

## Test it in 5 minutes

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
easyon-scan sheet --out markers-free-a4.pdf
# Optional: one card per A4 sheet, using different IDs:
# easyon-scan sheet --id 0 --out marker-0.pdf
```

Or download [markers-free-a4.pdf](markers-free-a4.pdf). Print at **100% / actual
size**, without fitting to the page. Check the 100 mm ruler and each black
90 × 90 mm square with a physical ruler. Cut the four cards along the cut lines,
keeping their white borders.

Place the cards on all sides so they **surround the entire pattern with clearance**
on a dark, matte table, with any rotation. No rectangle, tape measure or prescribed spacing is needed. Use four
or more distinct ArUco DICT_4X4_50 IDs (0–49), all with 90 mm black squares.
Keep the cards and light paper flat in the **same plane**, with a gap between
cards and pattern. The complete pattern must sit well inside the markers' convex
hull (the boundary joining the outermost markers). Contours touching that boundary
or the marker cards are rejected. Leave a visible strip of dark table all around.

```text
[ID 0]                         [ID 1]
       +-------------------+
       |   paper pattern   |
       +-------------------+
          dark table gap
[ID 3]                         [ID 2]
```

Keep other light objects and spare paper off the working area.

Photograph from above with the whole pattern and all markers visible. Use even
lighting and the original photograph; avoid glare, shadows and ultrawide lenses.

```sh
easyon-scan web
# Open http://127.0.0.1:8765 and upload the photograph.
# Or:
easyon-scan measure photo.jpg --out pattern.dxf --preview preview.png
```

The page shows the outline, dimensions, area and marker residual in **mm**, with
a warning above 1 mm, and offers a DXF download. Uploads and exports stay in
request memory. Limits: 25 MB / 40 megapixels; the server binds to localhost.

In free mode width and height are the shorter and longer sides of the minimum
rotated bounding rectangle. Coordinates use the lowest detected marker ID as
the reference frame (origin at its first corner, y down). DXF uses millimetres,
a closed LWPOLYLINE on OUTLINE, and y up with its origin at the outline's lower
left. No marker pose is prescribed.

### Camera calibration and photo quality

```sh
easyon-scan calibrate --board charuco-a4.pdf
# Print at actual size, mount flat, and verify 30 mm squares with a ruler.
# Take at least six sharp photos with varied tilts, distances and board positions.
easyon-scan calibrate calibration/*.jpg --phone my-phone-main --out my-phone.json
easyon-scan measure photo.jpg --profile my-phone.json --out pattern.dxf
```

The A4 board has 6 × 8 ChArUco squares (30 mm, markers 22 mm,
DICT_4X4_50). Calibration requires at least 12 detected corners per view,
varied frame coverage, matching oriented resolutions and RMS reprojection
error at most 2 pixels. The versioned JSON stores the phone label, resolution,
camera matrix, OpenCV distortion coefficients, view count and fit RMS. The fit
uses two radial terms and tangential distortion (k3 fixed at zero).
Use a separate profile per phone, lens, zoom and capture mode. Resolution
mismatches are rejected; other capture-setting mismatches cannot be detected.
EXIF orientation is applied before calibration and measurement. Correction
runs before marker detection and contour extraction. Fit RMS is not a real
measurement accuracy estimate.

The web page accepts an optional profile JSON upload. CLI JSON and the page
show quality checks and actionable hints. Checks run before contour measurement:
minimum marker edge 35 px; minimum normalized marker Laplacian variance 100;
marker white/black contrast at least 60 intensity levels; clipping fraction
warning above 80%; paper/card illumination spread warning above 25% and rejection
above 55%; homography local anisotropy rejection above 3; marker RMS warning
above 1 mm and rejection above 2 mm. The homography ratio is a tilt proxy,
not a camera angle. These are experimental heuristics, not validated safety
or accuracy thresholds. Missing or undetectable markers can prevent checks.
Rejected CLI measurements exit 2 and output JSON with the error and available
checks; no DXF is exported. The web page displays rejection checks and hints.

Without a profile, an explicit lens warning is always included: some distortion
can escape consistency checks. Every result warns to physically verify 90 mm
cards: uniform printer scaling is unobservable from the photo. Unequal scale,
nonplanarity and distortion may raise marker residuals but cannot be uniquely
identified. Passing checks never certifies scale or real-photo accuracy.

### Optional fixed layout

Use `--layout fixed` with `sheet`, `measure` or `web` for the original layout.
All markers must point up, with black-square top-left corners at:

| ID | x (mm) | y (mm) |
|---|---:|---:|
| 0 | 0 | 0 |
| 1 | 690 | 0 |
| 2 | 690 | 890 |
| 3 | 0 | 890 |

The envelope is 780 × 980 mm. Keep the pattern within x=90…690, y=90…890,
with 3 mm clearance. Fixed-mode width and height follow the layout axes.

## Synthetic smoke test

No printer or camera needed:

```sh
pip install -e '.[test]'
python -m easyon_scan.synthetic synthetic.png
easyon-scan measure synthetic.png --out pattern.dxf --preview preview.png
pytest -q
```

Tests exercise random free marker poses with 4 and 6 cards at camera tilts
0°, 10°, 20° and 35°, fixed layout, perspective and JPEG input, requiring
width/height error below 1 mm
against a known 400 × 600 mm rectangle, reread the DXF with ezdxf, check PDF page
sizes, test the upload/download flow, and reject missing references/paper.

### Synthetic photo batches

Generate fixed-layout cases without a printer or camera:

```sh
easyon-scan synthetic --out cases --count 10 --seed 2026 \
  --tilt-deg 20 --radial-k1 0.04 --shadow 0.25 --curl-mm 2 \
  --lighting 0.25 --printer-scale 1.02 --noise-sigma 3 --jpeg-quality 65
# These fixtures use the fixed marker layout:
easyon-scan measure cases/case-0000.png --layout fixed --out pattern.dxf
python scripts/benchmark.py --count 3 --seed 2026
pytest -q  # Includes the small benchmark marked slow
# To skip the benchmark test: pytest -q -m 'not slow'
```

The batch contains lossless `case-NNNN.png` files and `manifest.json` (schema
version 1). Each entry records its filename, seed, layout, exact parameters and
nominal ground truth: width/height in mm, area in mm², contour coordinates in mm
in the fixed-layout frame, and actual printed black-square size in mm. Case i
uses seed + i. Existing case files or manifests are refused. Reproduction assumes
the same NumPy/OpenCV versions. PNG preserves the simulated JPEG artifacts without
an additional lossy save.

The Python API is `generate_case(seed=..., parameters=Parameters(...))`, returning
an image and metadata; `generate_batch(folder, count=..., seed=..., parameters=...)`
writes a manifest. Existing `generate` and `generate_free` fixtures remain available.
All effects default to disabled (`printer_scale=1`, `jpeg_quality=100`). Parameters:

| Parameter | Range | Model |
|---|---|---|
| `tilt_deg` | −45…45 degrees | Pinhole plane projection about the horizontal camera axis, with wider framing at larger tilts |
| `radial_k1` | −0.4…0.4 | Inverse radial sampling, radius normalized by half the image diagonal |
| `shadow` | 0…0.9 | Broad soft Gaussian attenuation |
| `curl_mm` | 0…20 mm | Sinusoidal horizontal displacement of the paper, random phase |
| `lighting` | 0…0.9 | Multiplicative linear illumination gradient |
| `printer_scale` | 0.9…1.1 | Uniform scaling of marker cards only (0.98 means 98%) |
| `noise_sigma` | 0…50 intensity levels | Independent Gaussian sensor noise, clipped to 8 bits |
| `jpeg_quality` | integer 1…100 | JPEG encode/decode; 100 bypasses compression |

Effects apply as card scaling, waviness, shadow/lighting, perspective, radial
distortion, sensor noise, then JPEG compression. The pattern's independent nominal size stays
400 × 600 mm, including when cards are printed at the wrong scale. Curl is a
projected waviness approximation, not a physical 3D paper model or a change to
material dimensions. These controlled defects are sensitivity fixtures, not
validated models of phone cameras or real paper.

[Benchmark results](docs/benchmark.md) report mean, p95 and worst absolute dimension
error (the larger of width and height error) per defect profile, plus rejection
rates using free-layout calibration (assumed 90 mm cards). `--count` is cases per profile. The fixed seed varies noise and waviness;
other profiles repeat their deterministic geometry. Accepted-case statistics
exclude rejected images; all-rejected profiles report N/A. The benchmark does
not reproduce the earlier research prototype's contour-error metric and does
not establish real-photo accuracy.

## Accuracy and limits

The preceding research prototype reported **synthetic mean contour error around
0.05 mm** under selected calibrated conditions (not width/height error and not a
real-photo guarantee). This smaller package has a separate synthetic smoke test;
the sensitivity benchmark above does not reproduce that full benchmark. **Real phone photos are not yet
validated.** Successful marker detection does not certify the resulting scale.

The scanner detects ArUco DICT_4X4_50 markers. Free calibration starts from one
90 mm square, maps the other corners into that plane, and initializes each
unknown pose with a rigid 2D fit. Five damped Gauss–Newton iterations jointly
refine the image-to-plane homography and all other marker rotations/translations
against every corner. The first marker fixes the arbitrary coordinate frame.
The reported residual is RMS Euclidean corner error in mm; above 1 mm produces
a warning. Coherent printer scale errors can still pass this check.

Light contours inside the marker hull are checked for relative area, solidity
and clearance from cards and the hull. Multiple plausible contours are rejected
with a request to remove light objects. No rectangular shape is required. Its intensity edge is refined in the source photo before mapping
to mm. Rectification is used for preview only, capped at 4000 pixels per side.
Degenerate homographies and extreme working hulls are rejected.

Lens distortion is corrected only when a matching camera profile is supplied. Printer scaling, placement error, paper curl,
shadows and poor contrast can dominate the error. Use the same plane for paper
and markers. White tables, multiple pieces, holes and interior markings are not
supported. Inspect the preview and independently check dimensions before use.
Only the outer contour is exported: **no notches, grain line, seam allowance,
grading or AAMA/ASTM semantics**. Audaces compatibility has not been validated.

The detector uses AprilTag corner refinement followed by explicit subpixel
refinement: local OpenCV 4.13/4.14 default ArUco refinement displaced the
corners on the clean fixture.
Dependencies carry their own licenses and bundled binary notices; Apache-2.0
applies to this project's code, not all third-party binary components.

## Roadmap

- Validate against measured real phone photographs and publish error distributions.
- Validate camera profiles and quality-check thresholds on real devices.
- Support custom marker sizes and persisted layouts.
- Detect notches and grain lines; add AAMA/ASTM export.
- Add multi-photo stitching and broader lighting/contrast fixtures.

## Provenance and development

The numerical approach and the `sample` / `cruzamento` subpixel helpers were
adapted from the read-only MOLDES-0 prototype's `pipeline/extrair.py`, reviewed
alongside its README, decisions, method, engine and DXF example. No prototype
photos, scene datasets, logs or generated outputs are distributed here.

```sh
pip install -e '.[test]'
pytest -q
```

See [LICENSE](LICENSE) for Apache License 2.0.
