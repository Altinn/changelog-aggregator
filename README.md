# Changelog Aggregator

Aggregates lines added to changelog files in public repositories in a GitHub organization.

The output is intentionally machine-oriented: it is delimited so an LLM or another
processor can later identify which repository and commit each block came from.

## Usage

```bash
python3 changelog_aggregator.py --org altinn --week 2026-W20
```

You can also run a custom inclusive date range:

```bash
python3 changelog_aggregator.py --org altinn --from 2026-05-01 --to 2026-05-07
```

Each run writes its report to a period-specific file under:

```text
.changelog-aggregator/runs/
```

The first run discovers public repositories and stores changelog paths in:

```text
.changelog-aggregator/changelogs.json
```

Later runs reuse that file. Force rediscovery with:

```bash
python3 changelog_aggregator.py --org altinn --week 2026-W20 --discover
```

Write to a specific report file:

```bash
python3 changelog_aggregator.py --org altinn --week 2026-W20 --output report.md
```

Write to stdout explicitly:

```bash
python3 changelog_aggregator.py --org altinn --week 2026-W20 --output -
```

Emit JSON:

```bash
python3 changelog_aggregator.py --org altinn --week 2026-W20 --format json
```

The default `llm` format contains only metadata and added changelog lines from
commits touching each discovered changelog file. It does not parse Markdown
sections or infer release structure from changelog contents.

The script prints progress to stderr while it works, keeping stdout reserved for
the report. Suppress progress output with:

```bash
python3 changelog_aggregator.py --org altinn --week 2026-W20 --quiet
```

GitHub rate-limit responses are retried automatically. The script honors
`retry-after` first, then `x-ratelimit-reset` when `x-ratelimit-remaining` is
`0`, and otherwise uses exponential backoff. Tune retry attempts with:

```bash
python3 changelog_aggregator.py --org altinn --week 2026-W20 --max-rate-limit-retries 8
```

## Turn Aggregation Output Into a Human Digest

`prompt.txt` contains instructions for converting a machine-oriented aggregation
run into a human-readable Markdown digest.

Set the aggregation file you want to summarize:

```bash
RUN=.changelog-aggregator/runs/changelog-aggregation-2026-W20.txt
```

Run Codex non-interactively and write the final Markdown message to `digest.md`:

```bash
cat prompt.txt "$RUN" | codex exec -o digest.md -
```

Run Claude Code non-interactively and redirect the Markdown output to
`digest.md`:

```bash
cat prompt.txt "$RUN" \
  | claude -p "Follow the supplied instructions and return only the Markdown digest." \
  > digest.md
```

Set `GITHUB_TOKEN` to increase GitHub API limits.
