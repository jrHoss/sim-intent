"""Optional live smoke test for the Task 11 OpenAI interpreter."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openai import OpenAI

from geom.cylinders import analyze_cylinders
from geom.inventory import FaceInventory, file_sha256
from geom.parser import parse_step
from llm.interpreter import DEFAULT_MODEL, Interpreter, summarize_face_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "instruction",
        nargs="?",
        default="Fix the two bolt holes.",
        help="engineering instruction to interpret",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        help="OpenAI model name (or set OPENAI_MODEL)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not configured; no live request was made.", file=sys.stderr)
        return 2

    fixture = ROOT / "tests" / "fixtures" / "bracket.step"
    inventory = FaceInventory(fixture.name, file_sha256(fixture), parse_step(fixture))
    summary = summarize_face_inventory(inventory, analyze_cylinders(fixture))
    client = OpenAI(api_key=api_key)
    result = Interpreter(client=client, model=args.model).interpret(args.instruction, summary)
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
