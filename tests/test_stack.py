from datetime import datetime, timezone

import pytest

from digest.stack import Release, classify, parse_releases

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(("tag", "notes", "kind"), [
    ("v2.0.0", "", "major"),
    ("v1.2.0", "", "minor"),
    ("rust-v0.163.0", "", "minor"),
    ("n8n@2.45.0", "", "minor"),
    ("v22.8", "", "minor"),
    ("v0.65.0", "", "minor"),
    # Патч-релизы шумят: показываем только с ломающими изменениями
    ("v1.2.3", "Fixed a crash.", None),
    ("v1.2.4", "## BREAKING CHANGES\nRenamed the config key.", "breaking"),
    # Отрицания — обычный патч: «No breaking changes» в заметках не делает его ломающим
    ("v1.2.5", "No breaking changes.", None),
    ("v1.2.6", "Non-breaking bug fixes.", None),
    ("v1.2.7", "Fixes without breaking the API.", None),
    ("v1.2.8", "No breaking changes in the CLI.\n\nBREAKING: removed the v1 endpoint.", "breaking"),
    ("n8n@2.44.5", "", None),
    # Пре-релизы и ночные сборки — никогда, даже минорные
    ("v1.2.3-rc1", "", None),
    ("v1.3.0-rc1", "", None),
    ("0.164.0-alpha.9", "", None),
    ("v0.67.0-nightly.20261021.gd00859d6b", "", None),
    ("v0.66.0-preview.0", "", None),
    ("v5.0.0.dev0", "", None),
    # Не версия: сборки llama.cpp и плавающий тег n8n
    ("b11798", "", None),
    ("stable", "", None),
])
def test_only_minor_major_and_breaking_releases_pass(tag, notes, kind):
    assert classify(tag, notes) == kind


def api_release(tag: str, published: str, prerelease: bool = False, body: str = "") -> dict:
    return {"tag_name": tag, "name": f"Release {tag}", "prerelease": prerelease, "draft": False, "body": body,
            "published_at": published, "html_url": f"https://github.com/o/r/releases/tag/{tag}"}


def test_feed_keeps_fresh_minor_releases_and_reads_the_version_from_the_tag():
    payload = [api_release("v0.67.0-nightly.20261021", "2026-10-21T05:00:00Z", prerelease=True),
               api_release("v0.65.0", "2026-10-20T21:00:00Z"),
               api_release("v0.64.1", "2026-10-20T20:00:00Z"),
               api_release("v0.64.0", "2026-10-13T20:00:00Z")]
    releases = parse_releases(payload, {"name": "Gemini CLI", "repo": "google-gemini/gemini-cli"}, NOW, max_age_hours=30)
    # Имя релиза — «Release v0.65.0»; версия берётся из тега, а релиз недельной давности — уже не новость
    assert releases == [Release("Gemini CLI", "v0.65.0", "minor", "https://github.com/o/r/releases/tag/v0.65.0",
                                datetime(2026, 10, 20, 21, tzinfo=timezone.utc))]


def test_release_marked_prerelease_on_github_is_skipped_even_without_a_suffix():
    # n8n@2.45.0 21.10: тег без суффикса, но на GitHub это бета — флаг prerelease важнее тега
    payload = [api_release("n8n@2.45.0", "2026-10-20T21:00:00Z", prerelease=True)]
    assert parse_releases(payload, {"name": "n8n", "repo": "n8n-io/n8n"}, NOW) == []


def test_breaking_patch_is_found_in_the_release_notes():
    payload = [api_release("v4.14.10", "2026-10-21T08:00:00Z", body="### Breaking changes\n- removed the old API")]
    assert [r.kind for r in parse_releases(payload, {"name": "Wazuh", "repo": "wazuh/wazuh"}, NOW)] == ["breaking"]
