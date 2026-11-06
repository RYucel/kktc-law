"""global-law uyumlu commit trailer bloğu (Law / In-force / Kind) testleri."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools.build_corpus import CorpusBuilder, render_corpus_json
from tools.build_corpus import app as build_app
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


def _commit_dates(engine: DeterministicGitEngine, dates: list[str]) -> list[str]:
    """Her tarih için, içeriği farklı bir madde dosyası commit'i yazar."""
    path = "laws/fasil-113/madde-001.md"
    for index, day in enumerate(dates):
        engine.apply_legislative_commit(
            day,
            COLONIAL_AUTHOR_NAME,
            f"fasil-113: {day}",
            {path: f"# Madde 1\n\nMetin {day} #{index}.\n"},
            law="fasil-113" if index == 0 else f"yasa-{index}",
            kind="baseline" if index == 0 else "law",
        )
    return dates


def test_pre_epoch_commits_are_one_second_apart_then_midnight(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["1899-06-02", "1951-06-01", "1960-08-16", "1970-01-01", "1985-05-05"])
    stamps = [engine.commit_timestamp(c) for c in engine.list_commits(reverse=True)]
    assert stamps == [
        "1970-01-01T00:00:00+00:00",
        "1970-01-01T00:00:01+00:00",
        "1970-01-01T00:00:02+00:00",
        "1970-01-01T00:00:02+00:00",
        "1985-05-05T00:00:00+00:00",
    ]
    committers = [
        subprocess.run(
            ["git", "-C", str(engine.repo_path), "show", "-s", "--format=%cI", c],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().replace("Z", "+00:00")
        for c in engine.list_commits(reverse=True)
    ]
    assert committers == stamps


def test_as_of_before_1970_excludes_real_1970_commit(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["1951-06-01", "1960-08-16", "1970-01-01"])
    assert "1960-08-16" in (engine.read_file_as_of(LEGACY_PATH, "1969-12-31") or "")
    assert "1970-01-01" in (engine.read_file_as_of(LEGACY_PATH, "1970-01-01") or "")
    assert "1951-06-01" in (engine.read_file_as_of(LEGACY_PATH, "1951-06-01") or "")


def test_same_day_post_epoch_commits_resolve_to_last(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["2026-01-01", "2026-01-01"])
    assert engine.commit_timestamp("HEAD") == engine.commit_timestamp("HEAD~1")
    assert engine.commit_timestamp("HEAD") == "2026-01-01T00:00:00+00:00"
    assert engine.resolve_commit_as_of("2026-01-01") == engine.head()


def test_out_of_order_post_epoch_date_never_goes_backwards(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["2018-09-05", "2010-01-01"])
    assert engine.commit_timestamp("HEAD") == "2018-09-05T00:00:00+00:00"


def test_tag_shares_commit_stamp(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["1951-06-01"])
    engine.apply_legislative_commit(
        "1960-08-16",
        COLONIAL_AUTHOR_NAME,
        "fasil-113: etiketli",
        {LEGACY_PATH: "# Madde 1\n\nEtiketli.\n"},
        "v1960-08-16",
        law="yasa-1",
        kind="law",
    )
    stamp = engine.commit_timestamp("HEAD")
    assert stamp == "1970-01-01T00:00:01+00:00"
    tagger = subprocess.run(
        [
            "git",
            "-C",
            str(engine.repo_path),
            "for-each-ref",
            "--format=%(taggerdate:iso-strict)",
            "refs/tags/v1960-08-16",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tagger.replace("Z", "+00:00") == stamp


def _ref_exists(engine: DeterministicGitEngine, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(engine.repo_path), "rev-parse", "--verify", "-q", ref],
            capture_output=True,
        ).returncode
        == 0
    )


def test_finalize_moves_future_commits_off_main(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["2020-01-01", "2026-11-02", "2026-12-01", "2026-12-15"])
    old_head = engine.head()
    future_sha = engine.finalize_branches("2026-11-02")
    assert future_sha == old_head
    assert engine.rev_parse("refs/heads/future") == old_head
    assert engine.effective_date(engine.rev_parse("refs/heads/main")) == "2026-11-02"
    assert engine.head() == engine.rev_parse("refs/heads/main")
    assert engine.is_clean()
    expected = "# Madde 1\n\nMetin 2026-11-02 #1.\n"
    assert (engine.repo_path / LEGACY_PATH).read_text(encoding="utf-8") == expected
    assert engine.read_file_as_of(LEGACY_PATH, "2026-12-31") == expected


def test_finalize_without_future_commits_creates_no_branch(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["2020-01-01", "2026-11-02"])
    assert engine.finalize_branches("2026-11-02") is None
    assert not _ref_exists(engine, "refs/heads/future")


def test_finalize_rejects_cutoff_before_all_commits(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "r")
    _commit_dates(engine, ["2020-01-01", "2026-11-02"])
    old_head = engine.head()
    with pytest.raises(ValueError):
        engine.finalize_branches("1800-01-01")
    assert engine.head() == old_head
    assert not _ref_exists(engine, "refs/heads/future")


# ------------------------------------------------------------------ kesim tarihi / inşa

SRC_FASIL = Path(__file__).parent / "fixtures" / "fasil_113_sample.html"
SRC_YASA = Path(__file__).parent / "fixtures" / "yasa_2024_42_sample.html"
BUILD_AMENDMENTS = {"28/1974": "1974-12-15", "41/2018": "2026-12-01"}


def _build_sources(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "fasil_113_sample.html").write_bytes(SRC_FASIL.read_bytes())
    (directory / "yasa_2024_42_sample.html").write_bytes(SRC_YASA.read_bytes())
    return directory


def test_render_corpus_json_sets_cutoff_and_future_flag() -> None:
    out = render_corpus_json(
        '{"history": "current-only", "checked_on": "2026-11-02"}\n',
        cutoff="2026-11-03",
        has_future_branch=True,
    )
    assert out == (
        '{\n  "checked_on": "2026-11-03",\n  "has_future_branch": true,\n'
        '  "history": "current-only"\n}\n'
    )


def test_build_with_cutoff_splits_future_and_filters_stats(tmp_path: Path) -> None:
    src = _build_sources(tmp_path / "src")
    repo = tmp_path / "repo"
    summary = CorpusBuilder(
        repo, amendment_dates=BUILD_AMENDMENTS, cutoff="2026-11-02"
    ).build_from_dir(src)
    engine = DeterministicGitEngine(repo, initialize=False)
    assert summary.cutoff == "2026-11-02"
    assert summary.future_sha is not None
    assert summary.head_sha == engine.rev_parse("refs/heads/main")
    assert summary.future_sha == engine.rev_parse("refs/heads/future")
    root = engine.root_commit()
    corpus = json.loads(engine.read_file_at_commit("corpus.json", root) or "")
    assert corpus["checked_on"] == "2026-11-02"
    assert corpus["has_future_branch"] is True
    stats_md = engine.read_file_at_commit("STATS.md", root) or ""
    future_count = len(engine.list_commits(summary.future_sha)) - len(
        engine.list_commits(summary.head_sha)
    )
    assert future_count >= 1
    assert f"| Tarihsel commit | {summary.commit_count - future_count} |" in stats_md
    assert "| Son yürürlük tarihi | 2026-12-01 |" not in stats_md
    assert "| Son yürürlük tarihi | 2024-03-15 |" in stats_md


def test_build_twice_with_same_cutoff_is_identical(tmp_path: Path) -> None:
    src = _build_sources(tmp_path / "src")
    first = CorpusBuilder(
        tmp_path / "a", amendment_dates=BUILD_AMENDMENTS, cutoff="2026-11-02"
    ).build_from_dir(src)
    second = CorpusBuilder(
        tmp_path / "b", amendment_dates=BUILD_AMENDMENTS, cutoff="2026-11-02"
    ).build_from_dir(src)
    assert (first.head_sha, first.future_sha) == (second.head_sha, second.future_sha)


def test_build_defaults_cutoff_to_corpus_checked_on(tmp_path: Path) -> None:
    src = _build_sources(tmp_path / "src")
    summary = CorpusBuilder(tmp_path / "r", amendment_dates=BUILD_AMENDMENTS).build_from_dir(src)
    assert summary.cutoff == "2026-11-02"
    assert summary.future_sha is not None


def test_build_without_future_flags_false(tmp_path: Path) -> None:
    src = _build_sources(tmp_path / "src")
    repo = tmp_path / "r"
    summary = CorpusBuilder(
        repo, amendment_dates=BUILD_AMENDMENTS, cutoff="2027-01-01"
    ).build_from_dir(src)
    engine = DeterministicGitEngine(repo, initialize=False)
    assert summary.future_sha is None
    corpus = json.loads(engine.read_file_at_commit("corpus.json", engine.root_commit()) or "")
    assert corpus["has_future_branch"] is False


def test_build_rejects_cutoff_before_all_laws(tmp_path: Path) -> None:
    src = _build_sources(tmp_path / "src")
    repo = tmp_path / "repo"
    with pytest.raises(ValueError):
        CorpusBuilder(repo, cutoff="1800-01-01").build_from_dir(src)
    assert not DeterministicGitEngine(repo).has_commits()


def test_builder_rejects_missing_or_invalid_cutoff(tmp_path: Path) -> None:
    bad = tmp_path / "corpus.json"
    bad.write_text('{"history": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        CorpusBuilder(tmp_path / "r", corpus_path=bad)
    with pytest.raises(ValueError):
        CorpusBuilder(tmp_path / "r", cutoff="2026/11/02")


def test_build_cli_rejects_malformed_cutoff(tmp_path: Path) -> None:
    src = _build_sources(tmp_path / "src")
    result = CliRunner().invoke(
        build_app,
        ["--repo-path", str(tmp_path / "r"), "--from-dir", str(src), "--cutoff", "2026/11/02"],
    )
    assert result.exit_code != 0
