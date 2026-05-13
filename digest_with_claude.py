#!/usr/bin/env python3
"""Build a dynamic digest prompt and run Claude non-interactively."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from build_digest_prompt import build_prompt


def usage() -> str:
    return f"Usage: {Path(sys.argv[0]).name} <aggregation-run-file> <output-markdown-file>"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(usage(), file=sys.stderr)
        return 2

    run_file = Path(argv[1])
    output_file = Path(argv[2])

    combined_input = build_prompt(run_file)
    with output_file.open("w", encoding="utf-8") as output_handle:
        completed = subprocess.run(
            ["claude", "-p", "Follow the supplied instructions and return only the Markdown digest."],
            input=combined_input,
            text=True,
            stdout=output_handle,
            check=False,
        )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
