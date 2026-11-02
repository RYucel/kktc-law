"""Ultra Vires / normlar hiyerarşisi denetçisi.

Denetim deterministiktir: Git snapshot'ındaki kaynaklar, atıf çözümlemesi, tarihsel
yürürlük ve norm seviyesi üzerinden bulgular üretir. Hukuki yorum ve nihai mütalaa
LLM prompt'una bırakılır; bu modül yalnızca doğrulanabilir sinyaller üretir.
"""

from __future__ import annotations

import re

from tools.git_engine import DeterministicGitEngine, is_repealed_text
from tools.models import (
    AuditFinding,
    AuditSource,
    UltraViresReport,
)

__all__ = ["audit_norm", "extract_norm_citations", "norm_level"]

_LEVEL_BY_SUBJECT: dict[str, int] = {
    "anayasa": 1,
    "constitution": 1,
    "fasil": 2,
    "yasa": 2,
    "law": 2,
    "statute": 2,
    "secondary": 3,
    "tuzuk": 3,
    "tüzük": 3,
    "regulation": 3,
}
_ANAYASA_RE = re.compile(r"\banayasa\b", re.IGNORECASE)
_FASIL_RE = re.compile(r"\bfas[ıi]l\s*(?:no\.?)?\s*[-:]?\s*(\d{1,3})\b", re.IGNORECASE)
_FASIL_SLUG_RE = re.compile(r"\bfasil-(\d{1,3})\b", re.IGNORECASE)
_YASA_RE = re.compile(r"\b(\d{1,3})\s*/\s*(\d{4})\b")
_YASA_SLUG_RE = re.compile(r"\byasa-(\d{4})-(\d{1,3})\b", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"(?:\bmadde\s*[-:]?\s*|\b)(\d{1,3})\s*\.?\s*maddesi", re.IGNORECASE)
_CONFLICT_WORDS = ("aykırı", "aksine", "ihlal", "değiştir", "kaldır", "yetkisiz")


def norm_level(subject_level: str) -> int:
    """Norm seviyesini 1 (Anayasa), 2 (yasa) veya 3 (ikincil) olarak döndürür."""
    key = subject_level.strip().lower()
    if key in _LEVEL_BY_SUBJECT:
        return _LEVEL_BY_SUBJECT[key]
    raise ValueError(f"Bilinmeyen norm seviyesi: {subject_level!r}")


def _citation_article(text: str) -> str | None:
    match = _ARTICLE_RE.search(text)
    if match is None:
        return None
    return f"madde-{int(match.group(1)):03d}"


def extract_norm_citations(text: str) -> list[tuple[str, str | None]]:
    """Metindeki Anayasa/Fasıl/Yasa ve madde atıflarını çıkarır."""
    found: list[tuple[str, str | None]] = []
    article = _citation_article(text)
    if _ANAYASA_RE.search(text):
        found.append(("anayasa", article))
    for match in _FASIL_RE.finditer(text):
        found.append((f"fasil-{int(match.group(1))}", article))
    for match in _FASIL_SLUG_RE.finditer(text):
        found.append((f"fasil-{int(match.group(1))}", article))
    for match in _YASA_SLUG_RE.finditer(text):
        found.append((f"yasa-{match.group(1)}-{int(match.group(2))}", article))
    for match in _YASA_RE.finditer(text):
        found.append((f"yasa-{match.group(2)}-{int(match.group(1))}", article))

    result: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for citation in found:
        if citation not in seen:
            seen.add(citation)
            result.append(citation)
    return result


def _source_level(law_type: str) -> int:
    return 1 if law_type == "anayasa" else 2


def _has_conflict_language(text: str) -> bool:
    folded = text.replace("İ", "i").replace("I", "ı").casefold()
    return any(word in folded for word in _CONFLICT_WORDS)


