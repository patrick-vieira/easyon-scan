"""Print reproducible synthetic reference benchmark rows."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from easyon_scan.references import CATALOGUE
from easyon_scan.synthetic import generate_reference
from easyon_scan.scan import measure, ScanError


def report():
    lines = ['## No-printer references (SCAN-14)', '',
             'Two deterministic 3 px/mm cases per reference: frontal and projective.',
             'BIC repeats the frontal case: it has no perspective correction.',
             'Ideal rounded light body silhouettes; 180 × 260 mm pattern to the right.',
             'Small-reference extrapolation amplifies rasterization/side-fitting errors.',
             'These cases do not model real phone bezel contrast or validate real-photo accuracy.',
             'ArUco remains the most accurate option. Qualitative classes are not accuracy guarantees.', '',
             '| Reference | Accepted / total | Mean (mm) | p95 (mm) | Worst (mm) |',
             '|---|---:|---:|---:|---:|']
    for name in CATALOGUE:
        errors = []
        for perspective in (False, True):
            image, truth = generate_reference(name, perspective=perspective)
            try:
                result = measure(image, reference=name, reference_frontal=name == 'bic')
            except ScanError:
                continue
            errors.append(max(abs(result.width_mm-180), abs(result.height_mm-260)))
        stats = [np.mean(errors), np.percentile(errors,95), max(errors)] if errors else [None]*3
        lines.append(f'| {name} | {len(errors)} / 2 | ' +
                     ' | '.join('N/A' if v is None else f'{v:.3f}' for v in stats) + ' |')
    lines += ['', 'Reproduce: python scripts/benchmark_references.py (prints this section).', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    print(report())
