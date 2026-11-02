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