def audit_norm(
    subject_text: str,
    as_of_date: str,
    *,
    subject_level: str = "secondary",
    engine: DeterministicGitEngine,
) -> UltraViresReport:
    """Bir hüküm/iddiayı tarihsel Git snapshot'ındaki normlara göre denetler.

    Args:
        subject_text: Denetlenecek sözleşme hükmü, tüzük maddesi veya idari işlem.
        as_of_date: Olay tarihi; yalnızca o tarihte yürürlükte olan snapshot kullanılır.
        subject_level: Denetlenen metnin norm seviyesi (`secondary`, `yasa`, `anayasa`).
        engine: Külliyat Git motoru.

    Returns:
        `UltraViresReport`: tarih/commit, kaynaklar ve deterministik bulgular.
    """
    expected_level = norm_level(subject_level)
    snapshot = engine.resolve_commit_as_of(as_of_date)
    findings: list[AuditFinding] = []
    sources: list[AuditSource] = []

    if snapshot is None:
        return UltraViresReport(
            as_of_date=as_of_date,
            snapshot_commit="",
            subject_level=subject_level,
            findings=[
                AuditFinding(
                    severity="high",
                    code="TEMPORAL_SNAPSHOT_MISSING",
                    message=f"{as_of_date} için yürürlükte bir Git snapshot'ı bulunamadı.",
                )
            ],
        )

    citations = extract_norm_citations(subject_text)
    for law_id, article_id in citations:
        metadata = engine.get_law_metadata(law_id, as_of_date)
        if metadata is None:
            findings.append(
                AuditFinding(
                    severity="medium",
                    code="CITATION_NOT_FOUND",
                    message=f"Atıf yapılan mevzuat snapshot'ta bulunamadı: {law_id}",
                    law_id=law_id,
                    article_id=article_id,
                )
            )
            continue
        actual_article = article_id or "madde-001"
        content = engine.read_article_as_of(law_id, actual_article, as_of_date)
        if content is None:
            findings.append(
                AuditFinding(
                    severity="medium",
                    code="CITATION_ARTICLE_NOT_FOUND",
                    message=f"{law_id}/{actual_article} snapshot'ta bulunamadı.",
                    law_id=law_id,
                    article_id=actual_article,
                )
            )
            continue
        source_level = _source_level(metadata.type)
        sources.append(
            AuditSource(
                law_id=law_id,
                article_id=actual_article,
                title=metadata.title,
                norm_level=source_level,
                content=content,
                commit_sha=snapshot,
                effective_date=engine.effective_date(snapshot),
            )
        )
        if source_level < expected_level and _has_conflict_language(subject_text):
            findings.append(
                AuditFinding(
                    severity="high",
                    code="ULTRA_VIRES",
                    message=(
                        f"Alt norm ({subject_level}) üst normu ({law_id}) değiştirme/"
                        "kaldırma veya ona aykırı davranma iddiası taşıyor."
                    ),
                    law_id=law_id,
                    article_id=actual_article,
                )
            )
        if is_repealed_text(content):
            findings.append(
                AuditFinding(
                    severity="medium",
                    code="CITATION_REPEALED",
                    message=f"Atıf yapılan {law_id}/{actual_article} mülga görünüyor.",
                    law_id=law_id,
                    article_id=actual_article,
                )
            )
        if metadata.type == "anayasa" and _has_conflict_language(subject_text):
            findings.append(
                AuditFinding(
                    severity="high",
                    code="CONFLICT_WITH_CONSTITUTION",
                    message="Metin Anayasa'ya aykırılık/üstünlük iddiası içeriyor.",
                    law_id=law_id,
                    article_id=actual_article,
                )
            )

    if not citations:
        findings.append(
            AuditFinding(
                severity="low",
                code="NO_NORM_CITATION",
                message="Metinde doğrulanabilir bir mevzuat atfı bulunamadı.",
            )
        )

    unique: list[AuditFinding] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for finding in findings:
        key = (finding.code, finding.message, finding.law_id, finding.article_id)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return UltraViresReport(
        as_of_date=as_of_date,
        snapshot_commit=snapshot,
        subject_level=subject_level,
        findings=unique,
        sources=sources,
    )
