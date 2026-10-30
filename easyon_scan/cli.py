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
    p.add_argument("--size", choices=("a4", "a3"), default="a4")
    p.add_argument("--out", required=True)
    p = commands.add_parser("measure", help="Measure a phone photograph")
    p.add_argument("photo")
    p.add_argument("--out", required=True)
    p.add_argument("--preview")
    commands.add_parser("web", help="Open local upload interface at 127.0.0.1:8765")
    args = parser.parse_args()
    try:
        if args.command == "sheet":
            sheet(args.out, args.size)
            print(f"Saved {args.out}. Print at 100%; cut and assemble as instructed.")
        elif args.command == "measure":
            result = measure(args.photo)
            export_dxf(result.contour_mm, args.out)
            if args.preview and not cv.imwrite(args.preview, result.preview):
                raise OSError("Could not write preview")
            print(json.dumps(result.summary(), indent=2))
            if result.residual_px > 1:
                print("Warning: marker residual exceeds 1 px; inspect placement and photo.", file=sys.stderr)
        else:
            from .web import create_app
            create_app().run(host="127.0.0.1", port=8765, debug=False)
    except (ScanError, OSError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")
