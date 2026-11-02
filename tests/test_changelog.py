"""Commit log ve iki tarih arası külliyat değişiklikleri testleri."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools import mcp_server
from tools.build_corpus import CorpusBuilder
from tools.changelog import collect_changes, collect_commit_log
from tools.git_engine import DeterministicGitEngine
from tools.law import app as cli_app

FIXTURES = Path(__file__).parent / "fixtures"
FASIL_BYTES = (FIXTURES / "fasil_113_sample.html").read_bytes()
YASA_BYTES = (FIXTURES / "yasa_2024_42_sample.html").read_bytes()


@pytest.fixture()
def change_repo(tmp_path: Path) -> Path:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_113.html").write_bytes(FASIL_BYTES)
    (sources / "yasa_2024_42.html").write_bytes(YASA_BYTES)
    repo = tmp_path / "repo"
    CorpusBuilder(repo).build_from_dir(sources)
    return repo


def test_commit_log_filters_law_and_dates(change_repo: Path) -> None:
    engine = DeterministicGitEngine(change_repo, initialize=False)

    entries = collect_commit_log(engine, law_id="fasil-113")
    assert entries
    assert all(entry.law_id == "fasil-113" for entry in entries)
    assert any(entry.effective_date == "2019-01-01" for entry in entries)

    filtered = collect_commit_log(
        engine,
        law_id="fasil-113",
        since="2018-01-01",
        until="2020-01-01",
    )
    assert [entry.effective_date for entry in filtered] == ["2019-01-01", "2018-09-05"]


def test_changes_include_metadata_and_articles(change_repo: Path) -> None:
    engine = DeterministicGitEngine(change_repo, initialize=False)
    changes = collect_changes(
        engine,
        since="2018-01-01",
        until="2020-01-01",
        law_id="fasil-113",
    )
    assert changes
    assert any(item.path.endswith("metadata.json") for item in changes)
    assert all(item.law_id == "fasil-113" for item in changes)


def test_log_and_changes_cli(change_repo: Path) -> None:
    runner = CliRunner()

    log_result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(change_repo),
            "log",
            "--law",
            "fasil-113",
            "--since",
            "2018-01-01",
            "--until",
            "2020-01-01",
        ],
    )
    assert log_result.exit_code == 0
    assert "2019-01-01" in log_result.output

    changes_result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(change_repo),
            "--json",
            "changes",
            "--since",
            "2018-01-01",
            "--until",
            "2020-01-01",
        ],
    )
    assert changes_result.exit_code == 0
    payload = json.loads(changes_result.output)
    assert payload
    assert any(item["path"].endswith("metadata.json") for item in payload)


def test_mcp_log_and_changes(change_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KKTC_LAW_REPO_PATH", str(change_repo))
    log = mcp_server.get_commit_log(law_id="fasil-113", limit=2)
    assert log
    assert all(item["law_id"] == "fasil-113" for item in log)

    changes = mcp_server.get_corpus_changes("2018-01-01", "2020-01-01")
    assert changes
    assert any(item["path"].endswith("metadata.json") for item in changes)
