"""Faz 6: tarih fallback, kenar başlığı hizalaması ve platform bağımsız dönüştürücü."""

from __future__ import annotations

import io
import json
from pathlib import Path

from docx import Document

from tools.doc_converter import convert_document_to_html
from tools.models import LawHeader
from tools.parser import load_law_date_overrides, parse_articles, parse_law_header


def test_header_date_fallbacks_and_override(tmp_path: Path) -> None:
    fallback = parse_law_header("FASIL 999\nBOŞ YASA")
    assert fallback.law_id == "fasil-999"
    assert fallback.original_date == "1959-01-01"
    assert fallback.date_source == "fallback_1959"

    older = parse_law_header(
        "FASIL 999\nESKİ YASA\n12/1954 sayılı Yasa ile değiştirilmiştir."
    )
    assert older.original_date == "1954-01-01"
    assert older.date_source == "fallback_1959"

    edition = parse_law_header("FASIL 998\nESKİ YASA\n[1954 Baskısı]")
    assert edition.original_date == "1954-01-01"
    assert edition.date_source == "fallback_1959"

    modern = parse_law_header("42/2024 Sayılı Örnek Yasa")
    assert modern.law_id == "yasa-2024-42"
    assert modern.original_date == "2024-01-01"
    assert modern.date_source == "fallback_law_year"

    override_path = tmp_path / "overrides.json"
    override_path.write_text(
        json.dumps({"fasil-999": "1960-05-01"}), encoding="utf-8"
    )
    overrides = load_law_date_overrides(override_path)
    assert overrides == {"fasil-999": "1960-05-01"}

    overridden = parse_law_header(
        "FASIL 999\nBOŞ YASA", date_overrides={"fasil-999": "1960-05-01"}
    )
    assert overridden.original_date == "1960-05-01"
    assert overridden.date_source == "override"


def test_header_date_source_is_copied_to_metadata() -> None:
    header = LawHeader(
        law_id="fasil-999",
        type="fasil",
        number="999",
        title="Örnek",
        original_date="1959-01-01",
        date_source="fallback_1959",
    )
    metadata = header.to_metadata()
    assert metadata.date_source == "fallback_1959"


def test_marginal_note_moves_from_previous_article_to_next() -> None:
    html = """
    <html><body>
      <p>FASIL 999</p>
      <p>ÖRNEK YASA</p>
      <p>1. Bu Yasa, Örnek Yasa olarak isimlendirilir.</p>
      <p>Tefsir</p>
      <p>2(a) 34/2019</p>
      <p>2. Bu Yasada kullanılan deyimler aşağıdaki anlamları taşır.</p>
    </body></html>
    """
    articles = {article.article_id: article for article in parse_articles(html)}

    first = articles["madde-001"]
    second = articles["madde-002"]
    assert "Tefsir" not in first.body
    assert first.title == "Kısa İsim" or first.title == ""
    assert second.title == "Tefsir"
    assert "34/2019" in second.amendments


def test_docx_converter_preserves_paragraph_and_table_order() -> None:
    document = Document()
    document.add_paragraph("FASIL 999")
    document.add_paragraph("ÖRNEK YASA")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "1. Bu Yasa, Örnek Yasa olarak isimlendirilir."
    buffer = io.BytesIO()
    document.save(buffer)

    html = convert_document_to_html(buffer.getvalue(), ".docx")
    text = html.decode("utf-8")

    assert "FASIL 999" in text
    assert "ÖRNEK YASA" in text
    assert "<table>" in text
    assert "Örnek Yasa olarak isimlendirilir" in text
