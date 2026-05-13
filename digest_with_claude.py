#!/usr/bin/env python3
"""Build a dynamic digest prompt and run Claude non-interactively."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def usage() -> str:
    return f"Usage: {Path(sys.argv[0]).name} <aggregation-run-file> <output-markdown-file>"


def build_prompt(script_dir: Path) -> str:
    prompt_path = script_dir / "prompt.txt"
    repo_names_path = script_dir / "reponames.json"

    if not prompt_path.exists():
        raise SystemExit(f"Prompt file not found: {prompt_path}")

    prompt = prompt_path.read_text(encoding="utf-8")
    if "{{REPONAMES}}" not in prompt:
        raise SystemExit("prompt.txt must contain the {{REPONAMES}} placeholder.")

    repo_names: dict[str, str] = {}
    if repo_names_path.exists():
        raw = json.loads(repo_names_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SystemExit(f"{repo_names_path} must contain a JSON object.")
        repo_names = {
            key: value
            for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str) and key != value
        }

    if repo_names:
        replacements = "\n".join(
            f"  - Replace repository heading `{repo}` with `{display_name}`."
            for repo, display_name in sorted(repo_names.items(), key=lambda item: item[0].lower())
        )
    else:
        replacements = "  - No repository heading replacements are configured."

    return prompt.replace("{{REPONAMES}}", replacements)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(usage(), file=sys.stderr)
        return 2

    run_file = Path(argv[1])
    output_file = Path(argv[2])
    script_dir = Path(__file__).resolve().parent

    if not run_file.exists():
        print(f"Aggregation run file not found: {run_file}", file=sys.stderr)
        return 2

    combined_input = build_prompt(script_dir) + "\n" + run_file.read_text(encoding="utf-8")
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
