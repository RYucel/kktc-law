"""TurkLex Sentinel: deterministik hibrit hukuk ajanı orkestratörü.

LLM çağrısı yapmaz. Zaman damgalı Git snapshot'ından exact/semantic kaynakları toplar,
Ultra Vires denetimini çalıştırır ve dış LLM'e verilecek sıfır-halüsinasyon bağlam
paketini üretir.
"""

from __future__ import annotations

from typing import Any

from tools.git_engine import DeterministicGitEngine
from tools.models import SemanticHit
from tools.semantic_index import SemanticIndex
from tools.ultra_vires import audit_norm, extract_norm_citations, norm_level

__all__ = [
    "HybridRetriever",
    "audit_contract_clause",
    "build_audit_prompt",
    "semantic_search",
]


class HybridRetriever:
    """Exact Git snapshot + yerel FTS5/BM25 hibrit kaynak getirici."""

    def __init__(self, engine: DeterministicGitEngine):
        self.engine = engine

    def _source(
        self,
        law_id: str,
        article_id: str,
        *,
        retrieval: str,
        score: float | None = None,
        event_date: str | None = None,
    ) -> dict[str, Any] | None:
        metadata = self.engine.get_law_metadata(law_id, event_date)
        content = self.engine.read_article_as_of(law_id, article_id, event_date)
        if metadata is None or content is None:
            return None
        commit = self.engine.resolve_commit_as_of(event_date)
        if commit is None:
            return None
        return {
            "law_id": law_id,
            "article_id": article_id,
            "law_title": metadata.title,
            "norm_level": norm_level(metadata.type),
            "content": content,
            "commit_sha": commit,
            "effective_date": self.engine.effective_date(commit),
            "retrieval": retrieval,
            "score": score,
        }

    def search(
        self,
        query: str,
        event_date: str,
        *,
        law_id: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Exact atıfları ve semantik eşleşmeleri tek deterministik listede birleştirir."""
        sources: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for cited_law, cited_article in extract_norm_citations(query):
            article = cited_article or "madde-001"
            source = self._source(
                cited_law,
                article,
                retrieval="exact",
                event_date=event_date,
            )
            if source is None:
                continue
            key = (cited_law, article)
            if key not in seen:
                seen.add(key)
                sources.append(source)

        with SemanticIndex.from_engine(self.engine, event_date) as index:
            hits = index.search(query, law_id=law_id, limit=limit)
        for hit in hits:
            key = (hit.law_id, hit.article_id)
            if key in seen:
                continue
            source = self._source(
                hit.law_id,
                hit.article_id,
                retrieval="semantic",
                score=hit.score,
                event_date=event_date,
            )
            if source is None:
                continue
            seen.add(key)
            sources.append(source)
        return sources


def semantic_search(
    engine: DeterministicGitEngine,
    query: str,
    as_of_date: str | None = None,
    *,
    law_id: str | None = None,
    limit: int = 10,
) -> list[SemanticHit]:
    """MCP/CLI için FTS5/BM25 araması."""
    with SemanticIndex.from_engine(engine, as_of_date) as index:
        return index.search(query, law_id=law_id, limit=limit)


def build_audit_prompt(
    clause_text: str,
    event_date: str,
    snapshot_commit: str,
    sources: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> str:
    """LLM'e verilecek deterministik denetim prompt'unu üretir."""
    lines = [
        "TurkLex Sentinel — Ultra Vires Denetim Bağlamı",
        f"Olay tarihi: {event_date}",
        f"Git snapshot commit: {snapshot_commit}",
        f"Denetlenen hüküm: {clause_text}",
        "",
        "Normlar hiyerarşisi: Anayasa (1) > Fasıl/Yasa (2) > Tüzük/ikincil (3)",
        "",
        "Doğrulanmış kaynaklar:",
    ]
    if not sources:
        lines.append("- Kaynak bulunamadı.")
    for index, source in enumerate(sources, 1):
        lines.extend(
            [
                f"[{index}] {source['law_id']}/{source['article_id']} "
                f"(seviye {source['norm_level']}, {source['retrieval']})",
                f"    Başlık: {source['law_title']}",
                f"    Commit: {source['commit_sha']} / Yürürlük: {source['effective_date']}",
                "    Metin:",
                source["content"].strip(),
                "",
            ]
        )
    lines.append("Deterministik Ultra Vires bulguları:")
    if not findings:
        lines.append("- Belirgin ihlal sinyali yok.")
    for finding in findings:
        lines.append(
            f"- [{finding['severity'].upper()}:{finding['code']}] {finding['message']}"
        )
    lines.extend(
        [
            "",
            "Görev: Yalnızca yukarıdaki kaynaklara dayanarak hükmün Anayasa ve yürürlükteki",
            "mevzuata uygunluğunu değerlendir. Kaynakta olmayan bir hüküm uydurma; her",
            "sonucu law_id/article_id ve commit_sha ile gerekçelendir. Ultra Vires riski",
            "varsa açıkça belirt; yoksa gerekçeli olarak 'tespit edilmedi' yaz.",
        ]
    )
    return "\n".join(lines)


def audit_contract_clause(
    clause_text: str,
    event_date: str,
    engine: DeterministicGitEngine,
    *,
    subject_level: str = "secondary",
    law_id: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Bir sözleşme hükmünü tarihsel snapshot ve Ultra Vires denetimiyle paketler."""
    report = audit_norm(
        clause_text,
        event_date,
        subject_level=subject_level,
        engine=engine,
    )
    retriever = HybridRetriever(engine)
    sources = retriever.search(clause_text, event_date, law_id=law_id, limit=limit)

    source_keys = {(source["law_id"], source["article_id"]) for source in sources}
    for audit_source in report.sources:
        key = (audit_source.law_id, audit_source.article_id)
        if key not in source_keys:
            sources.append(
                {
                    "law_id": audit_source.law_id,
                    "article_id": audit_source.article_id,
                    "law_title": audit_source.title,
                    "norm_level": audit_source.norm_level,
                    "content": audit_source.content,
                    "commit_sha": audit_source.commit_sha,
                    "effective_date": audit_source.effective_date,
                    "retrieval": "exact",
                    "score": None,
                }
            )
            source_keys.add(key)

    findings = [finding.model_dump(mode="json") for finding in report.findings]
    prompt = build_audit_prompt(
        clause_text,
        event_date,
        report.snapshot_commit,
        sources,
        findings,
    )
    return {
        "event_date": event_date,
        "snapshot_commit": report.snapshot_commit,
        "subject_level": subject_level,
        "sources": sources,
        "findings": findings,
        "audit_prompt": prompt,
        "deterministic": True,
    }
