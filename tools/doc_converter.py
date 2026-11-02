"""Platform bağımsız belge dönüştürücü.

Gerçek portal yasaları `.doc`, `.docx`, `.rtf`, `.pdf` ve HTML olarak yayımlanır.
Parser HTML beklediği için bu modül belgeleri sırasıyla şu yollarla çevirir:

* `.docx` → `python-docx` (paragraf/tablo sırası korunur),
* `.pdf` → `pypdf` metin çıkarımı,
* `.doc`/`.rtf` → LibreOffice headless, Word otomasyonu veya saf Python binary
  metin fallback'i.

Dönüştürme başarısız olursa `DocumentConversionError` üretilir; `CorpusBuilder`
bunu `skipped_laws` içine yazar.
"""

from __future__ import annotations

import contextlib
import html
import io
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

__all__ = ["DocumentConversionError", "convert_document_to_html"]

_HTML_SUFFIXES = frozenset({".htm", ".html"})
_LEGACY_SUFFIXES = frozenset({".doc", ".rtf"})
_WORD_FORMATS = frozenset({".doc", ".docx", ".rtf", ".pdf"})
_WD_FORMAT_FILTERED_HTML = 10
_UTF8_CODEPAGE = 65001
_TEXT_RUN_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9 .,;:()\[\]\-–—/\n\r]{4,}")


class DocumentConversionError(RuntimeError):
    """Belge HTML'e dönüştürülemediğinde oluşan hata."""


def _wrap_text_as_html(text: str) -> bytes:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        raise DocumentConversionError("Belgeden okunabilir metin çıkarılamadı")
    body = "\n".join(f"<p>{html.escape(line)}</p>" for line in lines)
    return f"<!DOCTYPE html><html><body>{body}</body></html>".encode()


def _convert_docx_to_html(data: bytes) -> bytes:
    try:
        from docx import Document
        from docx.document import Document as DocumentClass
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - bağımlılık kurulmamışsa
        raise DocumentConversionError(
            ".docx dönüştürücü için python-docx gerekir"
        ) from exc

    def iter_blocks(document: Any) -> Iterator[Any]:
        if isinstance(document, DocumentClass):
            parent = document.element.body
        else:
            parent = document._tc
        for child in parent.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, document)
            elif child.tag == qn("w:tbl"):
                yield Table(child, document)

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise DocumentConversionError(f".docx açılamadı: {exc}") from exc

    parts: list[str] = ["<!DOCTYPE html><html><body>"]
    for block in iter_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                parts.append(f"<p>{html.escape(text)}</p>")
            continue
        parts.append("<table>")
        for row in block.rows:
            cells = [html.escape(" ".join(cell.text.split())) for cell in row.cells]
            parts.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "\n".join(parts).encode("utf-8")


def _convert_pdf_to_html(data: bytes) -> bytes:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - bağımlılık kurulmamışsa
        raise DocumentConversionError("PDF dönüştürücü için pypdf gerekir") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise DocumentConversionError(f"PDF okunamadı: {exc}") from exc
    return _wrap_text_as_html(text)


def _convert_with_soffice(data: bytes, suffix: str) -> bytes:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if executable is None:
        raise DocumentConversionError("LibreOffice (soffice) bulunamadı")
    with tempfile.TemporaryDirectory(
        prefix="kktc-law-soffice-", ignore_cleanup_errors=True
    ) as temporary:
        directory = Path(temporary)
        source = directory / f"source{suffix}"
        source.write_bytes(data)
        try:
            completed = subprocess.run(
                [
                    executable,
                    "--headless",
                    "--convert-to",
                    "html",
                    "--outdir",
                    str(directory),
                    str(source),
                ],
                capture_output=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DocumentConversionError(f"LibreOffice dönüşümü başarısız: {exc}") from exc
        target = directory / "source.html"
        if completed.returncode != 0 or not target.is_file():
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise DocumentConversionError(f"LibreOffice dönüşümü başarısız: {detail}")
        return target.read_bytes()


def _load_word() -> object:
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - Windows dışı ortam
        raise DocumentConversionError("Word otomasyonu için pywin32 gerekir") from exc
    try:
        return win32com.client.DispatchEx("Word.Application")
    except Exception as exc:  # pragma: no cover - Word kurulu değilse
        raise DocumentConversionError("Microsoft Word otomasyonu başlatılamadı") from exc


def _convert_with_word(data: bytes, suffix: str) -> bytes:
    word = _load_word()
    with tempfile.TemporaryDirectory(
        prefix="kktc-law-word-", ignore_cleanup_errors=True
    ) as temporary:
        directory = Path(temporary)
        source = directory / f"source{suffix}"
        target = directory / "converted.html"
        source.write_bytes(data)
        document = None
        try:
            documents = word.Documents  # type: ignore[attr-defined]
            document = documents.Open(
                str(source),
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )
            document.SaveAs2(
                str(target),
                FileFormat=_WD_FORMAT_FILTERED_HTML,
                Encoding=_UTF8_CODEPAGE,
            )
            document.Close(False)
            document = None
        except Exception as exc:  # pragma: no cover - Word'e bağlı
            raise DocumentConversionError(f"Word dönüşümü başarısız: {exc}") from exc
        finally:
            if document is not None:
                with contextlib.suppress(Exception):
                    document.Close(False)
            with contextlib.suppress(Exception):
                word.Quit()  # type: ignore[attr-defined]
        if not target.is_file():
            raise DocumentConversionError("Word dönüşüm çıktısı oluşmadı")
        return target.read_bytes()


def _binary_doc_text(data: bytes) -> str:
    """Eski binary `.doc` içinden UTF-16LE ve CP1254 metin akışı çıkarır."""
    candidates: list[str] = []
    for encoding in ("utf-16le", "cp1254"):
        try:
            decoded = data.decode(encoding, errors="ignore")
        except LookupError:  # pragma: no cover - Python standart kodlamaları
            continue
        runs = _TEXT_RUN_RE.findall(decoded)
        text = "\n".join(" ".join(run.split()) for run in runs if run.strip())
        if text:
            candidates.append(text)
    if not candidates:
        return ""
    return max(candidates, key=len)


def convert_document_to_html(data: bytes, suffix: str) -> bytes:
    """Ham belge baytlarını parser'a verilebilecek UTF-8 HTML'e çevirir."""
    normalized_suffix = suffix.lower()
    if normalized_suffix in _HTML_SUFFIXES:
        return data
    if normalized_suffix not in _WORD_FORMATS:
        raise DocumentConversionError(
            f"Desteklenmeyen belge türü: {suffix or '(uzantısız)'}"
        )
    if normalized_suffix == ".docx":
        return _convert_docx_to_html(data)
    if normalized_suffix == ".pdf":
        return _convert_pdf_to_html(data)

    errors: list[str] = []
    for converter in (
        lambda: _convert_with_soffice(data, normalized_suffix),
        lambda: _convert_with_word(data, normalized_suffix),
    ):
        try:
            return converter()
        except DocumentConversionError as exc:
            errors.append(str(exc))

    text = _binary_doc_text(data)
    if text:
        return _wrap_text_as_html(text)
    detail = "; ".join(errors) or "bilinmeyen dönüştürme hatası"
    raise DocumentConversionError(f"{normalized_suffix} dönüştürülemedi: {detail}")
