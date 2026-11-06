"""global-law uyumlu commit trailer bloğu (Law / In-force / Kind) testleri."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.git_engine import (
    COLONIAL_AUTHOR_NAME,
    DEFAULT_AUTHOR_NAME,
    DeterministicGitEngine,
)
from tools.models import ParsedLaw
from tools.parser import build_chronological_commits, parse_articles, parse_law_header

FASIL_FIXTURE = Path(__file__).parent / "fixtures" / "fasil_113_sample.html"
AMENDMENT_DATES = {"28/1974": "1974-12-15", "41/2018": "2018-09-05"}

LEGACY_PATH = "laws/fasil-113/madde-001.md"


def _legacy_commit(engine: DeterministicGitEngine, body: str) -> None:
    """Motoru atlayıp doğrudan git ile (eski biçimde) commit yazar."""
    (engine.repo_path / "laws/fasil-113").mkdir(parents=True, exist_ok=True)
    (engine.repo_path / LEGACY_PATH).write_text("# Madde 1\n\nA.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(engine.repo_path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(engine.repo_path), "commit", "-F", "-"],
        input=body.encode("utf-8"),
        env=engine._legislative_env("1951-06-01", COLONIAL_AUTHOR_NAME),
        check=True,
        capture_output=True,
    )


def test_baseline_and_law_commits_carry_trailers(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    engine.apply_legislative_commit(
        "1951-06-01",
        COLONIAL_AUTHOR_NAME,
        "fasil-113: ilk",
        {"laws/fasil-113/madde-001.md": "# Madde 1\n\nA.\n"},
        law="fasil-113 Şirketler",
        kind="baseline",
    )
    engine.apply_legislative_commit(
        "2018-09-05",
        DEFAULT_AUTHOR_NAME,
        "fasil-113: 41/2018",
        {"laws/fasil-113/madde-001.md": "# Madde 1\n\nB.\n"},
        law="41/2018",
        kind="law",
    )
    root = engine.commit_message(engine.root_commit()).rstrip("\n") + "\n"
    head = engine.commit_message("HEAD").rstrip("\n") + "\n"
    assert root.endswith("\n\nLaw: fasil-113 Şirketler\nIn-force: 1951-06-01\nKind: baseline\n")
    assert head.endswith("\n\nLaw: 41/2018\nIn-force: 2018-09-05\nKind: law\n")
    assert "Effective-Date" not in root + head


def test_legacy_effective_date_trailer_still_resolves(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _legacy_commit(engine, "x\n\nEffective-Date: 1951-06-01\n")
    assert engine.effective_date("HEAD") == "1951-06-01"
    assert engine.read_file_as_of(LEGACY_PATH, "1955-01-01") is not None
    assert engine.read_file_as_of(LEGACY_PATH, "1949-01-01") is None


def test_in_force_wins_over_legacy_when_both_present(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _legacy_commit(engine, "x\n\nEffective-Date: 1950-01-01\nIn-force: 1951-06-01\n")
    assert engine.effective_date("HEAD") == "1951-06-01"


@pytest.mark.parametrize(
    ("law", "kind"),
    [("", "law"), ("   ", "law"), ("a\nb", "law"), ("41/2018", "other")],
)
def test_invalid_law_or_kind_is_rejected(tmp_path: Path, law: str, kind: str) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    with pytest.raises(ValueError):
        engine.apply_legislative_commit(
            "2018-09-05",
            DEFAULT_AUTHOR_NAME,
            "x",
            {LEGACY_PATH: "# Madde 1\n\nA.\n"},
            law=law,
            kind=kind,  # type: ignore[arg-type]
        )


def test_parser_assigns_kind_and_law() -> None:
    raw = FASIL_FIXTURE.read_bytes()
    parsed = ParsedLaw(header=parse_law_header(raw), articles=parse_articles(raw))
    header = parsed.header
    plans = build_chronological_commits(parsed, AMENDMENT_DATES)
    assert plans[0].kind == "baseline"
    assert plans[0].law == f"{header.law_id} {header.title}".strip()
    assert len(plans) > 1
    assert all(p.kind == "law" and p.law == p.amending_law for p in plans[1:])
