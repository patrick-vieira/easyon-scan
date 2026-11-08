"""Reproducible synthetic sensitivity benchmark; no real-photo accuracy claim."""
import argparse
from dataclasses import asdict
from pathlib import Path
import sys

# Support direct execution from a source checkout, without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2 as cv
import numpy as np
from easyon_scan.scan import ScanError, measure
from easyon_scan.synthetic import Parameters, generate_case

PROFILES = {
    'clean': Parameters(),
    'notches and grain': Parameters(annotations=True),
    'perspective': Parameters(tilt_deg=25),
    'radial distortion': Parameters(radial_k1=.08),
    'soft shadow': Parameters(shadow=.35),
    'paper waviness': Parameters(curl_mm=3),
    'uneven lighting': Parameters(lighting=.4),
    'printer scale': Parameters(printer_scale=.98),
    'sensor noise': Parameters(noise_sigma=5),
    'JPEG compression': Parameters(jpeg_quality=50),
    'combined': Parameters(tilt_deg=20, radial_k1=.04, shadow=.25,
                           curl_mm=2, lighting=.25, printer_scale=1.02,
                           noise_sigma=3, jpeg_quality=65),
}


def benchmark(count=3, seed=2026, profiles=None):
    """count cases per profile; rejected cases are excluded from error statistics."""
    if count < 1:
        raise ValueError('count must be positive')
    results = []
    for name, parameters in (PROFILES if profiles is None else profiles).items():
        errors, rejections, raw_errors, warnings, corrected_errors = [], [], [], [], []
        for i in range(count):
            image, metadata = generate_case(seed=seed+i, parameters=parameters)
            from unittest.mock import patch
            truth = metadata.get('ground_truth', {})
            try:
                with patch('easyon_scan.quality.checks', return_value=[]):
                    raw = measure(image, layout='free')
                raw_errors.append(max(abs(raw.width_mm-truth['width_mm']), abs(raw.height_mm-truth['height_mm'])))
            except ScanError:
                pass
            if parameters.radial_k1 and image is not None:
                h, w = image.shape[:2]
                radius = np.hypot(w, h)/2
                k = parameters.radial_k1
                profile = dict(schema_version=1, image_size=[w, h],
                    camera_matrix=[[radius, 0, w/2], [0, radius, h/2], [0, 0, 1]],
                    distortion=[-k, 3*k*k, 0, 0, -12*k**3])
                try:
                    corrected = measure(image, profile=profile)
                    corrected_errors.append(max(abs(corrected.width_mm-400), abs(corrected.height_mm-600)))
                except ScanError:
                    pass
            try:
                result = measure(image, layout='free')
                warnings.extend(c['message'] for c in result.quality_checks if c['status'] == 'warn')
            except ScanError as exc:
                rejections.append(str(exc))
                continue
            truth = metadata['ground_truth']
            errors.append(max(abs(result.width_mm-truth['width_mm']),
                              abs(result.height_mm-truth['height_mm'])))
        results.append(dict(profile=name, parameters=asdict(parameters), count=count,
                            accepted=len(errors), rejections=rejections, warnings=sorted(set(warnings)),
                            before_mm=float(np.mean(raw_errors)) if raw_errors else None,
                            corrected_mm=float(np.mean(corrected_errors)) if corrected_errors else None,
                            mean_mm=float(np.mean(errors)) if errors else None,
                            p95_mm=float(np.percentile(errors, 95)) if errors else None,
                            worst_mm=float(max(errors)) if errors else None))
    return results


def write_report(results, out, seed):
    lines = ['# Synthetic sensitivity benchmark', '',
             f'Seed: {seed}. Cases per profile: {results[0]["count"]}.',
             f'Runtime: Python {sys.version.split()[0]}, NumPy {np.__version__}, OpenCV {cv.__version__}.', '',
             'Error is max(abs(width error), abs(height error)) in mm against the',
             'independent nominal 400 × 600 mm pattern. Mean, p95 (linear percentile)',
             'and worst error include accepted cases only. Rejections are ScanError',
             'failures; unexpected errors abort the run. Free-layout measurement is used (card size assumed to be 90 mm).', '',
             '**These simulated cases do not validate accuracy on real photographs.**',
             'The waviness model is a projected sinusoidal displacement, not a 3D surface.',
             'Random seeds vary waviness phase and sensor noise; deterministic profiles',
             'otherwise repeat the same geometry. N is per profile, not total cases.', '',
             '| Defect | Accepted / total | Mean (mm) | p95 (mm) | Worst (mm) | Rejection rate |',
             '|---|---:|---:|---:|---:|---:|']
    for row in results:
        values = ['N/A' if row[k] is None else f'{row[k]:.3f}'
                  for k in ('mean_mm', 'p95_mm', 'worst_mm')]
        lines.append(f'| {row["profile"]} | {row["accepted"]} / {row["count"]} | ' +
                     ' | '.join(values) + f' | {len(row["rejections"])/row["count"]:.1%} |')
    lines += ['', '## Before / after', '', '| Defect | Checks disabled mean (mm) | With simulated lens profile mean (mm) |', '|---|---:|---:|']
    for row in results:
        fmt = lambda value: 'N/A' if value is None else f'{value:.3f}'
        lines.append(f'| {row["profile"]} | {fmt(row["before_mm"])} | {fmt(row["corrected_mm"])} |')
    lines += ['', 'Lens profiles here use the analytic inverse-series approximation of the known synthetic radial sampler; they are not fitted phone profiles. Other defects remain uncorrected. Uniform printer scaling is unobservable: accepted cases explicitly warn to verify every 90 mm square with a ruler.', '']
    lines += ['', '## Parameters', '', 'Non-default parameters for each profile:', '']
    defaults = asdict(Parameters())
    for row in results:
        changed = ', '.join(f'{k}={v}' for k, v in row['parameters'].items() if v != defaults[k])
        lines.append(f'- {row["profile"]}: {changed or "defaults"}.')
        if row.get('warnings'):
            lines.append('  Warnings: ' + '; '.join(row['warnings']))
        if row['rejections']:
            lines.append('  Rejections: ' + '; '.join(sorted(set(row['rejections']))))
    lines += ['', 'Reproduce from the repository root:', '', '```sh',
              f'python scripts/benchmark.py --count {results[0]["count"]} --seed {seed}', '```', '']
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=3, help='Cases per defect profile')
    parser.add_argument('--seed', type=int, default=2026)
    parser.add_argument('--out', default='docs/benchmark.md')
    args = parser.parse_args()
    write_report(benchmark(args.count, args.seed), args.out, args.seed)


if __name__ == '__main__':
    main()
