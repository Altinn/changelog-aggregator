#!/usr/bin/env python3
"""Build the final digest prompt from prompt.txt, reponames.json, and a run file."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPONAMES_PLACEHOLDER = "{{REPONAMES}}"


def load_repo_name_overrides(repo_names_path: Path) -> dict[str, str]:
    if not repo_names_path.exists():
        return {}

    raw = json.loads(repo_names_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"{repo_names_path} must contain a JSON object.")

    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str) and key != value
    }


def render_repo_name_replacements(repo_names: dict[str, str]) -> str:
    if not repo_names:
        return "  - No repository heading replacements are configured."
    return "\n".join(
        f"  - Replace repository heading `{repo}` with `{display_name}`."
        for repo, display_name in sorted(repo_names.items(), key=lambda item: item[0].lower())
    )


def build_prompt(
    run_file: Path,
    prompt_path: Path | None = None,
    repo_names_path: Path | None = None,
) -> str:
    repo_root = Path(__file__).resolve().parent
    prompt_path = prompt_path or repo_root / "prompt.txt"
    repo_names_path = repo_names_path or repo_root / "reponames.json"

    if not prompt_path.exists():
        raise SystemExit(f"Prompt file not found: {prompt_path}")
    if not run_file.exists():
        raise SystemExit(f"Aggregation run file not found: {run_file}")

    prompt = prompt_path.read_text(encoding="utf-8")
    if REPONAMES_PLACEHOLDER not in prompt:
        raise SystemExit(f"{prompt_path} must contain the {REPONAMES_PLACEHOLDER} placeholder.")

    replacements = render_repo_name_replacements(load_repo_name_overrides(repo_names_path))
    generated_prompt = prompt.replace(REPONAMES_PLACEHOLDER, replacements)
    return generated_prompt + "\n" + run_file.read_text(encoding="utf-8")


def usage() -> str:
    return (
        f"Usage: {Path(sys.argv[0]).name} <aggregation-run-file> <output-prompt-file> "
        "[prompt-file] [reponames-file]"
    )


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4, 5):
        print(usage(), file=sys.stderr)
        return 2

    run_file = Path(argv[1])
    output_file = Path(argv[2])
    prompt_path = Path(argv[3]) if len(argv) >= 4 else None
    repo_names_path = Path(argv[4]) if len(argv) == 5 else None

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        build_prompt(run_file, prompt_path=prompt_path, repo_names_path=repo_names_path),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
