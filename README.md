# easyon-scan

Turn a phone photograph of a flat paper sewing pattern into a closed DXF outline
in millimetres. Python 3.11+, Apache-2.0. Early experimental release.

## Test it in 5 minutes

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
easyon-scan sheet --size a4 --out markers.pdf
# A3 is also supported: --size a3
```

Print the PDF at **100% / actual size**, with all fit-to-page options disabled.
Check the printed 100 mm ruler with a physical ruler, in both printer axes;
check that each black marker measures 90 × 90 mm. Keep white borders around
all markers. The printed sheet is a set of **cut-out cards**, not the final
measurement layout: four 90 mm markers on an intact A4 leave too little room
for a useful pattern. A3 uses the same cards and assembly dimensions.

Cut out the cards leaving at least 5 mm white borders. Tape them flat to a dark,
matte table, all TOP arrows pointing up. Use a tape measure and square to place
the **top-left corner of each black square** at these coordinates:

| ID | x (mm) | y (mm) | Position |
|---|---:|---:|---|
| 0 | 0 | 0 | top left |
| 1 | 690 | 0 | top right |
| 2 | 690 | 890 | bottom right |
| 3 | 0 | 890 | bottom left |

The outer black-square envelope is **780 × 980 mm**. Its diagonals should both
measure approximately 1252.5 mm. The empty central working area is 600 × 800 mm
(x=90…690, y=90…890). Layout and marker dimensions are fixed in this release;
arbitrarily placing the cards gives incorrect measurements. The assembly takes
additional time the first time you use it.

Place one light paper pattern flat in the middle, leaving at least 3 mm clearance
from the central area's edges. Photograph all four markers and the entire pattern.
Use even lighting, avoid shadows, glare, ultrawide lenses and steep angles. Transfer
the original photograph to your computer (not a screenshot).

```sh
easyon-scan measure photo.jpg --out pattern.dxf --preview preview.png
# Or use a local upload form:
easyon-scan web
# Open http://127.0.0.1:8765
```

The web page shows the rectified photograph, red outline, area and width × height,
then offers a DXF download. Uploads, previews and DXFs are processed in request
memory, without a server-side file store or session cache. The result is embedded
in the response for the browser to download. Limits: 25 MB and 40 megapixels.
The Flask server binds only to localhost and is intended for local use.

Width and height are the axis-aligned extents relative to the assembled layout,
not the minimum rotated bounding box. Area is in mm². DXF contains one closed
LWPOLYLINE on layer OUTLINE, `$INSUNITS=4`; its origin is lower left and y points
up. The API's contour uses table coordinates, with y pointing down.

## Synthetic smoke test

No printer or camera needed:

```sh
pip install -e '.[test]'
python -m easyon_scan.synthetic synthetic.png
easyon-scan measure synthetic.png --out pattern.dxf --preview preview.png
pytest -q
```

Tests exercise perspective and JPEG input, require width/height error below 1 mm
against a known 400 × 600 mm rectangle, reread the DXF with ezdxf, check PDF page
sizes, test the upload/download flow, and reject missing references/paper.

## Accuracy and limits

The preceding research prototype reported **synthetic mean contour error around
0.05 mm** under selected calibrated conditions (not width/height error and not a
real-photo guarantee). This smaller package has a separate synthetic smoke test;
it does not reproduce that full benchmark. **Real phone photos are not yet
validated.** Successful marker detection does not certify the resulting scale.

The scanner detects real-image ArUco DICT_4X4_50 IDs 0–3, estimates a planar
homography from all 16 corners, segments the largest light-paper contour on a
darker table, refines the intensity edge in the original photo, and maps it to mm.
Rectification is used for preview only. Marker reprojection residual above 3 px
fails; above 1 px produces a warning. Coherent print scaling errors can still pass.

Lens distortion is not corrected. Printer scaling, placement error, paper curl,
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
- Add camera calibration / lens distortion correction and stronger quality checks.
- Support custom, persisted layouts and larger patterns.
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
