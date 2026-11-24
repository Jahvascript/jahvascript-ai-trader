"""JahvaScript historical research-session controls.

Usage:
  python research_control_v001.py status
  python research_control_v001.py step --count 93
  python research_control_v001.py resume

Historical replay only. No APIs, AI calls, or orders.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHECKPOINT = ROOT / "journal/research_session_v001.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("status", "step", "resume")
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None
    )
    args = parser.parse_args()

    if args.action == "status":
        if args.count is not None:
            parser.error("--count does not apply to status")

        target = ROOT / "research_dashboard_v001.py"
        command = [
            sys.executable,
            str(target),
            "--checkpoint",
            str(CHECKPOINT)
        ]

    else:
        if args.action == "resume" and args.count is not None:
            parser.error("--count does not apply to resume")

        count = args.count if args.count is not None else 93

        if args.action == "step" and not 1 <= count <= 656:
            parser.error("--count must be between 1 and 656")

        target = ROOT / "research_session_v001.py"
        command = [
            sys.executable,
            str(target),
            "--checkpoint",
            str(CHECKPOINT)
        ]

        if args.action == "step":
            command.extend([
                "--max-transitions",
                str(count)
            ])

    print(
        "JAHVASCRIPT HISTORICAL RESEARCH:",
        args.action.upper(),
        flush=True
    )

    result = subprocess.run(command)

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
