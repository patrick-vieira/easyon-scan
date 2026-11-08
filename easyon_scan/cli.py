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
    p.add_argument("--id", type=int, help="Print one marker (ID 0–49) per sheet")
    p.add_argument("--size", choices=("a4", "a3"), default="a4")
    p.add_argument("--out", required=True)
    p = commands.add_parser("measure", help="Measure a phone photograph")
    p.add_argument("photo")
    p.add_argument("--out", required=True)
    p.add_argument("--preview")
    commands.add_parser("web", help="Open local upload interface at 127.0.0.1:8765")
    for command in commands.choices.values():
        command.add_argument("--layout", choices=("free", "fixed"), default="free")
    p = commands.add_parser("synthetic", help="Generate synthetic cases and a metric manifest")
    p.add_argument("--out", required=True)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--seed", type=int, default=0)
    from .synthetic import Parameters, generate_batch
    from dataclasses import fields
    for field in fields(Parameters):
        p.add_argument("--" + field.name.replace("_", "-"),
                       type=int if field.name == "jpeg_quality" else float,
                       default=field.default)
    args = parser.parse_args()
    try:
        if args.command == "synthetic":
            parameters = Parameters(**{f.name: getattr(args, f.name) for f in fields(Parameters)})
            generate_batch(args.out, count=args.count, seed=args.seed, parameters=parameters)
            print(f"Saved {args.count} cases and manifest.json to {args.out}")
        elif args.command == "sheet":
            sheet(args.out, args.size, args.layout, args.id)
            print(f"Saved {args.out}. Print at 100%; cut and place as instructed.")
        elif args.command == "measure":
            result = measure(args.photo, layout=args.layout)
            export_dxf(result.contour_mm, args.out)
            if args.preview and not cv.imwrite(args.preview, result.preview):
                raise OSError("Could not write preview")
            print(json.dumps(result.summary(), indent=2))
            if result.residual_mm > 1:
                print("Warning: marker residual exceeds 1 mm; inspect placement and photo.", file=sys.stderr)
        else:
            from .web import create_app
            create_app(layout=args.layout).run(host="127.0.0.1", port=8765, debug=False)
    except (ScanError, OSError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")
