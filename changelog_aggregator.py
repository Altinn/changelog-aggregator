#!/usr/bin/env python3
"""Aggregate changelog additions from public GitHub repositories."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any


DEFAULT_CACHE_DIR = Path(".changelog-aggregator")
CHANGELOG_INDEX = "changelogs.json"
STATE_FILE = "state.json"
RUNS_DIR = "runs"
API_ROOT = "https://api.github.com"

PREFERRED_CHANGELOG_PATHS = [
    "CHANGELOG.md",
    "Changelog.md",
    "changelog.md",
    "RELEASES.md",
    "RELEASE_NOTES.md",
    "docs/CHANGELOG.md",
    ".github/CHANGELOG.md",
]

CHANGELOG_BASENAMES = {
    "CHANGELOG.md",
    "CHANGELOG.MD",
    "Changelog.md",
    "changelog.md",
    "RELEASES.md",
    "RELEASE_NOTES.md",
}
CHANGELOG_BASENAMES_LOWER = {name.lower() for name in CHANGELOG_BASENAMES}


@dataclass
class ChangelogRepo:
    name: str
    full_name: str
    url: str
    default_branch: str
    changelog_path: str | None
    found: bool


@dataclass
class ChangelogAddition:
    repo: str
    repo_url: str
    path: str
    file_url: str
    commit_sha: str
    commit_url: str
    commit_date: str
    commit_message: str
    added_lines: list[str]


@dataclass
class Period:
    label: str
    start_date: date
    end_date: date
    start: datetime
    end: datetime
    week: str | None = None


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        progress: Any | None = None,
        max_rate_limit_retries: int = 5,
        sleep_func: Any = time.sleep,
    ) -> None:
        self.token = token
        self.progress = progress
        self.max_rate_limit_retries = max_rate_limit_retries
        self.sleep_func = sleep_func

    def get_json(self, url: str) -> tuple[Any, dict[str, str]]:
        request = urllib.request.Request(url)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "changelog-aggregator")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        for attempt in range(self.max_rate_limit_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    return json.loads(response.read().decode("utf-8")), headers
            except urllib.error.HTTPError as exc:
                headers = {key.lower(): value for key, value in exc.headers.items()}
                detail = exc.read().decode("utf-8", errors="replace")
                if self._is_rate_limited(exc.code, headers, detail) and attempt < self.max_rate_limit_retries:
                    delay = self._rate_limit_delay(headers, attempt)
                    self._log(
                        f"GitHub rate limit response {exc.code}; retrying in {delay:.0f}s "
                        f"(attempt {attempt + 1}/{self.max_rate_limit_retries})."
                    )
                    self.sleep_func(delay)
                    continue
                raise GitHubApiError(exc.code, url, detail, headers) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"GitHub API request failed: {url}\n{exc}") from exc

        raise RuntimeError(f"GitHub API request failed after retries: {url}")

    def _log(self, message: str) -> None:
        if self.progress:
            self.progress.log(message)

    @staticmethod
    def _is_rate_limited(status: int, headers: dict[str, str], detail: str) -> bool:
        if status == 429:
            return True
        if status == 403 and headers.get("x-ratelimit-remaining") == "0":
            return True
        if status == 403 and "rate limit" in detail.lower():
            return True
        return False

    @staticmethod
    def _rate_limit_delay(headers: dict[str, str], attempt: int) -> float:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass

        if headers.get("x-ratelimit-remaining") == "0" and headers.get("x-ratelimit-reset"):
            try:
                reset_at = float(headers["x-ratelimit-reset"])
                return max(0.0, reset_at - time.time()) + 1.0
            except ValueError:
                pass

        return 60.0 * (2**attempt)


class GitHubApiError(RuntimeError):
    def __init__(self, status: int, url: str, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"GitHub API request failed: {status} {url}\n{detail}")
        self.status = status
        self.url = url
        self.detail = detail
        self.headers = headers or {}


class Progress:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def log(self, message: str) -> None:
        if not self.quiet:
            print(f"[changelog-aggregator] {message}", file=sys.stderr, flush=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate GitHub changelog additions.")
    parser.add_argument("--org", default="altinn", help="GitHub organization to scan.")
    parser.add_argument("--week", help="ISO 8601 week in YYYY-Www form, for example 2026-W20.")
    parser.add_argument("--from", dest="from_date", help="Inclusive start date in YYYY-MM-DD form.")
    parser.add_argument("--to", dest="to_date", help="Inclusive end date in YYYY-MM-DD form.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Directory for cache/state files.")
    parser.add_argument("--discover", action="store_true", help="Refresh changelog discovery cache.")
    parser.add_argument(
        "--refresh-changelog-index",
        action="store_true",
        help="Alias for --discover.",
    )
    parser.add_argument(
        "--output",
        help=(
            "Write report to this file. Defaults to a period-named file under "
            ".changelog-aggregator/runs/. Use '-' for stdout."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output on stderr.")
    parser.add_argument(
        "--max-rate-limit-retries",
        type=int,
        default=5,
        help="Maximum number of retries for GitHub 403/429 rate-limit responses.",
    )
    parser.add_argument(
        "--format",
        choices=("llm", "json"),
        default="llm",
        help="Report output format.",
    )
    return parser.parse_args(argv)


def parse_cli_date(value: str, flag_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid {flag_name} {value!r}; expected YYYY-MM-DD.") from exc


def period_from_dates(start_date: date, end_date: date) -> Period:
    if end_date < start_date:
        raise SystemExit("--to must be on or after --from.")
    start = datetime.combine(start_date, datetime_time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_date, datetime_time.max, tzinfo=timezone.utc)
    label = f"{start_date.isoformat()}_to_{end_date.isoformat()}"
    return Period(label=label, start_date=start_date, end_date=end_date, start=start, end=end)


def parse_iso_week(value: str) -> Period:
    normalized = value.upper()
    if len(normalized) != 8 or normalized[4:6] != "-W" or not normalized[:4].isdigit() or not normalized[6:].isdigit():
        raise SystemExit(f"Invalid --week {value!r}; expected ISO week in YYYY-Www form.")
    year = int(normalized[:4])
    week = int(normalized[6:])
    try:
        start_date = date.fromisocalendar(year, week, 1)
        end_date = date.fromisocalendar(year, week, 7)
    except ValueError as exc:
        raise SystemExit(f"Invalid --week {value!r}; expected a valid ISO 8601 week.") from exc
    period = period_from_dates(start_date, end_date)
    period.label = normalized
    period.week = normalized
    return period


def resolve_period(args: argparse.Namespace) -> Period:
    if args.week:
        if args.from_date or args.to_date:
            raise SystemExit("Use either --week or --from/--to, not both.")
        return parse_iso_week(args.week)
    if args.from_date or args.to_date:
        if not args.from_date or not args.to_date:
            raise SystemExit("Use both --from and --to together.")
        return period_from_dates(
            parse_cli_date(args.from_date, "--from"),
            parse_cli_date(args.to_date, "--to"),
        )
    raise SystemExit("Provide either --week YYYY-Www or both --from YYYY-MM-DD and --to YYYY-MM-DD.")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_public_repos(client: GitHubClient, org: str, progress: Progress) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    encoded_org = urllib.parse.quote(org, safe="")
    while True:
        progress.log(f"Fetching public repositories for {org}, page {page}.")
        url = f"{API_ROOT}/orgs/{encoded_org}/repos?type=public&per_page=100&page={page}"
        data, _headers = client.get_json(url)
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected repository response for {org}: {data!r}")
        repos.extend(data)
        if len(data) < 100:
            progress.log(f"Discovered {len(repos)} public repositories for {org}.")
            return repos
        page += 1


def find_changelog_path(client: GitHubClient, repo: dict[str, Any], progress: Progress) -> str | None:
    full_name = repo["full_name"]
    progress.log(f"Scanning {full_name} for changelog files.")
    branch = repo["default_branch"]
    encoded_branch = urllib.parse.quote(branch, safe="")
    url = f"{API_ROOT}/repos/{full_name}/git/trees/{encoded_branch}?recursive=1"
    try:
        data, _headers = client.get_json(url)
    except GitHubApiError as exc:
        if exc.status == 409 and "Git Repository is empty" in exc.detail:
            progress.log(f"Skipping {full_name}: repository is empty.")
            return None
        raise
    tree = data.get("tree", [])
    blob_paths = [
        item.get("path")
        for item in tree
        if item.get("type") == "blob" and isinstance(item.get("path"), str)
    ]

    paths_by_lower = {path.lower(): path for path in blob_paths}
    for preferred in PREFERRED_CHANGELOG_PATHS:
        match = paths_by_lower.get(preferred.lower())
        if match:
            progress.log(f"Found changelog for {full_name}: {match}.")
            return match

    candidates = [
        path
        for path in blob_paths
        if path.rsplit("/", 1)[-1] in CHANGELOG_BASENAMES
        or path.rsplit("/", 1)[-1].lower() in CHANGELOG_BASENAMES_LOWER
    ]
    if not candidates:
        progress.log(f"No changelog found for {full_name}.")
        return None
    match = sorted(candidates, key=lambda path: (path.count("/"), path.lower()))[0]
    progress.log(f"Found changelog for {full_name}: {match}.")
    return match


def discover_changelogs(client: GitHubClient, org: str, progress: Progress) -> dict[str, Any]:
    progress.log(f"Starting changelog discovery for {org}.")
    repos = []
    public_repos = list_public_repos(client, org, progress)
    for position, repo in enumerate(public_repos, start=1):
        progress.log(f"Discovering changelog path {position}/{len(public_repos)}: {repo['full_name']}.")
        path = find_changelog_path(client, repo, progress)
        repos.append(
            asdict(
                ChangelogRepo(
                    name=repo["name"],
                    full_name=repo["full_name"],
                    url=repo["html_url"],
                    default_branch=repo["default_branch"],
                    changelog_path=path,
                    found=path is not None,
                )
            )
        )

    found_count = sum(1 for repo in repos if repo["found"])
    progress.log(f"Changelog discovery complete: {found_count}/{len(repos)} repositories have changelogs.")
    return {
        "org": org,
        "discoveredAt": utc_now_iso(),
        "repos": sorted(repos, key=lambda item: item["full_name"].lower()),
    }


def read_index(index_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{index_path} is invalid JSON. Rerun with --discover to replace it.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        raise SystemExit(f"{index_path} has an invalid schema. Rerun with --discover to replace it.")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_output_path(cache_dir: Path, output_format: str, period: Period) -> Path:
    extension = "txt" if output_format == "llm" else "json"
    return cache_dir / RUNS_DIR / f"changelog-aggregation-{period.label}.{extension}"


def github_file_url(repo: dict[str, Any]) -> str:
    branch = urllib.parse.quote(repo["default_branch"], safe="")
    path = repo["changelog_path"]
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    return f"{repo['url']}/blob/{branch}/{encoded_path}"


def list_commits_for_path(
    client: GitHubClient,
    repo: dict[str, Any],
    period: Period,
    progress: Progress,
) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    page = 1
    since_value = urllib.parse.quote(period.start.isoformat().replace("+00:00", "Z"), safe="")
    until_value = urllib.parse.quote(period.end.isoformat().replace("+00:00", "Z"), safe="")
    path = "/".join(urllib.parse.quote(part, safe="") for part in repo["changelog_path"].split("/"))
    branch = urllib.parse.quote(repo["default_branch"], safe="")
    while True:
        progress.log(f"Fetching commits for {repo['full_name']}:{repo['changelog_path']}, page {page}.")
        url = (
            f"{API_ROOT}/repos/{repo['full_name']}/commits"
            f"?sha={branch}&path={path}&since={since_value}&until={until_value}&per_page=100&page={page}"
        )
        data, _headers = client.get_json(url)
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected commits response for {repo['full_name']}: {data!r}")
        commits.extend(data)
        if len(data) < 100:
            progress.log(f"Found {len(commits)} commits touching {repo['full_name']}:{repo['changelog_path']}.")
            return commits
        page += 1


def get_commit(client: GitHubClient, repo: dict[str, Any], sha: str, progress: Progress) -> dict[str, Any]:
    progress.log(f"Fetching commit details for {repo['full_name']}@{sha[:12]}.")
    encoded_sha = urllib.parse.quote(sha, safe="")
    data, _headers = client.get_json(f"{API_ROOT}/repos/{repo['full_name']}/commits/{encoded_sha}")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected commit response for {repo['full_name']}@{sha}: {data!r}")
    return data


def extract_added_lines_from_patch(patch: str) -> list[str]:
    lines: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return lines


def addition_from_commit(
    client: GitHubClient,
    repo: dict[str, Any],
    commit_summary: dict[str, Any],
    progress: Progress,
) -> ChangelogAddition | None:
    sha = commit_summary["sha"]
    commit = get_commit(client, repo, sha, progress)
    added_lines: list[str] = []
    for file_info in commit.get("files", []):
        if file_info.get("filename") != repo["changelog_path"]:
            continue
        patch = file_info.get("patch")
        if isinstance(patch, str):
            added_lines.extend(extract_added_lines_from_patch(patch))
    if not added_lines:
        progress.log(f"No added changelog lines in {repo['full_name']}@{sha[:12]}.")
        return None
    progress.log(f"Extracted {len(added_lines)} added changelog lines from {repo['full_name']}@{sha[:12]}.")

    commit_info = commit.get("commit", {})
    author_info = commit_info.get("author") or commit_info.get("committer") or {}
    message = commit_info.get("message", "")
    return ChangelogAddition(
        repo=repo["full_name"],
        repo_url=repo["url"],
        path=repo["changelog_path"],
        file_url=github_file_url(repo),
        commit_sha=sha,
        commit_url=commit.get("html_url", f"{repo['url']}/commit/{sha}"),
        commit_date=author_info.get("date", ""),
        commit_message=message.splitlines()[0] if message else "",
        added_lines=added_lines,
    )


def aggregate(
    client: GitHubClient,
    index: dict[str, Any],
    period: Period,
    progress: Progress,
) -> tuple[list[ChangelogAddition], list[dict[str, str]]]:
    additions: list[ChangelogAddition] = []
    errors: list[dict[str, str]] = []

    repos_with_changelogs = [
        repo for repo in index["repos"] if repo.get("found") and repo.get("changelog_path")
    ]
    progress.log(f"Scanning {len(repos_with_changelogs)} cached changelog paths for changes.")
    for position, repo in enumerate(repos_with_changelogs, start=1):
        progress.log(f"Processing repository {position}/{len(repos_with_changelogs)}: {repo['full_name']}.")
        if not repo.get("found") or not repo.get("changelog_path"):
            continue
        try:
            for commit in list_commits_for_path(client, repo, period, progress):
                addition = addition_from_commit(client, repo, commit, progress)
                if addition:
                    additions.append(addition)
        except Exception as exc:  # noqa: BLE001 - report all repo-level failures and continue.
            errors.append({"repo": repo.get("full_name", repo.get("name", "unknown")), "error": str(exc)})
            progress.log(f"Error while processing {repo.get('full_name', repo.get('name', 'unknown'))}: {exc}")

    additions.sort(key=lambda item: (item.repo.lower(), item.commit_date, item.commit_sha))
    errors.sort(key=lambda item: item["repo"].lower())
    line_count = sum(len(addition.added_lines) for addition in additions)
    progress.log(f"Aggregation complete: {len(additions)} commits with {line_count} added lines, {len(errors)} errors.")
    return additions, errors


def render_llm(
    org: str,
    period: Period,
    additions: list[ChangelogAddition],
    errors: list[dict[str, str]],
) -> str:
    output = [
        "CHANGELOG_AGGREGATION_FORMAT_VERSION: 1",
        f"ORG: {org}",
        f"PERIOD_LABEL: {period.label}",
        f"FROM_DATE: {period.start_date.isoformat()}",
        f"TO_DATE: {period.end_date.isoformat()}",
        f"FROM_TIMESTAMP: {period.start.isoformat().replace('+00:00', 'Z')}",
        f"TO_TIMESTAMP: {period.end.isoformat().replace('+00:00', 'Z')}",
    ]
    if period.week:
        output.append(f"ISO_WEEK: {period.week}")

    current_repo = None
    for addition in additions:
        if current_repo != addition.repo:
            current_repo = addition.repo
            output.extend(
                [
                    "",
                    "===== REPOSITORY START =====",
                    f"REPO: {addition.repo}",
                    f"REPO_URL: {addition.repo_url}",
                    f"CHANGELOG_PATH: {addition.path}",
                    f"CHANGELOG_URL: {addition.file_url}",
                    "===== REPOSITORY CHANGELOG ADDITIONS =====",
                ]
            )
        output.extend(
            [
                "",
                "----- COMMIT START -----",
                f"COMMIT_SHA: {addition.commit_sha}",
                f"COMMIT_DATE: {addition.commit_date}",
                f"COMMIT_URL: {addition.commit_url}",
                f"COMMIT_MESSAGE: {addition.commit_message}",
                "ADDED_LINES_START",
            ]
        )
        output.extend(addition.added_lines)
        output.extend(["ADDED_LINES_END", "----- COMMIT END -----"])

    if current_repo is not None:
        output.extend(["", "===== REPOSITORY END ====="])

    if not additions:
        output.extend(["", "NO_CHANGELOG_ADDITIONS_FOUND"])

    if errors:
        output.extend(["", "===== ERRORS START ====="])
        for error in errors:
            output.extend([f"ERROR_REPO: {error['repo']}", f"ERROR: {error['error']}"])
        output.append("===== ERRORS END =====")

    return "\n".join(output).rstrip() + "\n"


def render_json(
    org: str,
    period: Period,
    additions: list[ChangelogAddition],
    errors: list[dict[str, str]],
) -> str:
    payload = {
        "org": org,
        "period": {
            "label": period.label,
            "isoWeek": period.week,
            "fromDate": period.start_date.isoformat(),
            "toDate": period.end_date.isoformat(),
            "fromTimestamp": period.start.isoformat().replace("+00:00", "Z"),
            "toTimestamp": period.end.isoformat().replace("+00:00", "Z"),
        },
        "additions": [asdict(addition) for addition in additions],
        "errors": errors,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def save_state(
    state_path: Path,
    org: str,
    period: Period,
    index: dict[str, Any],
    additions: list[ChangelogAddition],
    errors: list[dict[str, str]],
) -> None:
    state = {
        "lastRunAt": utc_now_iso(),
        "org": org,
        "period": {
            "label": period.label,
            "isoWeek": period.week,
            "fromDate": period.start_date.isoformat(),
            "toDate": period.end_date.isoformat(),
            "fromTimestamp": period.start.isoformat().replace("+00:00", "Z"),
            "toTimestamp": period.end.isoformat().replace("+00:00", "Z"),
        },
        "indexDiscoveredAt": index.get("discoveredAt"),
        "additionCommitCount": len(additions),
        "addedLineCount": sum(len(addition.added_lines) for addition in additions),
        "errorCount": len(errors),
    }
    write_json(state_path, state)


def load_or_discover_index(
    client: GitHubClient,
    org: str,
    cache_dir: Path,
    force_discovery: bool,
    progress: Progress,
) -> dict[str, Any]:
    index_path = cache_dir / CHANGELOG_INDEX
    if force_discovery or not index_path.exists():
        if force_discovery:
            progress.log(f"Refreshing changelog index at {index_path}.")
        else:
            progress.log(f"No changelog index found at {index_path}; running discovery.")
        index = discover_changelogs(client, org, progress)
        write_json(index_path, index)
        progress.log(f"Wrote changelog index to {index_path}.")
        return index
    progress.log(f"Loading cached changelog index from {index_path}.")
    index = read_index(index_path)
    if index.get("org") != org:
        raise SystemExit(
            f"{index_path} was created for org {index.get('org')!r}, not {org!r}. "
            "Use --discover to replace it."
        )
    return index


def write_report(path: str | Path, content: str, progress: Progress) -> None:
    if str(path) == "-":
        progress.log("Writing report to stdout.")
        sys.stdout.write(content)
        return

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    progress.log(f"Wrote report to {output_path}.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    period = resolve_period(args)
    cache_dir = Path(args.cache_dir)
    force_discovery = args.discover or args.refresh_changelog_index
    progress = Progress(args.quiet)
    client = GitHubClient(
        os.environ.get("GITHUB_TOKEN"),
        progress=progress,
        max_rate_limit_retries=args.max_rate_limit_retries,
    )

    progress.log(
        f"Starting run for org={args.org}, period={period.label} "
        f"({period.start.isoformat().replace('+00:00', 'Z')} to "
        f"{period.end.isoformat().replace('+00:00', 'Z')})."
    )
    progress.log("Using GITHUB_TOKEN authentication." if client.token else "No GITHUB_TOKEN set; using unauthenticated GitHub API requests.")
    index = load_or_discover_index(client, args.org, cache_dir, force_discovery, progress)
    additions, errors = aggregate(client, index, period, progress)

    if args.format == "json":
        report = render_json(args.org, period, additions, errors)
    else:
        report = render_llm(args.org, period, additions, errors)

    output_path = args.output if args.output else default_output_path(cache_dir, args.format, period)
    write_report(output_path, report, progress)
    save_state(cache_dir / STATE_FILE, args.org, period, index, additions, errors)
    progress.log(f"Wrote run state to {cache_dir / STATE_FILE}.")
    return 1 if errors and not additions else 0


if __name__ == "__main__":
    raise SystemExit(main())
