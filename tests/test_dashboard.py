"""Statik dashboard üretim testleri."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tools.build_corpus import CorpusBuilder
from tools.dashboard import build_dashboard, render_dashboard
from tools.git_engine import DeterministicGitEngine
from tools.law import app as cli_app

FIXTURES = Path(__file__).parent / "fixtures"


def _repo(tmp_path: Path) -> Path:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_113.html").write_bytes(
        (FIXTURES / "fasil_113_sample.html").read_bytes()
    )
    (sources / "yasa.html").write_bytes(
        (FIXTURES / "yasa_2024_42_sample.html").read_bytes()
    )
    repo = tmp_path / "repo"
    CorpusBuilder(repo).build_from_dir(sources)
    return repo


def test_dashboard_contains_cards_charts_and_laws(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    engine = DeterministicGitEngine(repo, initialize=False)

    first = render_dashboard(engine)
    second = render_dashboard(engine)
    assert first == second
    assert "KKTC Mevzuat Külliyatı" in first
    assert "Yasa türleri" in first
    assert "Yürürlük zaman çizelgesi" in first
    assert "fasil-113" in first
    assert "conic-gradient" in first

    target = build_dashboard(engine, tmp_path / "dashboard")
    assert target.is_file()
    assert "Yasalar" in target.read_text(encoding="utf-8")
    law_page = tmp_path / "dashboard" / "laws" / "fasil-113.html"
    assert law_page.is_file()
    law_html = law_page.read_text(encoding="utf-8")
    assert "fasil-113" in law_html
    assert "commit" in law_html
    assert "laws/fasil-113.html" in target.read_text(encoding="utf-8")


def test_dashboard_cli(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(repo),
            "dashboard",
            "--output",
            str(tmp_path / "site"),
        ],
    )
    assert result.exit_code == 0
    assert (tmp_path / "site" / "index.html").is_file()


def test_dashboard_renders_git_diff_view(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "diff-repo")
    metadata = (
        '{"law_id":"fasil-1","type":"fasil","number":"1","title":"Test",'
        '"original_date":"1950-01-01","date_source":"explicit",'
        '"consolidation_history":[]}\n'
    )
    engine.apply_legislative_commit(
        effective_date="1950-01-01",
        author_name="Test",
        commit_message="fasil-1: 1950-01-01 — ilk yürürlük (Test)",
        file_changes={
            "laws/fasil-1/metadata.json": metadata,
            "laws/fasil-1/madde-001.md": "# Madde 1: Test\n\nEski metin.\n",
        },
        law="fasil-1 Test",
        kind="baseline",
    )
    engine.apply_legislative_commit(
        effective_date="1960-01-01",
        author_name="Test",
        commit_message="fasil-1: 1960-01-01 — 1/1960 (madde-001)",
        file_changes={
            "laws/fasil-1/madde-001.md": "# Madde 1: Test\n\nYeni metin.\n",
        },
        law="1/1960",
        kind="law",
    )

    html = render_dashboard(engine)
    assert "Yasa metni diff görünümü" in html
    assert "diff-add" in html
    assert "diff-del" in html
    assert "Yeni metin." in html
