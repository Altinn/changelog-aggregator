import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import changelog_aggregator as ca


QUIET = ca.Progress(quiet=True)
RANGE_PERIOD = ca.period_from_dates(
    datetime(2026, 5, 12, tzinfo=timezone.utc).date(),
    datetime(2026, 5, 18, tzinfo=timezone.utc).date(),
)


class FakeDiscoveryClient:
    def __init__(self):
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        if "/orgs/altinn/repos" in url:
            return (
                [
                    {
                        "name": "repo-a",
                        "full_name": "altinn/repo-a",
                        "html_url": "https://github.com/altinn/repo-a",
                        "default_branch": "main",
                    },
                    {
                        "name": "repo-b",
                        "full_name": "altinn/repo-b",
                        "html_url": "https://github.com/altinn/repo-b",
                        "default_branch": "master",
                    },
                ],
                {},
            )
        if "/repos/altinn/repo-a/git/trees/main" in url:
            return ({"tree": [{"type": "blob", "path": "CHANGELOG.md"}]}, {})
        if "/repos/altinn/repo-b/git/trees/master" in url:
            return ({"tree": [{"type": "blob", "path": "README.md"}]}, {})
        raise AssertionError(f"unexpected url {url}")


class FakeAggregationClient:
    def __init__(self):
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        if "/repos/altinn/repo-a/commits?" in url:
            return (
                [
                    {
                        "sha": "abc123",
                        "commit": {
                            "author": {"date": "2026-05-12T10:00:00Z"},
                            "message": "Update changelog",
                        },
                    }
                ],
                {},
            )
        if "/repos/altinn/repo-a/commits/abc123" in url:
            return (
                {
                    "sha": "abc123",
                    "html_url": "https://github.com/altinn/repo-a/commit/abc123",
                    "commit": {
                        "author": {"date": "2026-05-12T10:00:00Z"},
                        "message": "Update changelog\n\nDetails",
                    },
                    "files": [
                        {
                            "filename": "CHANGELOG.md",
                            "patch": "@@\n ## 1.0.0\n+- Added one\n context\n++ Added literal plus\n--- not metadata in body",
                        },
                        {
                            "filename": "README.md",
                            "patch": "@@\n+- Ignore",
                        },
                    ],
                },
                {},
            )
        raise AssertionError(f"unexpected url {url}")


class EmptyRepoDiscoveryClient:
    def get_json(self, url):
        if "/repos/Altinn/labels/git/trees/main" in url:
            raise ca.GitHubApiError(
                409,
                url,
                '{"message":"Git Repository is empty.","status":"409"}',
            )
        raise AssertionError(f"unexpected url {url}")


class FakeHttpResponse:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def http_error(status, headers=None, body=b'{"message":"rate limited"}'):
    message = Message()
    for key, value in (headers or {}).items():
        message.add_header(key, value)
    return urllib_error(status, message, body)


def urllib_error(status, headers, body):
    return ca.urllib.error.HTTPError(
        "https://api.github.com/rate-limited",
        status,
        "rate limited",
        headers,
        BytesIO(body),
    )


