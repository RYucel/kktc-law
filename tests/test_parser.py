"""Faz 2 ayrıştırıcı ve kronolojik replay testleri."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.git_engine import (
    COLONIAL_AUTHOR_NAME,
    DEFAULT_AUTHOR_NAME,
    GIT_EPOCH_DATE,
    DeterministicGitEngine,
)
from tools.models import LegislativeCommitPlan, ParsedArticle, ParsedLaw
from tools.parser import (
    build_chronological_commits,
    extract_amendment_laws,
    extract_amendment_refs,
    normalize_legal_text,
    parse_articles,
    parse_law_header,
)

FIXTURES = Path(__file__).parent / "fixtures"
FASIL_FIXTURE = FIXTURES / "fasil_113_sample.html"
YASA_FIXTURE = FIXTURES / "yasa_2024_42_sample.html"

AMENDMENT_DATES = {"28/1974": "1974-12-15", "41/2018": "2018-09-05"}
EXPECTED_DATES = ["1951-06-01", "1974-12-15", "2018-09-05", "2019-01-01"]
LAW_PREFIX = "laws/fasil-113"


def _parsed_fasil() -> ParsedLaw:
    raw = FASIL_FIXTURE.read_bytes()
    return ParsedLaw(header=parse_law_header(raw), articles=parse_articles(raw))


def _fasil_plans() -> tuple[ParsedLaw, list[LegislativeCommitPlan]]:
    law = _parsed_fasil()
    return law, build_chronological_commits(law, AMENDMENT_DATES)


# --------------------------------------------------------------------- normalizasyon


def test_normalize_legal_text_repairs_mojibake() -> None:
    assert normalize_legal_text("Åžirketler YasasÄ±") == "Şirketler Yasası"
    assert normalize_legal_text("TÃ¼zel kişilik") == "Tüzel kişilik"
    assert normalize_legal_text("Ã‡alışma ve Ã–zgÃ¼rlÃ¼k") == "Çalışma ve Özgürlük"


def test_normalize_legal_text_decodes_windows_1254_bytes() -> None:
    raw = "Şirketler Yasası — 1951".encode("cp1254")
    assert normalize_legal_text(raw) == "Şirketler Yasası — 1951"


def test_normalize_legal_text_decodes_fixture_with_meta_charset() -> None:
    text = normalize_legal_text(FASIL_FIXTURE.read_bytes())
    assert "ŞİRKETLER YASASI" in text
    assert "Şirket Tüzel Kişiliği" in text
    assert "Ã" not in text
    assert "\xa0" not in text


def test_normalize_legal_text_strips_html_and_preserves_blocks() -> None:
    html = (
        "<html><head><style>.x { color: red; }</style>"
        "<script>alert('takip');</script></head>"
        "<body><p>Şirketler&nbsp;Yasası</p>"
        "<table><tr><td>1.</td><td>Kısa&nbsp;İsim</td></tr></table>"
        "<div>Satır<br>Alt</div></body></html>"
    )
    result = normalize_legal_text(html)
    assert "<" not in result
    assert "alert" not in result
    assert "color" not in result
    assert "\xa0" not in result
    assert "Şirketler Yasası" in result.split("\n")
    assert "\n\n\n" not in result
    lines = result.split("\n")
    assert "1." in lines
    assert "Kısa İsim" in lines
    assert "Satır" in lines
    assert "Alt" in lines


# -------------------------------------------------------------------- başlık çözümü


def test_parse_law_header_fasil() -> None:
    header = parse_law_header("FASIL 113\nŞİRKETLER YASASI\n1 Haziran 1951")
    assert header.law_id == "fasil-113"
    assert header.type == "fasil"
    assert header.number == "113"
    assert header.title == "Şirketler Yasası"
    assert header.original_date == "1951-06-01"

    plain = parse_law_header("Fasıl 154")
    assert plain.law_id == "fasil-154"
    assert plain.number == "154"


@pytest.mark.parametrize(
    "text",
    ["42/2024 SAYILI YASA", "42/2024 SAYILI KANUN", "Sayısı: 42/2024"],
)
def test_parse_law_header_modern(text: str) -> None:
    header = parse_law_header(text)
    assert header.law_id == "yasa-2024-42"
    assert header.type == "yasa"
    assert header.number == "42/2024"


def test_parse_law_header_anayasa() -> None:
    header = parse_law_header("KUZEY KIBRIS TÜRK CUMHURİYETİ ANAYASASI")
    assert header.law_id == "anayasa"
    assert header.type == "anayasa"
    assert header.number == "1"


# --------------------------------------------------------------- değişiklik referansı


def test_extract_amendment_refs_formats_and_order() -> None:
    text = (
        "2(a) 34/2019, 3. 41/2018, 4(1)(b) 12/1995, 28/1974 ve 12/85 sayılı Yasalar."
    )
    references = extract_amendment_refs(text)
    assert [item.reference for item in references] == [
        "28/1974",
        "12/1985",
        "12/1995",
        "41/2018",
        "34/2019",
    ]
    by_reference = {item.reference: item for item in references}
    assert by_reference["34/2019"].article_ref == "2(a)"
    assert by_reference["41/2018"].article_ref == "3"
    assert by_reference["12/1995"].article_ref == "4(1)(b)"
    assert by_reference["28/1974"].article_ref is None
    assert by_reference["12/1985"].year == 1985
    assert extract_amendment_laws(text) == [item.reference for item in references]


def test_extract_amendment_refs_ignores_full_dates() -> None:
    references = extract_amendment_refs(
        "1 Haziran 1951 tarihinde, 01/06/1951 ve 1951-06-01 kayıtlarıyla yürürlüğe girmiştir."
    )
    assert references == []


# ------------------------------------------------------------------- madde ayrıştırma


def test_parse_articles_modern_and_temporary() -> None:
    articles = parse_articles(YASA_FIXTURE.read_bytes())
    assert [article.article_id for article in articles] == [
        "madde-001",
        "madde-002",
        "gecici-madde-001",
    ]
    assert articles[0].title == "Amaç"
    assert articles[1].title == "Kapsam"
    assert articles[2].title == "Geçiş Hükümleri"
    assert articles[2].content.startswith("# Geçici Madde 1: Geçiş Hükümleri\n\n")
    assert not articles[0].is_repealed


def test_parse_articles_fasil_plain_text() -> None:
    text = (
        "1951 yılında yürürlüğe girmiştir.\n\n"
        "1. Kısa İsim\n\n"
        "Bu Yasa, Örnek Yasası olarak isimlendirilir.\n\n"
        "(1) Alt fıkra madde sayılmaz.\n\n"
        "2. Kapsam\n\n"
        "Bu Yasa tüm şirketlere uygulanır.\n"
    )
    articles = parse_articles(text)
    assert [article.article_id for article in articles] == ["madde-001", "madde-002"]
    assert articles[0].title == "Kısa İsim"
    assert "(1) Alt fıkra madde sayılmaz." in articles[0].body
    assert articles[1].title == "Kapsam"


def test_parse_fasil_113_fixture() -> None:
    law = _parsed_fasil()
    assert law.law_id == "fasil-113"
    assert law.title == "Şirketler Yasası"
    assert [article.article_id for article in law.articles] == [
        "madde-001",
        "madde-002",
        "madde-004",
        "madde-005",
    ]

    madde_001 = law.article("madde-001")
    assert madde_001 is not None
    assert madde_001.title == "Kısa İsim"
    assert madde_001.content == (
        "# Madde 1: Kısa İsim\n\n"
        "Bu Yasa, Şirketler Yasası olarak isimlendirilir.\n"
    )

    madde_002 = law.article("madde-002")
    assert madde_002 is not None
    assert madde_002.amendments == ["28/1974"]
    assert "Tescil belgesi" in madde_002.body

    madde_004 = law.article("madde-004")
    assert madde_004 is not None
    assert madde_004.amendments == ["41/2018", "34/2019"]
    assert "Tüzel kişilik" in madde_004.body
    assert "Ã" not in madde_004.content

    madde_005 = law.article("madde-005")
    assert madde_005 is not None
    assert madde_005.is_repealed is True
    assert madde_005.amendments == ["28/1974"]


# ---------------------------------------------------------------- kronolojik planlama


def test_build_chronological_commits_dates_authors_and_tags() -> None:
    law, plans = _fasil_plans()
    assert [plan.effective_date for plan in plans] == EXPECTED_DATES
    assert plans[0].author_name == COLONIAL_AUTHOR_NAME
    assert all(plan.author_name == DEFAULT_AUTHOR_NAME for plan in plans[1:])
    assert [plan.tag_name for plan in plans] == [f"v{date}" for date in EXPECTED_DATES]
    assert plans[0].commit_message.startswith("fasil-113: 1951-06-01 — ilk yürürlük")
    assert plans[3].amending_law == "34/2019"
    assert plans[3].affected_articles == ["madde-004"]

    root_files = plans[0].file_changes
    assert set(root_files) == {
        f"{LAW_PREFIX}/madde-001.md",
        f"{LAW_PREFIX}/madde-002.md",
        f"{LAW_PREFIX}/madde-004.md",
        f"{LAW_PREFIX}/madde-005.md",
        f"{LAW_PREFIX}/metadata.json",
    }
    metadata = json.loads(plans[-1].file_changes[f"{LAW_PREFIX}/metadata.json"])
    assert [record["amending_law"] for record in metadata["consolidation_history"]] == [
        "28/1974",
        "41/2018",
        "34/2019",
    ]
    assert metadata["consolidation_history"][2]["effective_date"] == "2019-01-01"
    assert metadata["original_date"] == "1951-06-01"

    # Plan üretimi saf ve deterministiktir.
    assert [plan.model_dump() for plan in plans] == [
        plan.model_dump() for plan in build_chronological_commits(law, AMENDMENT_DATES)
    ]


def test_build_chronological_commits_uses_override_dates() -> None:
    law = _parsed_fasil()
    plans = build_chronological_commits(
        law,
        {
            "28/1974": "1974-12-15",
            "41/2018": "2018-09-05",
            "34/2019": "2019-06-01",
        },
    )
    assert [plan.effective_date for plan in plans][-1] == "2019-06-01"


def test_build_chronological_commits_uses_fallback_original_date() -> None:
    header = parse_law_header("FASIL 113")
    article = ParsedArticle(article_id="madde-001", title="Kısa İsim", body="Metin.")
    plans = build_chronological_commits(ParsedLaw(header=header, articles=[article]))

    assert header.original_date == "1959-01-01"
    assert header.date_source == "fallback_1959"
    assert plans[0].effective_date == "1959-01-01"



# ---------------------------------------------------------------------- uçtan uca


def test_end_to_end_fasil_113_is_deterministic(tmp_path: Path) -> None:
    _, plans = _fasil_plans()
    engine_a = DeterministicGitEngine(tmp_path / "repo_a")
    engine_b = DeterministicGitEngine(tmp_path / "repo_b")

    for plan in plans:
        assert plan.apply_to(engine_a) == engine_a.head()
        engine_b.apply_legislative_commit(
            effective_date=plan.effective_date,
            author_name=plan.author_name,
            commit_message=plan.commit_message,
            file_changes={
                path: content.replace("\n", "\r\n")
                for path, content in reversed(list(plan.file_changes.items()))
            },
            tag_name=plan.tag_name,
        )

    assert engine_a.head() == engine_b.head()
    assert engine_a.root_commit() == engine_b.root_commit()
    assert engine_a.list_files_at_commit("HEAD") == engine_b.list_files_at_commit("HEAD")

    shas = engine_a.list_commits(reverse=True)
    assert len(shas) == 4
    assert [engine_a.effective_date(sha) for sha in shas] == EXPECTED_DATES
    assert engine_a.commit_date(shas[0]) == GIT_EPOCH_DATE

    for date, sha in zip(EXPECTED_DATES, shas, strict=True):
        tag_commit = engine_a.rev_parse(f"refs/tags/v{date}^{{commit}}")
        assert tag_commit == sha

    madde_005 = engine_a.read_file_as_of(f"{LAW_PREFIX}/madde-005.md", "2020-01-01")
    assert madde_005 is not None
    assert "kaldırılmıştır" in madde_005

    metadata_2018 = json.loads(
        engine_a.read_file_as_of(f"{LAW_PREFIX}/metadata.json", "2018-09-06") or "{}"
    )
    assert [record["amending_law"] for record in metadata_2018["consolidation_history"]] == [
        "28/1974",
        "41/2018",
    ]

    metadata_2019 = json.loads(
        engine_a.read_file_as_of(f"{LAW_PREFIX}/metadata.json", "2019-01-02") or "{}"
    )
    assert [record["amending_law"] for record in metadata_2019["consolidation_history"]] == [
        "28/1974",
        "41/2018",
        "34/2019",
    ]
