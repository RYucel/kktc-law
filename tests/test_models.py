"""Pydantic v2 şemalarının doğrulama ve normalizasyon testleri."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tools.models import (
    AmendmentRecord,
    ArticleVersion,
    LawMetadata,
    normalize_article_id,
    validate_iso_date,
)


def test_normalize_article_id_variants() -> None:
    assert normalize_article_id("madde-4") == "madde-004"
    assert normalize_article_id("Madde 12") == "madde-012"
    assert normalize_article_id("madde-004.md") == "madde-004"
    assert normalize_article_id("Geçici Madde 3") == "gecici-madde-003"
    assert normalize_article_id("gecici-madde-1") == "gecici-madde-001"

    with pytest.raises(ValueError):
        normalize_article_id("")
    with pytest.raises(ValueError):
        normalize_article_id("ek-1")


@pytest.mark.parametrize("value", ["1951-06-01", "2024-02-29", "1970-01-01"])
def test_validate_iso_date_accepts_valid(value: str) -> None:
    assert validate_iso_date(value) == value


@pytest.mark.parametrize("value", ["1951-6-1", "1951/06/01", "2024-02-30", "01-06-1951", ""])
def test_validate_iso_date_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        validate_iso_date(value)


def test_amendment_record_normalizes_fields() -> None:
    record = AmendmentRecord(
        amending_law=" 41/2018 ",
        effective_date="2018-09-05",
        affected_articles=["madde-4", "Madde 4", "gecici madde 2"],
        commit_message="41/2018: madde 4 değişti",
    )
    assert record.amending_law == "41/2018"
    assert record.affected_articles == ["madde-004", "gecici-madde-002"]
    assert record.sort_key == ("2018-09-05", "41/2018")

    with pytest.raises(ValidationError):
        AmendmentRecord(
            amending_law="",
            effective_date="2018-09-05",
            affected_articles=[],
            commit_message="x",
        )
    with pytest.raises(ValidationError):
        AmendmentRecord(
            amending_law="41/2018",
            effective_date="05.09.2018",
            affected_articles=[],
            commit_message="x",
        )
    with pytest.raises(ValidationError):
        AmendmentRecord(
            amending_law="41/2018",
            effective_date="2018-09-05",
            affected_articles=[],
        )


def test_article_version_normalizes_content() -> None:
    version = ArticleVersion(
        article_id="madde-4",
        title="Şirket Tüzel Kişiliği",
        content="Birinci satır.   \r\nİkinci satır.\r\n\r\n",
        effective_date="1951-06-01",
    )
    assert version.article_id == "madde-004"
    assert version.content == "Birinci satır.\nİkinci satır."
    assert version.amending_law is None
    assert version.to_markdown() == (
        "# Madde 4: Şirket Tüzel Kişiliği\n\nBirinci satır.\nİkinci satır.\n"
    )

    with pytest.raises(ValidationError):
        ArticleVersion(
            article_id="madde-004",
            title="",
            content="x",
            effective_date="1951-06-01",
        )
    with pytest.raises(ValidationError):
        ArticleVersion(
            article_id="madde-004",
            title="Başlık",
            content="x",
            effective_date="1951-06-01",
            amending_law="   ",
        )


def test_law_metadata_sorts_history_and_links_articles() -> None:
    metadata = LawMetadata(
        law_id="fasil-113",
        type="fasil",
        number="113",
        title="Şirketler Yasası",
        original_date="1951-06-01",
        consolidation_history=[
            {
                "amending_law": "41/2018",
                "effective_date": "2018-09-05",
                "affected_articles": ["madde-004"],
                "commit_message": "41/2018: madde 4",
            },
            {
                "amending_law": "28/1974",
                "effective_date": "1974-12-15",
                "affected_articles": ["madde-002", "madde-014"],
                "commit_message": "28/1974: maddeler 2 ve 14",
            },
        ],
    )

    assert [record.amending_law for record in metadata.consolidation_history] == [
        "28/1974",
        "41/2018",
    ]
    assert [record.amending_law for record in metadata.history_for("madde-4")] == ["41/2018"]
    assert metadata.history_for("madde-2")[0].amending_law == "28/1974"

    # Deterministik serileştirme: anahtarlar sıralı, Türkçe karakterler korunur.
    dumped = json.dumps(
        metadata.model_dump(), sort_keys=True, indent=2, ensure_ascii=False
    )
    assert dumped.startswith('{\n  "consolidation_history"')
    assert "Şirketler Yasası" in dumped


def test_law_metadata_rejects_inconsistent_input() -> None:
    base = {
        "type": "fasil",
        "number": "113",
        "title": "Şirketler Yasası",
        "original_date": "1951-06-01",
        "consolidation_history": [],
    }

    with pytest.raises(ValidationError):
        LawMetadata(law_id="yasa-2024-42", **base)
    with pytest.raises(ValidationError):
        LawMetadata(law_id="fasil-113", **{**base, "type": "yasa"})
    with pytest.raises(ValidationError):
        LawMetadata(law_id="anayasa-1985", **{**base, "type": "anayasa"})
    with pytest.raises(ValidationError):
        LawMetadata(law_id="fasil-113", **{**base, "original_date": "1951-13-01"})
    with pytest.raises(ValidationError):
        LawMetadata(
            law_id="fasil-113",
            **{
                **base,
                "consolidation_history": [
                    {
                        "amending_law": "1/1940",
                        "effective_date": "1940-01-01",
                        "affected_articles": ["madde-001"],
                        "commit_message": "eski",
                    }
                ],
            },
        )

    valid = LawMetadata(law_id="anayasa", type="anayasa", number="1", **{
        "title": "KKTC Anayasası",
        "original_date": "1985-05-07",
        "consolidation_history": [],
    })
    assert valid.law_id == "anayasa"
