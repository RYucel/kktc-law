"""Faz 8: tarih anomalisi koruması, Anayasa ve gerçek yama motoru testleri."""

from __future__ import annotations

from pathlib import Path

from tools.amendment_patcher import parse_amendment_instructions
from tools.git_engine import DeterministicGitEngine
from tools.models import ParsedLaw
from tools.parser import (
    build_chronological_commits,
    extract_amendment_refs,
    parse_articles,
    parse_law_header,
)
from tools.scraper import parse_mahkemeler_index

BASE_URL = "https://www.mahkemeler.net/mevzuat/"


def test_two_digit_years_use_historical_pivot() -> None:
    refs = extract_amendment_refs("12/47 ve 1/27")
    assert {ref.reference for ref in refs} == {"12/1947", "1/1927"}

    future = extract_amendment_refs("27/2047")
    assert future == []


def test_anayasa_is_always_first_in_index_and_has_override_date() -> None:
    entries = parse_mahkemeler_index(
        "<html><body><a href='/fasil-113.html'>Fasıl 113</a></body></html>",
        BASE_URL,
    )
    assert entries[0].law_id == "anayasa"
    assert entries[0].category == "anayasa"

    header = parse_law_header(
        "Kuzey Kıbrıs Türk Cumhuriyeti Anayasası\n1985 Anayasası",
        date_overrides={"anayasa": "1985-05-05"},
    )
    assert header.law_id == "anayasa"
    assert header.original_date == "1985-05-05"
    assert header.date_source == "override"


def test_anayasa_preamble_and_temporary_articles_are_parsed() -> None:
    html = """
    <html><body>
      <p>Kuzey Kıbrıs Türk Cumhuriyeti Anayasası</p>
      <p>5 Mayıs, 1985 tarihinde Halkoylamasına sunulup kabul edilmiştir.</p>
      <p>BAŞLANGIÇ</p>
      <p>Kıbrıs Türk Halkı, tarihi boyunca özgürlük için mücadele etmiştir.</p>
      <p>Madde 1. Devletin şekli Cumhuriyettir.</p>
      <p>Geçici Madde 1. Bu Anayasa yayımıyla yürürlüğe girer.</p>
    </body></html>
    """
    articles = {article.article_id: article for article in parse_articles(html)}

    assert "madde-000" in articles
    assert "Kıbrıs Türk Halkı" in articles["madde-000"].body
    assert "gecici-madde-001" in articles


def test_amendment_patterns_are_parsed() -> None:
    text = (
        "Esas Yasanın 5'inci maddesi kaldırılarak yerine aşağıdaki yeni 5'inci madde konur: "
        "Yeni madde metni.\n"
        "Esas Yasanın 5'inci maddesinin (2) fıkrasında yer alan 'eski ibare' ibaresi "
        "çıkarılarak yerine 'yeni ibare' ibaresi konur.\n"
        "Esas Yasanın 5'inci maddesi yürürlükten kaldırılır."
    )
    patches = parse_amendment_instructions(text)
    assert [patch.action for patch in patches] == [
        "replace_article",
        "replace_text",
        "repeal_article",
    ]
    assert patches[0].target_article == "madde-005"
    assert patches[1].old_text == "eski ibare"
    assert patches[1].new_text == "yeni ibare"


def test_real_patch_produces_git_diff(tmp_path: Path) -> None:
    html = """
    <html><body>
      <p>FASIL 1</p>
      <p>YAMA TESTİ YASASI</p>
      <p>1 Ocak 1950 tarihinde yürürlüğe girmiştir.</p>
      <p>1. Eski metin burada yer alır.</p>
    </body></html>
    """
    header = parse_law_header(html)
    articles = parse_articles(html)
    parsed = ParsedLaw(header=header, articles=articles)
    patch = parse_amendment_instructions(
        "Esas Yasanın 1'inci maddesinin (1) fıkrasında yer alan 'Eski metin' "
        "ibaresi çıkarılarak yerine 'Yeni metin' ibaresi konur."
    )[0]
    patch = patch.model_copy(update={"effective_date": "1960-01-01", "amending_law": "1/1960"})
    plans = build_chronological_commits(parsed, patches=[patch])

    engine = DeterministicGitEngine(tmp_path / "repo")
    for plan in plans:
        plan.apply_to(engine)

    diff = engine.diff_article("fasil-1", "madde-001", "1950-01-01", "1960-01-01")
    assert "-" in diff and "Eski metin" in diff
    assert "+" in diff and "Yeni metin" in diff
