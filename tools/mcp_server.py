"""FastMCP sunucusu: KKTC mevzuatı için salt-okunur tarihsel sorgu araçları.

Sunucu, hedef külliyat reposunu `KKTC_LAW_REPO_PATH` ortam değişkeninden okur.
`python -m tools.mcp_server --repo-path <dizin>` ile de başlatılabilir. Tüm araçlar
Git nesne veritabanından okur; `git checkout` kullanmaz ve working tree'yi değiştirmez.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from tools.git_engine import DeterministicGitEngine, is_repealed_text
from tools.models import normalize_article_id
from tools.sentinel import audit_contract_clause as _sentinel_audit
from tools.sentinel import semantic_search as _semantic_search
from tools.stats import statistics_from_engine
from tools.ultra_vires import audit_norm as _audit_norm

mcp = FastMCP("kktc-law")


def _repo_path() -> Path:
    return Path(os.environ.get("KKTC_LAW_REPO_PATH", "corpus_repo")).expanduser()


def _engine() -> DeterministicGitEngine:
    return DeterministicGitEngine(_repo_path(), initialize=False)


@mcp.tool()
def list_laws(as_of_date: str | None = None) -> list[dict[str, Any]]:
    """Belirtilen tarihte yürürlükte olan yasaları listeler.

    Args:
        as_of_date: `YYYY-MM-DD`; `None` ise `HEAD` kullanılır.

    Returns:
        `laws/*/metadata.json` kayıtlarının `law_id` sıralı sözlük listesi.
    """
    return [
        metadata.model_dump(mode="json")
        for metadata in _engine().list_laws_as_of(as_of_date)
    ]


@mcp.tool()
def get_law_article(
    law_id: str,
    article_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Bir maddenin belirli tarihteki metnini ve kaynak commit bilgisini döndürür.

    Args:
        law_id: Yasa kimliği (örn. `fasil-113`).
        article_id: `madde-002`, `2` veya `gecici-1` gibi madde kimliği.
        as_of_date: `YYYY-MM-DD`; `None` ise `HEAD`.

    Returns:
        `law_id`, `article_id`, `law_title`, `content`, `commit_sha`,
        `effective_date`, `as_of_date` ve `repealed` alanlarını taşıyan sözlük.
    """
    engine = _engine()
    normalized = normalize_article_id(article_id)
    sha = engine.resolve_commit_as_of(as_of_date)
    if sha is None:
        raise ValueError(
            f"{as_of_date or 'HEAD'} itibarıyla yürürlükte bir commit bulunamadı"
        )
    content = engine.read_article_as_of(law_id, normalized, as_of_date)
    if content is None:
        raise ValueError(
            f"{law_id}/{normalized}, {as_of_date or 'HEAD'} itibarıyla bulunamadı"
        )
    metadata = engine.get_law_metadata(law_id, as_of_date)
    return {
        "law_id": law_id,
        "article_id": normalized,
        "law_title": metadata.title if metadata is not None else law_id,
        "content": content,
        "commit_sha": sha,
        "effective_date": engine.effective_date(sha),
        "as_of_date": as_of_date,
        "repealed": is_repealed_text(content),
    }


@mcp.tool()
def get_article_amendment_history(
    law_id: str,
    article_id: str,
) -> list[dict[str, Any]]:
    """Bir maddenin tüm değişiklik geçmişini kronolojik olarak döndürür.

    Args:
        law_id: Yasa kimliği (örn. `fasil-113`).
        article_id: Madde kimliği (örn. `madde-004` veya `4`).

    Returns:
        `commit_sha`, `effective_date`, `author_name`, `subject`, `status`
        (`eklendi`/`güncellendi`/`silindi`) alanlarını taşıyan sözlükler.
    """
    return _engine().get_article_history(law_id, article_id)


@mcp.tool()
def compare_article_versions(
    law_id: str,
    article_id: str,
    from_date: str,
    to_date: str,
) -> dict[str, Any]:
    """İki tarih arasındaki madde sürümlerini karşılaştırır.

    Args:
        law_id: Yasa kimliği (örn. `fasil-113`).
        article_id: Madde kimliği (örn. `madde-004` veya `4`).
        from_date: Başlangıç tarihi (`YYYY-MM-DD`).
        to_date: Bitiş tarihi (`YYYY-MM-DD`).

    Returns:
        Çözümlenen commit'ler, yürürlük tarihleri, `changed` bayrağı ve unified
        `diff` metnini taşıyan sözlük.
    """
    engine = _engine()
    normalized = normalize_article_id(article_id)
    from_sha = engine.resolve_commit_as_of(from_date)
    to_sha = engine.resolve_commit_as_of(to_date)
    from_content = engine.read_article_as_of(law_id, normalized, from_date)
    to_content = engine.read_article_as_of(law_id, normalized, to_date)
    return {
        "law_id": law_id,
        "article_id": normalized,
        "from_date": from_date,
        "to_date": to_date,
        "from_commit": from_sha,
        "to_commit": to_sha,
        "from_effective_date": engine.effective_date(from_sha) if from_sha else None,
        "to_effective_date": engine.effective_date(to_sha) if to_sha else None,
        "changed": from_content != to_content,
        "diff": engine.diff_article(law_id, normalized, from_date, to_date),
    }


