"""Faz 7: çapraz tarih eşleştirme, stats CLI/MCP testleri."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools import mcp_server
from tools.build_corpus import CorpusBuilder
from tools.git_engine import DeterministicGitEngine
from tools.law import app as cli_app

FIXTURES = Path(__file__).parent / "fixtures"
FASIL_BYTES = (FIXTURES / "fasil_113_sample.html").read_bytes()
YASA_BYTES = (FIXTURES / "yasa_2024_42_sample.html").read_bytes()


def _build_two_law_repo(tmp_path: Path) -> Path:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_113.html").write_bytes(FASIL_BYTES)
    (sources / "yasa_2024_42.html").write_bytes(YASA_BYTES)
    repo = tmp_path / "repo"
    CorpusBuilder(repo).build_from_dir(sources)
    return repo


def test_cross_linking_uses_explicit_law_date(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "yasa_2024_42.html").write_bytes(YASA_BYTES)
    (sources / "fasil_999.html").write_text(
        """<!DOCTYPE html><html><body>
        <p>FASIL 999</p>
        <p>ÇAPRAZ YASA</p>
        <p>1 Ocak 1950 tarihinde yürürlüğe girmiştir.</p>
        <p>1. Bu Yasa, çapraz test için kullanılır.</p>
        <p>42/2024 sayılı Yasa ile değiştirilmiştir.</p>
        </body></html>""",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    CorpusBuilder(repo).build_from_dir(sources)

    engine = DeterministicGitEngine(repo, initialize=False)
    dates = [engine.effective_date(sha) for sha in engine.list_commits(reverse=True)]
    subjects = [
        engine.commit_message(sha) for sha in engine.list_commits(reverse=True)
    ]

    assert "2024-03-15" in dates
    assert any("42/2024" in subject for subject in subjects)
    assert not any(
        "42/2024" in subject and "2024-01-01" in subject for subject in subjects
    )


def test_stats_note_is_written_to_corpus_root(tmp_path: Path) -> None:
    repo = _build_two_law_repo(tmp_path)
    stats_path = repo / "STATS.md"
    assert stats_path.is_file()
    text = stats_path.read_text(encoding="utf-8")
    assert "Toplam yasa" in text
    assert "Tarih Kaynakları" in text


def test_stats_cli_and_mcp(tmp_path: Path) -> None:
    repo = _build_two_law_repo(tmp_path)
    runner = CliRunner()

    text_result = runner.invoke(
        cli_app, ["--repo-path", str(repo), "stats"]
    )
    assert text_result.exit_code == 0
    assert "Toplam yasa" in text_result.output

    json_result = runner.invoke(
        cli_app, ["--repo-path", str(repo), "--json", "stats"]
    )
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["total_laws"] == 2
    assert payload["total_articles"] > 0
    assert set(payload["date_source_counts"]) == {
        "explicit",
        "override",
        "fallback_1959",
        "fallback_law_year",
    }

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("KKTC_LAW_REPO_PATH", str(repo))
        mcp_stats = mcp_server.get_corpus_statistics()
        assert mcp_stats["total_laws"] == 2
        assert mcp_stats["total_articles"] > 0
    finally:
        monkeypatch.undo()
