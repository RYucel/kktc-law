"""TurkLex Sentinel hibrit arama ve Ultra Vires denetim testleri."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import mcp_server
from tools.build_corpus import CorpusBuilder
from tools.git_engine import DeterministicGitEngine
from tools.sentinel import HybridRetriever, audit_contract_clause, semantic_search
from tools.ultra_vires import audit_norm

FIXTURES = Path(__file__).parent / "fixtures"
FASIL_BYTES = (FIXTURES / "fasil_113_sample.html").read_bytes()
YASA_BYTES = (FIXTURES / "yasa_2024_42_sample.html").read_bytes()
ANAYASA_HTML = """<!DOCTYPE html>
<html><body>
<p>Kuzey Kıbrıs Türk Cumhuriyeti Anayasası</p>
<p>5 Mayıs, 1985 tarihinde Halkoylamasına sunulup kabul edilmiştir.</p>
<p>BAŞLANGIÇ</p>
<p>Kıbrıs Türk Halkı özgürlük için mücadele etmiştir.</p>
<p>Madde 1. Devletin şekli Cumhuriyettir.</p>
<p>Madde 2. Egemenlik kayıtsız şartsız halkındır.</p>
</body></html>
""".encode()


@pytest.fixture()
def sentinel_repo(tmp_path: Path) -> Path:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_113.html").write_bytes(FASIL_BYTES)
    (sources / "yasa_2024_42.html").write_bytes(YASA_BYTES)
    (sources / "anayasa.html").write_bytes(ANAYASA_HTML)
    repo = tmp_path / "repo"
    CorpusBuilder(repo).build_from_dir(sources)
    return repo


def test_semantic_search_returns_commit_stamped_hits(sentinel_repo: Path) -> None:
    engine = DeterministicGitEngine(sentinel_repo, initialize=False)
    hits = semantic_search(engine, "Şirketlerin Tescili", "2025-01-01", limit=5)
    assert hits
    assert any(hit.law_id == "fasil-113" for hit in hits)
    assert all(len(hit.commit_sha) == 40 for hit in hits)


def test_hybrid_retriever_includes_exact_and_semantic_sources(
    sentinel_repo: Path,
) -> None:
    engine = DeterministicGitEngine(sentinel_repo, initialize=False)
    retriever = HybridRetriever(engine)
    sources = retriever.search(
        "Fasıl 113 şirketlerin tescili",
        "2025-01-01",
        limit=3,
    )
    assert sources
    assert any(source["retrieval"] == "exact" for source in sources)
    assert all(source["commit_sha"] for source in sources)


def test_ultra_vires_detects_lower_norm_overriding_constitution(
    sentinel_repo: Path,
) -> None:
    engine = DeterministicGitEngine(sentinel_repo, initialize=False)
    report = audit_norm(
        "Anayasa'nın 1. maddesini değiştirir.",
        "2020-01-01",
        subject_level="secondary",
        engine=engine,
    )
    codes = {finding.code for finding in report.findings}
    assert "ULTRA_VIRES" in codes
    assert "CONFLICT_WITH_CONSTITUTION" in codes
    assert report.sources[0].law_id == "anayasa"


def test_sentinel_audit_is_prompt_only_and_traceable(sentinel_repo: Path) -> None:
    engine = DeterministicGitEngine(sentinel_repo, initialize=False)
    result = audit_contract_clause(
        "Anayasa'nın 1. maddesini değiştirir.",
        "2020-01-01",
        engine,
    )
    assert result["deterministic"] is True
    assert result["snapshot_commit"]
    assert result["audit_prompt"].startswith("TurkLex Sentinel")
    assert "Git snapshot commit" in result["audit_prompt"]
    assert result["sources"]


def test_mcp_sentinel_tools(sentinel_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KKTC_LAW_REPO_PATH", str(sentinel_repo))
    hits = mcp_server.semantic_search("şirket tescili", "2025-01-01", limit=3)
    assert hits
    report = mcp_server.audit_norm(
        "Anayasa'nın 1. maddesini değiştirir.",
        "2020-01-01",
    )
    assert any(item["code"] == "ULTRA_VIRES" for item in report["findings"])
    audit = mcp_server.turklex_audit("Anayasa'nın 1. maddesini değiştirir.", "2020-01-01")
    assert audit["deterministic"] is True
    assert audit["snapshot_commit"]
