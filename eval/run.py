"""Task 15 command-line evaluation entry point."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    raise SystemExit(
        subprocess.run(
            [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            check=False,
        ).returncode
    )
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.harness import (  # noqa: E402
    run_evaluation,
    write_live_unavailable_report,
    write_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the frozen 15-case Task 15 evaluation")
    parser.add_argument("--replay", action="store_true", help="use checked-in sanitized typed responses")
    parser.add_argument("--write-fallback", action="store_true", help="regenerate fallback data (replay only)")
    args = parser.parse_args(argv)
    mode = "REPLAY" if args.replay else "LIVE"
    if args.write_fallback and mode != "REPLAY":
        parser.error("--write-fallback requires --replay")
    try:
        report = run_evaluation(root=ROOT, mode=mode, write_fallback=args.write_fallback)
    except Exception as exc:
        if mode == "LIVE" and "OPENAI_API_KEY is required" in str(exc):
            write_live_unavailable_report(root=ROOT, reason=str(exc))
        print(f"{mode} evaluation could not start: {exc}", file=sys.stderr)
        return 2
    md_path, json_path = write_report(report, root=ROOT)
    print(f"{report.mode} score: {report.score}/{report.total}")
    print(f"PASS={report.pass_count} PASS_AFTER_CLARIFICATION={report.pass_after_clarification_count} FAIL={report.fail_count}")
    print(f"manifest={report.manifest_hash}")
    print(f"results={md_path.relative_to(ROOT)} {json_path.relative_to(ROOT)}")
    return 0 if report.threshold_achieved else 1


if __name__ == "__main__":
    raise SystemExit(main())
