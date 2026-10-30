# easyon-scan

Turn a phone photo of a sewing pattern into a true-scale vector (DXF).

Place the paper pattern flat on a table with printed ArUco markers at the corners, take a photo with your phone, and `easyon-scan` rectifies the table plane, finds the pattern outline and exports it in millimetres.

> **Status:** early prototype. In synthetic, calibrated photos the measured mean error is ~0.05 mm (p95 ~0.14 mm) on a ~60×80 cm pattern. **This is not yet validated on real photos** — real-world accuracy will be published here once measured.

## Why

Small ateliers digitise patterns by hand or with expensive digitising tables. A phone and a printed sheet of markers should be enough for most pieces.

## Roadmap

- [x] Synthetic photo generator with known ground truth
- [x] Marker-based plane rectification and outline measurement
- [ ] Validation with real phone photos
- [ ] Notches, grain line and seam allowance detection
- [ ] DXF-AAMA export
- [ ] Multi-photo stitching for patterns larger than one frame

## Made by

[EasyON](https://easyon.shop) — management software for fashion ateliers and clothing stores. This scanner is the open core of EasyON's pattern features.

## License

To be defined.
