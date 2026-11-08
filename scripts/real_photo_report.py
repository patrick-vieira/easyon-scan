"""Compare independent physical CSV measurements with saved scanner CLI JSON."""
import argparse
import csv
import json
import math
from pathlib import Path


def report(csv_path, scanner_dir):
    rows, seen = [], set()
    with Path(csv_path).open(newline='', encoding='utf-8-sig') as source:
        reader = csv.DictReader(source)
        required = {'capture_id', 'scanner_json', 'metric', 'physical_value'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError('Required columns: ' + ', '.join(sorted(required)))
        for line, item in enumerate(reader, 2):
            if any(not item.get(name) for name in required):
                raise ValueError(f'Line {line}: missing required value')
            key = (item['capture_id'], item['metric'])
            if key in seen:
                raise ValueError(f'Line {line}: duplicate capture/metric')
            seen.add(key)
            metric = item['metric']
            if metric not in ('width_mm', 'height_mm', 'area_mm2'):
                raise ValueError(f'Line {line}: unsupported metric {metric}')
            physical = float(item['physical_value'])
            if not math.isfinite(physical) or physical <= 0:
                raise ValueError(f'Line {line}: physical_value must be finite and positive')
            row = dict(capture_id=item['capture_id'], metric=metric, physical_value=physical,
                       status='unavailable', error=None, scanner_value=None,
                       signed_error=None, absolute_error=None, relative_error_percent=None)
            try:
                data = json.loads((Path(scanner_dir)/item['scanner_json']).read_text())
                if not isinstance(data, dict):
                    raise ValueError('Scanner JSON must be an object')
                if 'error' in data:
                    row.update(status='rejected', error=str(data['error']))
                else:
                    value = data[metric]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                        raise ValueError('Scanner value must be finite and positive')
                    delta = value-physical
                    row.update(status='ok', scanner_value=value, signed_error=delta,
                               absolute_error=abs(delta), relative_error_percent=100*delta/physical)
            except (OSError, ValueError, KeyError) as exc:
                row['error'] = str(exc)
            rows.append(row)
    if not rows:
        raise ValueError('CSV contains no measurements')
    summaries = {}
    for metric in sorted({r['metric'] for r in rows}):
        values = sorted(r['absolute_error'] for r in rows if r['metric'] == metric and r['status'] == 'ok')
        n = len(values)
        position = .95*(n-1)
        low = int(position)
        summaries[metric] = dict(accepted=n, total=sum(r['metric'] == metric for r in rows),
            mean_absolute_error=sum(values)/n if n else None,
            p95_absolute_error=(values[low]+(position-low)*(values[min(low+1,n-1)]-values[low])) if n else None,
            worst_absolute_error=max(values) if n else None)
    return dict(schema_version=1, notice='Observed comparisons only; no claim of real-photo accuracy. Units follow metric names. Failed rows are excluded from error statistics.',
                measurements=rows, summary=summaries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('measurements', help='Physical measurement CSV')
    parser.add_argument('--scanner-dir', default='.', help='Base directory for scanner_json paths')
    parser.add_argument('--out', required=True, help='JSON error report')
    args = parser.parse_args()
    try:
        result = report(args.measurements, args.scanner_dir)
        Path(args.out).write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
