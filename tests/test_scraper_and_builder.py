"""Faz 3 scraper ve uçtan uca küresel külliyat inşası testleri."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import httpx

from tools.build_corpus import CorpusBuilder, merge_commit_plans
from tools.git_engine import DeterministicGitEngine
from tools.models import LegislativeCommitPlan
from tools.scraper import (
    MahkemelerScraper,
    load_cache_manifest,
    parse_mahkemeler_index,
)

FIXTURES = Path(__file__).parent / "fixtures"
FASIL_BYTES = (FIXTURES / "fasil_113_sample.html").read_bytes()
YASA_BYTES = (FIXTURES / "yasa_2024_42_sample.html").read_bytes()

BASE_URL = "https://www.mahkemeler.net/mevzuat/"
FASIL_PATH = "/mevzuat/fasil-113.html"
YASA_PATH = "/mevzuat/yasa-2024-42.html"

EXPECTED_DATES = [
    "1951-06-01",
    "1974-12-15",
    "2018-09-05",
    "2019-01-01",
    "2024-03-15",
]

INDEX_HTML = """<!DOCTYPE html>
<html lang="tr">
  <head><meta charset="utf-8"><title>Mevzuat Fihristi</title></head>
  <body>
    <h1>KKTC Mevzuat Fihristi</h1>
    <a href="fasil-113.html">Fasıl 113</a>
    <a href="fasil-113.html">Fasıl 113 - Şirketler Yasası</a>
    <a href="yasa-2024-42.html">42/2024 Sayılı Kişisel Verilerin Korunması Yasası</a>
    <a href="/iletisim.html">İletişim</a>
  </body>
