"""Külliyat istatistikleri: planlardan veya Git deposundan deterministik özet."""

from __future__ import annotations

import re
from collections.abc import Sequence

from tools.git_engine import DeterministicGitEngine, is_repealed_text
from tools.models import (
    ArticleAmendmentStat,
    CorpusStatistics,
    DateSource,
    LawAmendmentStat,
    LawHeader,
    LawType,
    ParsedLaw,
)

__all__ = [
    "render_stats_markdown",
    "statistics_from_engine",
    "statistics_from_parsed_laws",
]

_LAW_TYPE_KEYS: tuple[LawType, ...] = ("anayasa", "fasil", "yasa")
_DATE_SOURCE_KEYS: tuple[DateSource, ...] = (
    "explicit",
    "override",
    "fallback_1959",
    "fallback_law_year",
)
_ARTICLE_FILE_RE = re.compile(r"^laws/(?P<law_id>[^/]+)/(?P<article_id>[^/]+)\.md$")
_HEADING_RE = re.compile(r"^#\s+(?:Geçici\s+)?Madde\s+\d+(?::\s*(?P<title>.+))?$")


def _zero_type_counts() -> dict[str, int]:
    return {key: 0 for key in _LAW_TYPE_KEYS}


def _zero_source_counts() -> dict[str, int]:
    return {key: 0 for key in _DATE_SOURCE_KEYS}


def _article_title(article_id: str, content: str) -> str:
    for line in content.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match is not None:
            return (match.group("title") or "").strip() or article_id
    return article_id


def _sort_top_laws(items: list[LawAmendmentStat]) -> list[LawAmendmentStat]:
    return sorted(items, key=lambda item: (-item.amendment_count, item.law_id))[:10]


def _sort_top_articles(items: list[ArticleAmendmentStat]) -> list[ArticleAmendmentStat]:
    return sorted(
        items,
        key=lambda item: (-item.amendment_count, item.law_id, item.article_id),
    )[:10]


def statistics_from_parsed_laws(
    headers: Sequence[LawHeader],
    laws: Sequence[ParsedLaw],
    commit_dates: Sequence[str],
) -> CorpusStatistics:
    """İnşa planlarından, Git deposu oluşmadan önce istatistik üretir."""
    law_type_counts = _zero_type_counts()
    source_counts = _zero_source_counts()
    for header in headers:
        law_type_counts[header.type] += 1
        source_counts[header.date_source] += 1

    total_articles = 0
    active_articles = 0
    repealed_articles = 0
    law_amendments: list[LawAmendmentStat] = []
    article_amendments: list[ArticleAmendmentStat] = []
    for law in laws:
        law_count = 0
        for article in law.articles:
            total_articles += 1
            if article.is_repealed:
                repealed_articles += 1
            else:
                active_articles += 1
            count = len(article.amendments)
            law_count += count
            if count:
                article_amendments.append(
                    ArticleAmendmentStat(
                        law_id=law.header.law_id,
                        article_id=article.article_id,
                        title=article.title or article.article_id,
                        amendment_count=count,
                    )
                )
        law_amendments.append(
            LawAmendmentStat(
                law_id=law.header.law_id,
                title=law.header.title or law.header.law_id,
                amendment_count=law_count,
            )
        )

    dates = [item for item in commit_dates if item]
    return CorpusStatistics(
        total_laws=len(headers),
        law_type_counts=law_type_counts,
        total_articles=total_articles,
        active_articles=active_articles,
        repealed_articles=repealed_articles,
        total_commits=len(commit_dates),
        min_effective_date=min(dates) if dates else None,
        max_effective_date=max(dates) if dates else None,
        date_source_counts=source_counts,
        top_laws=_sort_top_laws([item for item in law_amendments if item.amendment_count]),
        top_articles=_sort_top_articles(article_amendments),
    )


