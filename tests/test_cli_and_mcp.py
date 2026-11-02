"""Faz 4 salt-okunur sorgulama motoru, CLI ve FastMCP testleri."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools import mcp_server
from tools.build_corpus import CorpusBuilder
from tools.git_engine import DeterministicGitEngine, is_repealed_text
from tools.law import app as cli_app

FIXTURES = Path(__file__).parent / "fixtures"
FASIL_FIXTURE = FIXTURES / "fasil_113_sample.html"
YASA_FIXTURE = FIXTURES / "yasa_2024_42_sample.html"


@pytest.fixture(scope="module")
def corpus_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    sources = tmp_path_factory.mktemp("sources")
    (sources / "fasil_113_sample.html").write_bytes(FASIL_FIXTURE.read_bytes())
    (sources / "yasa_2024_42_sample.html").write_bytes(YASA_FIXTURE.read_bytes())
    repo = tmp_path_factory.mktemp("corpus")
    CorpusBuilder(repo).build_from_dir(sources)
    return repo


def _engine(repo: Path) -> DeterministicGitEngine:
    return DeterministicGitEngine(repo, initialize=False)


# ------------------------------------------------------------------ motor sorguları


def test_list_laws_as_of(corpus_repo: Path) -> None:
    engine = _engine(corpus_repo)

    at_1960 = engine.list_laws_as_of("1960-01-01")
    assert [law.law_id for law in at_1960] == ["fasil-113"]
    assert at_1960[0].title == "Şirketler Yasası"
    assert at_1960[0].original_date == "1951-06-01"

    at_2025 = engine.list_laws_as_of("2025-01-01")
    assert [law.law_id for law in at_2025] == ["fasil-113", "yasa-2024-42"]

    assert engine.list_laws_as_of("1950-01-01") == []
    assert len(engine.list_laws_as_of()) == 2


def test_read_article_shortcuts_and_repeal_detection(corpus_repo: Path) -> None:
    engine = _engine(corpus_repo)

    article_two = engine.read_article_as_of("fasil-113", "2", "2020-01-01")
    assert article_two is not None
    assert "Şirketlerin Tescili" in article_two
    assert "Tescil başvurusu" in article_two

    temporary = engine.read_article_as_of("yasa-2024-42", "gecici-1", "2025-01-01")
    assert temporary is not None
    assert "Geçiş Hükümleri" in temporary

    repealed = engine.read_article_as_of("fasil-113", "5", "2020-01-01")
    assert repealed is not None
    assert is_repealed_text(repealed)
    assert not is_repealed_text(article_two)


def test_article_history(corpus_repo: Path) -> None:
    engine = _engine(corpus_repo)

    history_four = engine.get_article_history("fasil-113", "madde-004")
    assert [entry["status"] for entry in history_four] == [
        "eklendi",
        "güncellendi",
        "güncellendi",
    ]
    assert [entry["effective_date"] for entry in history_four] == [
        "1951-06-01",
        "2018-09-05",
        "2019-01-01",
    ]
    assert history_four[0]["author_name"] == "Kıbrıs Koloni İdaresi"
    assert "41/2018" in history_four[1]["subject"]
    assert "34/2019" in history_four[2]["subject"]
    assert all(len(entry["commit_sha"]) == 40 for entry in history_four)

    history_five = engine.get_article_history("fasil-113", "5")
    assert [entry["effective_date"] for entry in history_five] == [
        "1951-06-01",
        "1974-12-15",
    ]
    assert [entry["status"] for entry in history_five] == ["eklendi", "güncellendi"]

    assert engine.get_article_history("fasil-113", "madde-999") == []


def test_diff_article(corpus_repo: Path) -> None:
    engine = _engine(corpus_repo)

    # 1951 öncesi boş ağaç kabul edilir: dosyanın tamamı eklenmiş görünür.
    repeal_diff = engine.diff_article(
        "fasil-113", "madde-005", "1951-01-01", "2020-01-01"
    )
    assert repeal_diff.startswith("diff --git")
    assert "kaldırılmıştır" in repeal_diff

    # Hibrit replay'de kök commit konsolide metni taşıdığı için metin farkı yoktur.
    assert (
        engine.diff_article("fasil-113", "madde-005", "1951-06-01", "2020-01-01")
        == ""
    )

    later_diff = engine.diff_article(
        "yasa-2024-42", "1", "2020-01-01", "2025-01-01"
    )
    assert later_diff.startswith("diff --git")
    assert "laws/yasa-2024-42/madde-001.md" in later_diff


def test_search_legislation(corpus_repo: Path) -> None:
    engine = _engine(corpus_repo)

    hits = engine.search_legislation("kaldırılmıştır", "2020-01-01")
    assert len(hits) == 1
    hit = hits[0]
    assert hit.law_id == "fasil-113"
    assert hit.article_id == "madde-005"
    assert hit.line_number >= 1
    assert "kaldırılmıştır" in hit.snippet

    company_hits = engine.search_legislation(
        "Şirketler", "2020-01-01", law_id="fasil-113"
    )
    assert company_hits
    assert all(item.law_id == "fasil-113" for item in company_hits)

    assert engine.search_legislation("veri", "2020-01-01") == []
    future_hits = engine.search_legislation("veri", "2025-01-01")
    assert any(item.law_id == "yasa-2024-42" for item in future_hits)


# --------------------------------------------------------------------------- CLI


def test_cli_laws_and_json(corpus_repo: Path) -> None:
    runner = CliRunner()

    text_result = runner.invoke(
        cli_app,
        ["--repo-path", str(corpus_repo), "laws", "--as-of", "1960-01-01"],
    )
    assert text_result.exit_code == 0
    assert "fasil-113" in text_result.output
    assert "yasa-2024-42" not in text_result.output

    json_result = runner.invoke(
        cli_app,
        ["--repo-path", str(corpus_repo), "--json", "laws"],
    )
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert [item["law_id"] for item in payload] == ["fasil-113", "yasa-2024-42"]


def test_cli_read_history_diff_search(corpus_repo: Path) -> None:
    runner = CliRunner()

    read_result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(corpus_repo),
            "read",
            "fasil-113",
            "2",
            "--as-of",
            "2020-01-01",
        ],
    )
    assert read_result.exit_code == 0
    assert "Şirketlerin Tescili" in read_result.output

    temporary_result = runner.invoke(
        cli_app,
        ["--repo-path", str(corpus_repo), "read", "yasa-2024-42", "gecici-1"],
    )
    assert temporary_result.exit_code == 0
    assert "Geçiş Hükümleri" in temporary_result.output

    history_result = runner.invoke(
        cli_app,
        ["--repo-path", str(corpus_repo), "history", "fasil-113", "4"],
    )
    assert history_result.exit_code == 0
    assert "güncellendi" in history_result.output
    assert "41/2018" in history_result.output
    assert "34/2019" in history_result.output

    diff_result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(corpus_repo),
            "diff",
            "yasa-2024-42",
            "1",
            "--from",
            "2020-01-01",
            "--to",
            "2025-01-01",
        ],
    )
    assert diff_result.exit_code == 0
    assert "diff --git" in diff_result.output
    assert "laws/yasa-2024-42/madde-001.md" in diff_result.output

    search_result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(corpus_repo),
            "search",
            "kaldırılmıştır",
            "--as-of",
            "2020-01-01",
        ],
    )
    assert search_result.exit_code == 0
    assert "fasil-113/madde-005" in search_result.output
    assert "kaldırılmıştır" in search_result.output


def test_cli_json_read_and_search(corpus_repo: Path) -> None:
    runner = CliRunner()

    read_result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(corpus_repo),
            "--json",
            "read",
            "yasa-2024-42",
            "gecici-1",
        ],
    )
    assert read_result.exit_code == 0
    article = json.loads(read_result.output)
    assert article["article_id"] == "gecici-madde-001"
    assert article["law_title"] == "Kişisel Verilerin Korunması Yasası"
    assert article["repealed"] is False
    assert "Geçiş Hükümleri" in article["content"]

    search_result = runner.invoke(
        cli_app,
        [
            "--repo-path",
            str(corpus_repo),
            "--json",
            "search",
            "kaldırılmıştır",
            "--as-of",
            "2020-01-01",
        ],
    )
    assert search_result.exit_code == 0
    hits = json.loads(search_result.output)
    assert hits[0]["article_id"] == "madde-005"
    assert hits[0]["line_number"] >= 1


# --------------------------------------------------------------------------- MCP


def test_mcp_tools_and_clean_working_tree(
    corpus_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KKTC_LAW_REPO_PATH", str(corpus_repo))
    engine = _engine(corpus_repo)
    head_before = engine.head()

    laws = mcp_server.list_laws("1960-01-01")
    assert [item["law_id"] for item in laws] == ["fasil-113"]
    assert laws[0]["title"] == "Şirketler Yasası"

    article = mcp_server.get_law_article("fasil-113", "2", "2020-01-01")
    assert article["article_id"] == "madde-002"
    assert article["law_title"] == "Şirketler Yasası"
    assert article["effective_date"] == "2019-01-01"
    assert len(article["commit_sha"]) == 40
    assert "Tescil başvurusu" in article["content"]
    assert article["repealed"] is False

    repealed = mcp_server.get_law_article("fasil-113", "5", "2020-01-01")
    assert repealed["repealed"] is True

    history = mcp_server.get_article_amendment_history("fasil-113", "4")
    assert [entry["status"] for entry in history] == [
        "eklendi",
        "güncellendi",
        "güncellendi",
    ]

    comparison = mcp_server.compare_article_versions(
        "yasa-2024-42", "1", "2020-01-01", "2025-01-01"
    )
    assert comparison["changed"] is True
    assert comparison["from_commit"] != comparison["to_commit"]
    assert "laws/yasa-2024-42/madde-001.md" in comparison["diff"]

    hits = mcp_server.search_legislation("kaldırılmıştır", "2020-01-01")
    assert [hit["article_id"] for hit in hits] == ["madde-005"]

    assert engine.head() == head_before
    assert engine.is_clean()
    status = subprocess.run(
        ["git", "-C", str(corpus_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""
