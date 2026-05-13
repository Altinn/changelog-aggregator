# Changelog Aggregator Plan

## Summary
Build a CLI changelog aggregator that discovers public repositories under `altinn`, caches discovered changelog paths on first run, and later uses GitHub commit metadata and diffs to report lines added to those changelog files for either an ISO week or an explicit inclusive date range. Rediscovery only happens when explicitly requested or when the cache is missing.

## Key Changes
- Create a CLI script for:
  - `--org altinn`
  - `--week YYYY-Www`
  - or `--from YYYY-MM-DD --to YYYY-MM-DD`
  - `--discover` or `--refresh-changelog-index`
  - `--output report.md`
  - `--format llm|json`
- On first run, discover public GitHub repos in the org and search for likely changelog files such as `CHANGELOG.md`, `changelog.md`, `RELEASES.md`, `RELEASE_NOTES.md`, and `docs/CHANGELOG.md`.
- During discovery, also detect `.github/release.yml` and treat published GitHub Releases as a second changelog source for those repositories.
- Store discovery results in `.changelog-aggregator/changelogs.json`.
- Store editable repository display names in `.changelog-aggregator/reponames.json`, defaulting each value to the full repository key so `key != value` identifies real overrides. Duplicate display-name values are allowed so related repositories can collapse into one logical digest section.
- On later runs, read `.changelog-aggregator/changelogs.json` and skip repository discovery unless rediscovery is explicitly requested.
- Store run metadata separately in `.changelog-aggregator/state.json`.

## Behavior
- If `changelogs.json` does not exist, run discovery automatically.
- If `changelogs.json` exists, use it as the source of truth.
- If `--discover` is provided, rerun discovery and overwrite `changelogs.json`.
- Query commits that touched each cached changelog path within the requested period.
- Fetch commit details and extract only added lines from the changelog file patch.
- Fetch published GitHub Releases for repositories with `.github/release.yml` and include release bodies published inside the requested period.
- Do not parse Markdown sections or infer release structure from changelog contents.
- Emit an LLM-oriented text format by default, grouped by repository, commit, and release with explicit dividers plus repository display-name mappings.
- Support JSON output for automation.

## Test Plan
- Run first invocation with no cache and verify `.changelog-aggregator/changelogs.json` is created.
- Run second invocation and verify no discovery API calls are made.
- Run with `--discover` and verify the cache is refreshed.
- Test repositories with:
  - no changelog
  - root `CHANGELOG.md`
  - nested `docs/CHANGELOG.md`
  - commits with changelog additions
  - commits that touch changelogs without added lines
- Test output in both LLM text and JSON formats.

## Assumptions
- Python is the default implementation language.
- GitHub API access uses `GITHUB_TOKEN` when available, with unauthenticated fallback.
- Archived public repos are included unless filtered later by explicit option.
- Discovery cache and run state are separate files.
