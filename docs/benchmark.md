# Synthetic sensitivity benchmark

Seed: 2026. Cases per profile: 3.
Runtime: Python 3.13.7, NumPy 2.5.4, OpenCV 4.14.0.

Error is max(abs(width error), abs(height error)) in mm against the
independent nominal 400 × 600 mm pattern. Mean, p95 (linear percentile)
and worst error include accepted cases only. Rejections are ScanError
failures; unexpected errors abort the run. Free-layout measurement is used (card size assumed to be 90 mm).

**These simulated cases do not validate accuracy on real photographs.**
The waviness model is a projected sinusoidal displacement, not a 3D surface.
Random seeds vary waviness phase and sensor noise; deterministic profiles
otherwise repeat the same geometry. N is per profile, not total cases.

| Defect | Accepted / total | Mean (mm) | p95 (mm) | Worst (mm) | Rejection rate |
|---|---:|---:|---:|---:|---:|
| clean | 3 / 3 | 0.260 | 0.260 | 0.260 | 0.0% |
| perspective | 3 / 3 | 0.444 | 0.444 | 0.444 | 0.0% |
| radial distortion | 0 / 3 | N/A | N/A | N/A | 100.0% |
| soft shadow | 3 / 3 | 0.334 | 0.334 | 0.334 | 0.0% |
| paper waviness | 3 / 3 | 5.461 | 5.969 | 6.010 | 0.0% |
| uneven lighting | 3 / 3 | 0.302 | 0.302 | 0.302 | 0.0% |
| printer scale | 3 / 3 | 11.590 | 11.590 | 11.590 | 0.0% |
| sensor noise | 3 / 3 | 0.285 | 0.289 | 0.289 | 0.0% |
| JPEG compression | 3 / 3 | 0.282 | 0.282 | 0.282 | 0.0% |
| combined | 3 / 3 | 11.541 | 12.102 | 12.199 | 0.0% |

## Before / after

| Defect | Checks disabled mean (mm) | With simulated lens profile mean (mm) |
|---|---:|---:|
| clean | 0.260 | N/A |
| perspective | 0.444 | N/A |
| radial distortion | 56.826 | 1.827 |
| soft shadow | 0.334 | N/A |
| paper waviness | 5.461 | N/A |
| uneven lighting | 0.302 | N/A |
| printer scale | 11.590 | N/A |
| sensor noise | 0.285 | N/A |
| JPEG compression | 0.282 | N/A |
| combined | 11.541 | 10.004 |

Lens profiles here use the analytic inverse-series approximation of the known synthetic radial sampler; they are not fitted phone profiles. Other defects remain uncorrected. Uniform printer scaling is unobservable: accepted cases explicitly warn to verify every 90 mm square with a ruler.


## Parameters

Non-default parameters for each profile:

- clean: defaults.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- perspective: tilt_deg=25.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- radial distortion: radial_k1=0.08.
  Rejections: Photo quality: marker_consistency_mm: Possible lens distortion or inconsistent marker scale: use a camera profile, verify 90 mm cards and keep them coplanar.
- soft shadow: shadow=0.35.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Use diffuse lighting from both sides and remove shadows across the pattern.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- paper waviness: curl_mm=3.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- uneven lighting: lighting=0.4.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Use diffuse lighting from both sides and remove shadows across the pattern.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- printer scale: printer_scale=0.98.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- sensor noise: noise_sigma=5.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- JPEG compression: jpeg_quality=50.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.
- combined: tilt_deg=20, radial_k1=0.04, shadow=0.25, curl_mm=2, lighting=0.25, printer_scale=1.02, noise_sigma=3, jpeg_quality=65.
  Warnings: No lens profile: distortion can escape marker checks. Calibrate this camera and avoid ultrawide lenses.; Possible lens distortion or inconsistent marker scale: use a camera profile, verify 90 mm cards and keep them coplanar.; Verify every black square is 90 mm with a ruler. Uniform printer scaling cannot be detected from this photo.

Reproduce from the repository root:

```sh
python scripts/benchmark.py --count 3 --seed 2026
```