def statistics_from_engine(engine: DeterministicGitEngine) -> CorpusStatistics:
    """Mevcut Git deposundan salt-okunur istatistik üretir."""
    if not engine.has_commits():
        return CorpusStatistics(
            total_laws=0,
            law_type_counts=_zero_type_counts(),
            total_articles=0,
            active_articles=0,
            repealed_articles=0,
            total_commits=0,
            date_source_counts=_zero_source_counts(),
        )

    metadata_list = engine.list_laws_as_of(None)
    law_type_counts = _zero_type_counts()
    source_counts = _zero_source_counts()
    law_amendments: list[LawAmendmentStat] = []
    article_counts: dict[tuple[str, str], int] = {}
    article_titles: dict[tuple[str, str], str] = {}

    metadata_by_id = {metadata.law_id: metadata for metadata in metadata_list}
    for metadata in metadata_list:
        law_type_counts[metadata.type] += 1
        source_counts[metadata.date_source] += 1
        law_amendments.append(
            LawAmendmentStat(
                law_id=metadata.law_id,
                title=metadata.title,
                amendment_count=len(metadata.consolidation_history),
            )
        )
        for record in metadata.consolidation_history:
            for article_id in record.affected_articles:
                key = (metadata.law_id, article_id)
                article_counts[key] = article_counts.get(key, 0) + 1

    total_articles = 0
    active_articles = 0
    repealed_articles = 0
    head = engine.head()
    for path in engine.list_files_at_commit(head):
        match = _ARTICLE_FILE_RE.match(path)
        if match is None:
            continue
        law_id = match.group("law_id")
        article_id = match.group("article_id")
        content = engine.read_file_at_commit(path, head)
        if content is None:
            continue
        total_articles += 1
        if is_repealed_text(content):
            repealed_articles += 1
        else:
            active_articles += 1
        key = (law_id, article_id)
        article_titles[key] = _article_title(article_id, content)

    commits = engine.list_commits()
    dates = [engine.effective_date(commit) for commit in commits]
    article_amendments = [
        ArticleAmendmentStat(
            law_id=law_id,
            article_id=article_id,
            title=article_titles.get((law_id, article_id), article_id),
            amendment_count=count,
        )
        for (law_id, article_id), count in article_counts.items()
        if count
    ]
    # Yalnızca gerçekten dosyası bulunan maddeleri üst listeye al.
    article_amendments = [
        item for item in article_amendments if (item.law_id, item.article_id) in article_titles
    ]

    return CorpusStatistics(
        total_laws=len(metadata_by_id),
        law_type_counts=law_type_counts,
        total_articles=total_articles,
        active_articles=active_articles,
        repealed_articles=repealed_articles,
        total_commits=len(commits),
        min_effective_date=min(dates) if dates else None,
        max_effective_date=max(dates) if dates else None,
        date_source_counts=source_counts,
        top_laws=_sort_top_laws([item for item in law_amendments if item.amendment_count]),
        top_articles=_sort_top_articles(article_amendments),
    )


def render_stats_markdown(stats: CorpusStatistics) -> str:
    """İstatistikleri deterministik `STATS.md` metnine dönüştürür."""
    lines = [
        "# KKTC Mevzuat Külliyatı — İstatistikler",
        "",
        "## Genel",
        "",
        "| Metrik | Değer |",
        "| --- | ---: |",
        f"| Toplam yasa | {stats.total_laws} |",
        f"| Toplam madde | {stats.total_articles} |",
        f"| Aktif madde | {stats.active_articles} |",
        f"| Mülga/kaldırılmış madde | {stats.repealed_articles} |",
        f"| Tarihsel commit | {stats.total_commits} |",
        f"| İlk yürürlük tarihi | {stats.min_effective_date or '-'} |",
        f"| Son yürürlük tarihi | {stats.max_effective_date or '-'} |",
        "",
        "## Yasa Türleri",
        "",
        "| Tür | Yasa |",
        "| --- | ---: |",
    ]
    for key in _LAW_TYPE_KEYS:
        lines.append(f"| {key} | {stats.law_type_counts.get(key, 0)} |")
    lines.extend(
        [
            "",
            "## Tarih Kaynakları",
            "",
            "| Kaynak | Yasa |",
            "| --- | ---: |",
        ]
    )
    for key in _DATE_SOURCE_KEYS:
        lines.append(f"| {key} | {stats.date_source_counts.get(key, 0)} |")
    lines.extend(
        [
            "",
            "## En Çok Değişiklik Geçiren 10 Yasa",
            "",
            "| # | Yasa | Başlık | Değişiklik |",
            "| ---: | --- | --- | ---: |",
        ]
    )
    if stats.top_laws:
        for index, item in enumerate(stats.top_laws, 1):
            lines.append(
                f"| {index} | {item.law_id} | {item.title} | {item.amendment_count} |"
            )
    else:
        lines.append("| - | - | Kayıt yok | 0 |")
    lines.extend(
        [
            "",
            "## En Çok Değişiklik Geçiren 10 Madde",
            "",
            "| # | Yasa | Madde | Başlık | Değişiklik |",
            "| ---: | --- | --- | --- | ---: |",
        ]
    )
    if stats.top_articles:
        for index, item in enumerate(stats.top_articles, 1):
            lines.append(
                f"| {index} | {item.law_id} | {item.article_id} | "
                f"{item.title} | {item.amendment_count} |"
            )
    else:
        lines.append("| - | - | - | Kayıt yok | 0 |")
    return "\n".join(lines) + "\n"