class ChangelogAggregatorTests(unittest.TestCase):
    def test_extract_added_lines_from_patch_ignores_diff_metadata(self):
        patch = """@@ -1,2 +1,3 @@
--- old
+++ new
 context
+Added
++ Literal leading plus
"""

        lines = ca.extract_added_lines_from_patch(patch)

        self.assertEqual(["Added", "+ Literal leading plus"], lines)

    def test_aggregate_uses_commit_metadata_and_patch_lines(self):
        index = {
            "org": "altinn",
            "repos": [
                {
                    "name": "repo-a",
                    "full_name": "altinn/repo-a",
                    "url": "https://github.com/altinn/repo-a",
                    "default_branch": "main",
                    "changelog_path": "CHANGELOG.md",
                    "found": True,
                }
            ],
        }

        additions, errors = ca.aggregate(
            FakeAggregationClient(),
            index,
            RANGE_PERIOD,
            QUIET,
        )

        self.assertEqual([], errors)
        self.assertEqual(1, len(additions))
        self.assertEqual("abc123", additions[0].commit_sha)
        self.assertEqual("Update changelog", additions[0].commit_message)
        self.assertEqual(["- Added one", "+ Added literal plus"], additions[0].added_lines)

    def test_discovery_is_written_when_index_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeDiscoveryClient()
            index = ca.load_or_discover_index(client, "altinn", Path(tmp), False, QUIET)

            self.assertTrue((Path(tmp) / ca.CHANGELOG_INDEX).exists())
            self.assertEqual("altinn", index["org"])
            self.assertEqual(
                ["altinn/repo-a", "altinn/repo-b"],
                [repo["full_name"] for repo in index["repos"]],
            )
            self.assertEqual("CHANGELOG.md", index["repos"][0]["changelog_path"])
            self.assertIsNone(index["repos"][1]["changelog_path"])

    def test_empty_repository_is_treated_as_no_changelog(self):
        repo = {
            "name": "labels",
            "full_name": "Altinn/labels",
            "html_url": "https://github.com/Altinn/labels",
            "default_branch": "main",
        }

        path = ca.find_changelog_path(EmptyRepoDiscoveryClient(), repo, QUIET)

        self.assertIsNone(path)

    def test_existing_index_skips_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / ca.CHANGELOG_INDEX
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                json.dumps(
                    {
                        "org": "altinn",
                        "discoveredAt": "2026-05-12T00:00:00Z",
                        "repos": [],
                    }
                ),
                encoding="utf-8",
            )
            client = FakeDiscoveryClient()

            index = ca.load_or_discover_index(client, "altinn", Path(tmp), False, QUIET)

            self.assertEqual([], index["repos"])
            self.assertEqual([], client.urls)

    def test_llm_report_uses_repo_and_commit_dividers(self):
        addition = ca.ChangelogAddition(
            repo="altinn/example",
            repo_url="https://github.com/altinn/example",
            path="CHANGELOG.md",
            file_url="https://github.com/altinn/example/blob/main/CHANGELOG.md",
            commit_sha="abc123",
            commit_url="https://github.com/altinn/example/commit/abc123",
            commit_date="2026-05-12T10:00:00Z",
            commit_message="Update changelog",
            added_lines=["## 1.0.0", "- Added"],
        )

        report = ca.render_llm("altinn", RANGE_PERIOD, [addition], [])

        self.assertIn("CHANGELOG_AGGREGATION_FORMAT_VERSION: 1", report)
        self.assertIn("PERIOD_LABEL: 2026-05-12_to_2026-05-18", report)
        self.assertIn("FROM_DATE: 2026-05-12", report)
        self.assertIn("TO_DATE: 2026-05-18", report)
        self.assertIn("===== REPOSITORY START =====", report)
        self.assertIn("REPO: altinn/example", report)
        self.assertIn("ADDED_LINES_START\n## 1.0.0\n- Added\nADDED_LINES_END", report)

    def test_progress_writes_to_stderr_unless_quiet(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            ca.Progress().log("working")
            ca.Progress(quiet=True).log("hidden")

        self.assertIn("[changelog-aggregator] working", stderr.getvalue())
        self.assertNotIn("hidden", stderr.getvalue())

    def test_default_output_path_uses_period_label_and_format_extension(self):
        path = ca.default_output_path(
            Path(".changelog-aggregator"),
            "json",
            RANGE_PERIOD,
        )

        self.assertEqual(
            Path(".changelog-aggregator/runs/changelog-aggregation-2026-05-12_to_2026-05-18.json"),
            path,
        )

    def test_write_report_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "nested" / "report.txt"

            ca.write_report(output_path, "content\n", QUIET)

            self.assertEqual("content\n", output_path.read_text(encoding="utf-8"))

    def test_github_client_retries_429_using_retry_after(self):
        sleeps = []
        responses = [
            http_error(429, {"retry-after": "2"}),
            FakeHttpResponse(b'{"ok": true}'),
        ]

        def fake_urlopen(_request, timeout):
            self.assertEqual(30, timeout)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        client = ca.GitHubClient(max_rate_limit_retries=1, sleep_func=sleeps.append)
        with patch.object(ca.urllib.request, "urlopen", fake_urlopen):
            data, _headers = client.get_json("https://api.github.com/example")

        self.assertEqual({"ok": True}, data)
        self.assertEqual([2.0], sleeps)

    def test_rate_limit_delay_uses_reset_header_when_remaining_is_zero(self):
        with patch.object(ca.time, "time", return_value=1000):
            delay = ca.GitHubClient._rate_limit_delay(
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1010"},
                attempt=0,
            )

        self.assertEqual(11.0, delay)

    def test_parse_iso_week_uses_monday_through_sunday(self):
        period = ca.parse_iso_week("2026-W20")

        self.assertEqual("2026-W20", period.label)
        self.assertEqual("2026-W20", period.week)
        self.assertEqual("2026-05-11", period.start_date.isoformat())
        self.assertEqual("2026-05-17", period.end_date.isoformat())

    def test_period_from_dates_is_inclusive_through_end_of_to_date(self):
        period = ca.period_from_dates(
            datetime(2026, 5, 1, tzinfo=timezone.utc).date(),
            datetime(2026, 5, 7, tzinfo=timezone.utc).date(),
        )

        self.assertEqual("2026-05-01T00:00:00Z", period.start.isoformat().replace("+00:00", "Z"))
        self.assertTrue(period.end.isoformat().startswith("2026-05-07T23:59:59.999999"))

    def test_commit_query_contains_since_and_until(self):
        client = FakeAggregationClient()
        repo = {
            "full_name": "altinn/repo-a",
            "default_branch": "main",
            "changelog_path": "CHANGELOG.md",
        }

        ca.list_commits_for_path(client, repo, RANGE_PERIOD, QUIET)

        self.assertTrue(any("since=2026-05-12T00%3A00%3A00Z" in url for url in client.urls))
        self.assertTrue(any("until=2026-05-18T23%3A59%3A59.999999Z" in url for url in client.urls))


if __name__ == "__main__":
    unittest.main()
