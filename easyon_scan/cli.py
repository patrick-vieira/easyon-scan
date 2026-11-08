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
    args = parser.parse_args()
    try:
        if args.command == "calibrate":
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
            if len(args.photo) > 1:
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