</html>
"""


def _mock_transport(counters: Counter[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        counters[path] += 1
        if path == "/mevzuat/":
            return httpx.Response(
                200,
                content=INDEX_HTML.encode("utf-8"),
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )
        if path == FASIL_PATH:
            return httpx.Response(
                200,
                content=FASIL_BYTES,
                headers={"content-type": "text/html; charset=windows-1254"},
                request=request,
            )
        if path == YASA_PATH:
            return httpx.Response(
                200,
                content=YASA_BYTES,
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


def _write_sources(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "fasil_113_sample.html").write_bytes(FASIL_BYTES)
    (directory / "yasa_2024_42_sample.html").write_bytes(YASA_BYTES)


def _without_anayasa(entries: list) -> list:
    return [entry for entry in entries if entry.law_id != "anayasa"]


def _plan(law_id: str, effective_date: str) -> LegislativeCommitPlan:
    return LegislativeCommitPlan(
        effective_date=effective_date,
        author_name="Test Meclisi",
        commit_message=f"{law_id} kök commit",
        file_changes={
            f"laws/{law_id}/madde-001.md": "# 1. Madde\n\nMetin.\n",
            f"laws/{law_id}/metadata.json": "{}\n",
        },
        tag_name=f"v{effective_date}",
    )


# ------------------------------------------------------------------- indeks ayrıştırıcı


def test_parse_mahkemeler_index_deduplicates_and_sorts() -> None:
    entries = parse_mahkemeler_index(INDEX_HTML, BASE_URL)

    assert [entry.law_id for entry in entries] == [
        "anayasa",
        "fasil-113",
        "yasa-2024-42",
    ]


    _, fasil, yasa = entries
    assert fasil.category == "fasil"
    assert fasil.number == "113"
    assert fasil.title == "Fasıl 113 - Şirketler Yasası"
    assert fasil.url == f"{BASE_URL}fasil-113.html"

    assert yasa.category == "yasa"
    assert yasa.number == "42/2024"
    assert yasa.title == "42/2024 Sayılı Kişisel Verilerin Korunması Yasası"
    assert yasa.url == f"{BASE_URL}yasa-2024-42.html"


def test_parse_mahkemeler_index_anayasa_and_url_fallback() -> None:
    html = """
    <html><body>
      <a href="/mevzuat/anayasa.html">KKTC Anayasası</a>
      <a href="/mevzuat/fasillar/154.html">154</a>
      <a href="/mevzuat/yasa/2020/7.html">Yasa</a>
      <a href="https://baskasite.example/fasil-1.html">Fasıl 1</a>
      <a href="#icerik">İçerik</a>
    </body></html>
    """

    entries = parse_mahkemeler_index(html.encode("utf-8"), BASE_URL)

    assert [entry.law_id for entry in entries] == ["anayasa", "fasil-154", "yasa-2020-7"]
    assert entries[0].category == "anayasa"
    assert entries[0].number is None
    assert entries[1].number == "154"
    assert entries[2].number == "7/2020"


# ------------------------------------------------------------------------- scraper


async def test_scraper_sha256_cache_is_idempotent(tmp_path: Path) -> None:
    counters: Counter[str] = Counter()
    client = httpx.AsyncClient(transport=_mock_transport(counters))
    scraper = MahkemelerScraper(tmp_path / "raw_cache", index_url=BASE_URL, client=client)
    cache_dir = tmp_path / "raw_cache"

    try:
        entries = await scraper.fetch_index()
        assert counters["/mevzuat/"] == 1
        assert counters[FASIL_PATH] == 0
        assert counters[YASA_PATH] == 0

        manifest = await scraper.fetch_all(_without_anayasa(entries))
        assert counters[FASIL_PATH] == 1
        assert counters[YASA_PATH] == 1

        assert sorted(manifest.entries) == ["fasil-113", "yasa-2024-42"]
        fasil_doc = manifest.entries["fasil-113"]
        raw_fasil = (cache_dir / "fasil-113.html").read_bytes()
        assert raw_fasil == FASIL_BYTES
        assert fasil_doc.sha256 == hashlib.sha256(raw_fasil).hexdigest()
        assert fasil_doc.raw_path == "fasil-113.html"
        assert fasil_doc.content_length == len(FASIL_BYTES)
        assert fasil_doc.encoding is not None
        assert "1254" in fasil_doc.encoding

        manifest_path = cache_dir / "manifest.json"
        assert manifest_path.is_file()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert sorted(payload["entries"]) == ["fasil-113", "yasa-2024-42"]
        assert manifest_path.read_text(encoding="utf-8") == (
            json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        )

        reloaded = load_cache_manifest(cache_dir)
        assert reloaded.get("fasil-113") == fasil_doc

        first_run_counts = Counter(counters)
        manifest_again = await scraper.fetch_all(_without_anayasa(entries))
        assert counters == first_run_counts
        assert manifest_again.entries["yasa-2024-42"].sha256 == manifest.entries[
            "yasa-2024-42"
        ].sha256

        await scraper.fetch_all(_without_anayasa(entries), force_refresh=True)
        assert counters[FASIL_PATH] == 2
        assert counters[YASA_PATH] == 2
    finally:
        await scraper.aclose()
        await client.aclose()


async def test_scraper_redownloads_when_cached_file_is_tampered(tmp_path: Path) -> None:
    counters: Counter[str] = Counter()
    client = httpx.AsyncClient(transport=_mock_transport(counters))
    scraper = MahkemelerScraper(tmp_path / "raw_cache", index_url=BASE_URL, client=client)
    cache_dir = tmp_path / "raw_cache"

    try:
        entries = await scraper.fetch_index()
        await scraper.fetch_all(_without_anayasa(entries))
        (cache_dir / "fasil-113.html").write_bytes(b"bozulmus icerik")

        manifest = await scraper.fetch_all(_without_anayasa(entries))
        assert counters[FASIL_PATH] == 2
        assert counters[YASA_PATH] == 1
        assert (cache_dir / "fasil-113.html").read_bytes() == FASIL_BYTES
        assert manifest.entries["fasil-113"].sha256 == hashlib.sha256(FASIL_BYTES).hexdigest()
    finally:
        await scraper.aclose()
        await client.aclose()


# --------------------------------------------------------------- küresel birleştirme


def test_merge_commit_plans_sorts_and_avoids_tag_collisions() -> None:
    fasil_plan = _plan("fasil-1", "2020-01-01")
    yasa_plan = _plan("yasa-2020-1", "2020-01-01")

    merged = merge_commit_plans([[yasa_plan], [fasil_plan]])

    assert [plan.commit_message for plan in merged] == [
        "fasil-1 kök commit",
        "yasa-2020-1 kök commit",
    ]
    assert [plan.tag_name for plan in merged] == [None, "v2020-01-01"]


def test_merge_commit_plans_orders_multiple_years() -> None:
    early = _plan("fasil-2", "1951-06-01")
    late = _plan("yasa-2024-1", "2024-03-15")

    merged = merge_commit_plans([[late], [early]])

    assert [plan.effective_date for plan in merged] == ["1951-06-01", "2024-03-15"]
    assert [plan.tag_name for plan in merged] == ["v1951-06-01", "v2024-03-15"]


# ------------------------------------------------------------- uçtan uca külliyat


def test_corpus_builder_is_deterministic_and_chronological(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    _write_sources(sources)

    summary_one = CorpusBuilder(tmp_path / "repo_1").build_from_dir(sources)
    summary_two = CorpusBuilder(tmp_path / "repo_2").build_from_dir(sources)

    assert summary_one == summary_two
    assert summary_one.law_count == 2
    assert summary_one.article_count == 7
    assert summary_one.commit_count == 5

    engine_one = DeterministicGitEngine(tmp_path / "repo_1", initialize=False)
    engine_two = DeterministicGitEngine(tmp_path / "repo_2", initialize=False)
    assert engine_one.head() == summary_one.head_sha
    assert engine_two.head() == summary_two.head_sha
    assert engine_one.head() == engine_two.head()

    commits = engine_one.list_commits(reverse=True)
    assert len(commits) == 5
    assert [engine_one.effective_date(commit) for commit in commits] == EXPECTED_DATES
    timestamps = [engine_one.commit_timestamp(commit) for commit in commits]
    assert timestamps == sorted(timestamps)

    for effective_date in EXPECTED_DATES:
        tag_commit = engine_one.rev_parse(f"refs/tags/v{effective_date}^{{commit}}")
        assert engine_one.effective_date(tag_commit) == effective_date

    assert engine_one.read_file_as_of("laws/fasil-113/madde-001.md", "2020-01-01") is not None
    assert engine_one.read_file_as_of("laws/yasa-2024-42/madde-001.md", "2020-01-01") is None

    assert engine_one.read_file_as_of("laws/fasil-113/madde-001.md", "2025-01-01") is not None
    assert engine_one.read_file_as_of("laws/yasa-2024-42/madde-001.md", "2025-01-01") is not None
    assert engine_one.read_file_as_of("corpus.json", "2025-01-01") is not None
    assert engine_one.read_file_as_of("REPRODUCE.md", "2025-01-01") is not None


async def test_corpus_builder_from_cache_matches_directory_build(tmp_path: Path) -> None:
    counters: Counter[str] = Counter()
    client = httpx.AsyncClient(transport=_mock_transport(counters))
    scraper = MahkemelerScraper(tmp_path / "raw_cache", index_url=BASE_URL, client=client)

    try:
        entries = await scraper.fetch_index()
        await scraper.fetch_all(_without_anayasa(entries))
    finally:
        await scraper.aclose()
        await client.aclose()

    sources = tmp_path / "sources"
    _write_sources(sources)

    summary_cache = CorpusBuilder(tmp_path / "repo_cache").build_from_cache(tmp_path / "raw_cache")
    summary_dir = CorpusBuilder(tmp_path / "repo_dir").build_from_dir(sources)

    assert summary_cache == summary_dir
    assert summary_cache.law_count == 2
    assert summary_cache.commit_count == 5