@mcp.tool()
def search_legislation(
    query: str,
    as_of_date: str | None = None,
    law_id: str | None = None,
) -> list[dict[str, Any]]:
    """Mevzuat içinde tarih damgalı tam metin araması yapar.

    Args:
        query: Aranacak sabit metin (büyük/küçük harf duyarsız).
        as_of_date: `YYYY-MM-DD`; `None` ise `HEAD`.
        law_id: Verilirse yalnızca bu yasa içinde arar.

    Returns:
        `law_id`, `article_id`, `line_number`, `snippet` alanlarını taşıyan
        eşleşme sözlükleri; deterministik sıralı.
    """
    return [
        hit.model_dump(mode="json")
        for hit in _engine().search_legislation(query, as_of_date, law_id)
    ]


@mcp.tool()
def get_corpus_statistics() -> dict[str, Any]:
    """Külliyatın yasa, madde, commit ve tarih kaynağı istatistiklerini döndürür.

    Returns:
        `CorpusStatistics` modelinin JSON uyumlu sözlüğü.
    """
    return statistics_from_engine(_engine()).model_dump(mode="json")


@mcp.tool()
def semantic_search(
    query: str,
    as_of_date: str | None = None,
    law_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Yerel FTS5/BM25 indeksinde tarihsel semantik arama yapar.

    Args:
        query: Doğal dil veya anahtar kelime sorgusu.
        as_of_date: `YYYY-MM-DD`; `None` ise `HEAD`.
        law_id: İsteğe bağlı yasa filtresi.
        limit: En fazla sonuç sayısı.

    Returns:
        `SemanticHit` sözlükleri; her sonuç commit SHA ve yürürlük tarihi taşır.
    """
    return [
        hit.model_dump(mode="json")
        for hit in _semantic_search(
            _engine(), query, as_of_date, law_id=law_id, limit=limit
        )
    ]


@mcp.tool()
def audit_norm(
    subject_text: str,
    as_of_date: str,
    subject_level: str = "secondary",
) -> dict[str, Any]:
    """Bir hükmü tarihsel snapshot'taki normlara karşı Ultra Vires denetler.

    Args:
        subject_text: Denetlenecek hüküm veya idari işlem metni.
        as_of_date: Olay tarihi (`YYYY-MM-DD`).
        subject_level: `secondary`, `yasa` veya `anayasa`.

    Returns:
        `UltraViresReport` sözlüğü: snapshot commit, kaynaklar ve bulgular.
    """
    return _audit_norm(
        subject_text,
        as_of_date,
        subject_level=subject_level,
        engine=_engine(),
    ).model_dump(mode="json")


@mcp.tool()
def turklex_audit(
    clause_text: str,
    event_date: str,
    subject_level: str = "secondary",
) -> dict[str, Any]:
    """Hibrit Git+FTS5 kaynaklarını toplayıp LLM'e hazır denetim paketi üretir.

    Args:
        clause_text: Denetlenecek sözleşme hükmü/idari metin.
        event_date: Olayın meydana geldiği tarih (`YYYY-MM-DD`).
        subject_level: Denetlenen metnin norm seviyesi.

    Returns:
        Kaynaklar, deterministik Ultra Vires bulguları, snapshot commit ve audit prompt.
    """
    return _sentinel_audit(
        clause_text,
        event_date,
        _engine(),
        subject_level=subject_level,
    )


def main() -> None:
    """MCP sunucusunu stdio taşımasıyla başlatır."""
    parser = argparse.ArgumentParser(
        description="KKTC mevzuatı FastMCP sunucusu",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Külliyat Git deposu (KKTC_LAW_REPO_PATH ortam değişkenini geçersiz kılar).",
    )
    args = parser.parse_args()
    if args.repo_path:
        os.environ["KKTC_LAW_REPO_PATH"] = str(
            Path(args.repo_path).expanduser().resolve()
        )
    mcp.run()


if __name__ == "__main__":
    main()
