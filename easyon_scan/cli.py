"""Command line interface."""
import argparse
import json
import sys
import cv2 as cv
from .markers import sheet
from .scan import measure, ScanError
from .dxf import export_dxf


def main():
    parser = argparse.ArgumentParser(prog="easyon-scan")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("sheet", help="Print-and-cut marker cards with assembly instructions")
    p.add_argument("--sketch-kit", action="store_true")
    p.add_argument("--id", type=int, help="Print one marker (ID 0–49) per sheet")
    p.add_argument("--size", choices=("a4", "a3"), default="a4")
    p.add_argument("--out", required=True)
    p = commands.add_parser("measure", help="Measure a phone photograph")
    p.add_argument("photo", nargs="+")
    p.add_argument("--layout-file")
    p.add_argument("--save-layout")
    p.add_argument("--out", required=True)
    p.add_argument("--preview")
    p.add_argument("--debug-overlay")
    p.add_argument("--reference")
    p.add_argument("--all-objects", action="store_true")
    p.add_argument("--sketch", action="store_true")
    p.add_argument("--calib-mm-x", type=float)
    p.add_argument("--calib-mm-y", type=float)
    p.add_argument("--bar-length")
    p.add_argument("--bar-points", type=json.loads)
    p.add_argument("--check-reference")
    p.add_argument("--grid-pitch-mm", type=float, default=5.)
    p.add_argument("--json-out")
    p.add_argument("--table-out")
    p.add_argument("--reference-frontal", action="store_true")
    p.add_argument("--dxf-mode", choices=("plain", "aama"), default="plain")
    p.add_argument("--size", default="BASE", help="Size label for AAMA-style DXF")
    p.add_argument("--profile")
    commands.add_parser("web", help="Open local upload interface at 127.0.0.1:8765")
    for command in commands.choices.values():
        command.add_argument("--layout", choices=("free", "fixed"), default="free")
    for name in ("sheet", "measure"):
        commands.choices[name].add_argument("--marker-size", help="Black-square sizes in mm: 0=90,1=60")
    p = commands.add_parser("calibrate", help="Print a ChArUco board or calibrate a phone")
    p.add_argument("photos", nargs="*")
    p.add_argument("--board")
    p.add_argument("--out")
    p.add_argument("--phone", default="phone")
    p = commands.add_parser("synthetic", help="Generate synthetic cases and a metric manifest")
    p.add_argument("--out", required=True)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--seed", type=int, default=0)
    from .synthetic import Parameters, generate_batch
    from dataclasses import fields
    for field in fields(Parameters):
        p.add_argument("--" + field.name.replace("_", "-"),
                       type=int if field.name in ("jpeg_quality", "annotations") else float,
                       default=field.default)
    p = commands.add_parser("synthetic-garment", help="Generate synthetic garment DXF and assembly fixture")
    p.add_argument("--out", required=True)
    p.add_argument("--sizes", default="P,M,G", help="Comma-separated subset of P,M,G")
    p = commands.add_parser("assembly", help="Convert DXF to experimental garment assembly v0")
    p.add_argument("file")
    p.add_argument("--out", required=True)
    p.add_argument("--size")
    p.add_argument("--assume-units", choices=("mm", "cm", "in"))
    p.add_argument("--seam-allowance-mm", type=float)
    p.add_argument("--report")
    p = commands.add_parser("propose", help="Suggest review-only seam pairs from assembly v0")
    p.add_argument("file")
    p.add_argument("--out", required=True)
    p = commands.add_parser("validate", help="Validate a garment assembly v0 JSON file")
    p.add_argument("file")
    p.add_argument("--json", action="store_true", help="Output JSON instead of human-readable format")
    p = commands.add_parser("dxf-inventory", help="Diagnostic report of a DXF file before garment-assembly conversion")
    p.add_argument("file")
    p.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable text")
    args = parser.parse_args()
    try:
        if args.command == "synthetic-garment":
            from .synthetic_garment import generate_fixture
            generate_fixture(args.out, tuple(args.sizes.split(",")))
            print(f"Saved synthetic garment fixture to {args.out}")
        elif args.command == "validate":
            from pathlib import Path
            from .assembly import validate_assembly
            try:
                doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                if args.json:
                    print(json.dumps({"valid": False, "issues": [{"level": "error", "code": "json_parse", "panel": None, "message": str(exc)}]}))
                else:
                    print(f"Error: Invalid JSON - {exc}")
                parser.exit(1)
            issues = validate_assembly(doc)
            errors = [i for i in issues if i['level'] == 'error']
            warnings = [i for i in issues if i['level'] == 'warning']
            if args.json:
                print(json.dumps({"valid": len(errors) == 0, "issues": issues}))
            else:
                for issue in issues:
                    panel_str = f" ({issue['panel']})" if issue.get('panel') else ""
                    print(f"{issue['level']}: {issue['code']}{panel_str}: {issue['message']}")
                print(f"{len(errors)} errors, {len(warnings)} warnings")
            if errors:
                parser.exit(1)
        elif args.command == "dxf-inventory":
            from pathlib import Path
            from .dxf_inventory import inventory
            result = inventory(args.file, json_output=args.json)
            print(result)
        elif args.command == "propose":
            from pathlib import Path
            from .propose import propose_assembly
            result = propose_assembly(json.loads(Path(args.file).read_text(encoding="utf-8")))
            Path(args.out).write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
            print(f"{len(result['proposals'])} proposals")
        elif args.command == "assembly":
            from pathlib import Path
            from .assembly import dxf_to_assembly, validate_assembly
            assembly = dxf_to_assembly(args.file, size=args.size,
                assume_units=args.assume_units, seam_allowance_mm=args.seam_allowance_mm)
            issues = validate_assembly(assembly)
            Path(args.out).write_text(json.dumps(assembly, indent=2, allow_nan=False)+"\n", encoding="utf-8")
            edges = [e for p in assembly['panels'] for e in p['edges']]
            summary = (f"{len(assembly['panels'])} panels, {len(edges)} edges, "
                       f"{sum(len(e['notches']) for e in edges)} notches, {len(issues)} issues")
            print(summary)
            if args.report:
                Path(args.report).write_text(summary+"\n"+"\n".join(
                    f"{i['level']}: {i['code']} ({i['panel']}): {i['message']}" for i in issues)+"\n", encoding="utf-8")
            if any(i['level'] == 'error' for i in issues):
                parser.exit(1)
        elif args.command == "calibrate":
            from .lens import board_pdf, calibrate
            from pathlib import Path
            if args.board:
                board_pdf(args.board)
            if args.photos:
                if not args.out:
                    raise ValueError("Photos require --out profile.json")
                Path(args.out).write_text(json.dumps(calibrate(args.photos, args.phone), indent=2))
            elif not args.board:
                raise ValueError("Specify --board or six photos and --out")
        elif args.command == "synthetic":
            parameters = Parameters(**{f.name: getattr(args, f.name) for f in fields(Parameters)})
            generate_batch(args.out, count=args.count, seed=args.seed, parameters=parameters)
            print(f"Saved {args.count} cases and manifest.json to {args.out}")
        elif args.command == "sheet":
            if args.sketch_kit:
                from .sketch import sketch_kit
                sketch_kit(args.out)
            else:
                sheet(args.out, args.size, args.layout, args.id, marker_size=args.marker_size)
            print(f"Saved {args.out}. Print at 100%; cut and place as instructed.")
        elif args.command == "measure":
            options = dict(profile=args.profile, marker_size=args.marker_size, layout_file=args.layout_file, save_layout=args.save_layout)
            if (args.reference or args.all_objects or args.check_reference) and len(args.photo) > 1:
                raise ValueError("References support one photo only")
            if args.sketch or args.calib_mm_x is not None or args.calib_mm_y is not None:
                if len(args.photo) != 1: raise ValueError("Sketch requires one photo")
                from .sketch import measure_sketch
                result = measure_sketch(args.photo[0], grid_pitch_mm=args.grid_pitch_mm, bar_points=args.bar_points, bar_length=args.bar_length, calib_mm_x=args.calib_mm_x, calib_mm_y=args.calib_mm_y)
            elif len(args.photo) > 1:
                if args.layout != "free":
                    raise ValueError("Stitching requires free layout")
                from .stitch import stitch
                result = stitch(args.photo, **options)
            else:
                result = measure(args.photo[0], layout=args.layout, reference=args.reference, reference_frontal=args.reference_frontal, debug_overlay=args.debug_overlay, all_objects=args.all_objects, check_reference=args.check_reference, grid_pitch_mm=args.grid_pitch_mm, **options)
            export_dxf(result, args.out, mode=args.dxf_mode, size=args.size)
            if args.preview and not cv.imwrite(args.preview, result.preview):
                raise OSError("Could not write preview")
            payload = json.dumps(result.summary(), indent=2, ensure_ascii=False, allow_nan=False)
            if args.json_out:
                from pathlib import Path
                Path(args.json_out).write_text(payload+"\n")
            if args.all_objects:
                from .multi import table_pt
                table = table_pt(result.summary())
                print(table, file=sys.stderr)
                if args.table_out:
                    from pathlib import Path
                    Path(args.table_out).write_text(table+"\n")
            print(payload)
            if result.residual_mm is not None and result.residual_mm > 1:
                print("Warning: marker residual exceeds 1 mm; inspect placement and photo.", file=sys.stderr)
        else:
            from .web import create_app
            create_app(layout=args.layout).run(host="127.0.0.1", port=8765, debug=False)
    except (ScanError, OSError, ValueError) as exc:
        if args.command == "measure":
            print(json.dumps({"error": str(exc), "code": getattr(exc, "code", None), "quality_checks": getattr(exc, "checks", [])}))
        parser.exit(2, f"Error: {exc}\n")
