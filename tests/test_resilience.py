"""Faz 5 filtreleme, indeks keşfi ve dayanıklılık testleri."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools.build_corpus import CorpusBuilder
from tools.build_corpus import app as build_app
from tools.models import LawIndexEntry
from tools.parser import parse_law_header
from tools.scraper import discover_index_pages, filter_entries

FIXTURES = Path(__file__).parent / "fixtures"
FASIL_BYTES = (FIXTURES / "fasil_113_sample.html").read_bytes()


def _entry(law_id: str, title: str, url: str) -> LawIndexEntry:
    category = "fasil" if law_id.startswith("fasil-") else "yasa"
    number = law_id.removeprefix("fasil-") if category == "fasil" else "42/2024"
    return LawIndexEntry(
        law_id=law_id,
        title=title,
        url=url,
        category=category,  # type: ignore[arg-type]
        number=number,
    )


def test_filter_entries_matches_id_or_title_and_limits() -> None:
    entries = [
        _entry("fasil-113", "Şirketler Yasası", "https://example.test/fasil-113.html"),
        _entry("fasil-154", "Ceza Yasası", "https://example.test/fasil-154.html"),
        _entry("yasa-2024-42", "Kişisel Verilerin Korunması Yasası", "https://example.test/yasa.html"),
    ]

    selected = filter_entries(entries, matches=["ŞİRKETLER", "yasa-2024"], limit=2)
    assert [item.law_id for item in selected] == ["fasil-113", "yasa-2024-42"]

    assert filter_entries(entries, matches=["anayasa"]) == []
    with pytest.raises(ValueError, match="limit"):
        filter_entries(entries, limit=0)


def test_discover_index_pages_finds_iframe_and_subcategory_but_not_laws() -> None:
    html = """
    <html><body>
      <iframe src="/mevzuat/fihrist-iframe.html"></iframe>
      <a href="/mevzuat/fasillar.html">Fasıl Listesi</a>
      <a href="/mevzuat/fasil-1.html">Fasıl 1</a>
      <a href="/mevzuat/iletisim.html">İletişim</a>
    </body></html>
    """
    pages = discover_index_pages(html, "https://example.test/mevzuat/")

    assert "https://example.test/mevzuat/fihrist-iframe.html" in pages
    assert "https://example.test/mevzuat/fasillar.html" in pages
    assert all("fasil-1.html" not in page for page in pages)
    assert all("iletisim.html" not in page for page in pages)


def test_builder_skips_empty_law_and_keeps_building(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_113.html").write_bytes(FASIL_BYTES)
    (sources / "fasil_999.html").write_text(
        """<!DOCTYPE html><html><body>
        <p>FASIL 999</p>
        <p>BOŞ YASA</p>
        <p>Bu belgede hiçbir madde bulunmamaktadır.</p>
        </body></html>""",
        encoding="utf-8",
    )

    summary = CorpusBuilder(tmp_path / "repo").build_from_dir(sources)

    assert summary.law_count == 1
    assert summary.article_count > 0
    assert [item.law_id for item in summary.skipped_laws] == ["fasil-999"]
    assert "madde" in summary.skipped_laws[0].reason.lower()


def test_builder_strict_mode_raises_for_empty_law(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_999.html").write_text(
        """<!DOCTYPE html><html><body>
        <p>FASIL 999</p>
        <p>BOŞ YASA</p>
        <p>Bu belgede hiçbir madde bulunmamaktadır.</p>
        </body></html>""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="madde"):
        CorpusBuilder(tmp_path / "repo", strict=True).build_from_dir(sources)


def test_builder_match_and_limit_apply_to_sources(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_113.html").write_bytes(FASIL_BYTES)
    (sources / "yasa_2024_42.html").write_bytes(
        (FIXTURES / "yasa_2024_42_sample.html").read_bytes()
    )

    summary = CorpusBuilder(
        tmp_path / "repo",
        matches=["fasil-113"],
        limit=1,
    ).build_from_dir(sources)

    assert summary.law_count == 1
    assert summary.head_sha


def test_build_cli_accepts_limit_and_match(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "fasil_113.html").write_bytes(FASIL_BYTES)
    (sources / "yasa_2024_42.html").write_bytes(
        (FIXTURES / "yasa_2024_42_sample.html").read_bytes()
    )
    runner = CliRunner()

    result = runner.invoke(
        build_app,
        [
            "--repo-path",
            str(tmp_path / "repo"),
            "--from-dir",
            str(sources),
            "--limit",
            "1",
            "--match",
            "fasil-113",
        ],
    )

    assert result.exit_code == 0
    assert '"law_count": 1' in result.output
    assert "skipped_laws" in result.output


def test_bolum_header_is_recognized_as_fasil() -> None:
    html = """
    <html><body>
      <p>BÖLÜM 154</p>
      <p>CEZA YASASI</p>
      <p>1. Bu Yasa, Ceza Yasası olarak isimlendirilir.</p>
    </body></html>
    """
    header = parse_law_header(html)

    assert header.law_id == "fasil-154"
    assert header.type == "fasil"

