"""Tarihsel commit günlüğü ve iki tarih arasındaki külliyat değişiklikleri."""

from __future__ import annotations

from tools.git_engine import DeterministicGitEngine
from tools.models import CommitLogEntry, CorpusChange, validate_iso_date

__all__ = ["collect_changes", "collect_commit_log"]

_STATUS_MAP = {
    "added": "eklendi",
    "modified": "güncellendi",
    "deleted": "silindi",
}


def collect_commit_log(
    engine: DeterministicGitEngine,
    *,
    law_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> list[CommitLogEntry]:
    """Commit'leri yürürlük tarihi ve isteğe bağlı yasa filtresiyle listeler."""
    if limit < 1:
        raise ValueError("limit en az 1 olmalıdır")
    start = validate_iso_date(since) if since else None
    end = validate_iso_date(until) if until else None
    entries: list[CommitLogEntry] = []
    for sha in engine.list_commits():
        effective = engine.effective_date(sha)
        if start is not None and effective < start:
            continue
        if end is not None and effective > end:
            continue
        subject = engine.commit_subject(sha)
        subject_law = subject.split(":", 1)[0].strip()
        if law_id is not None and subject_law != law_id:
            continue
        entries.append(
            CommitLogEntry(
                commit_sha=sha,
                effective_date=effective,
                law_id=subject_law,
                subject=subject,
                author_name=engine.commit_author(sha),
            )
        )
        if len(entries) >= limit:
            break
    return entries


def collect_changes(
    engine: DeterministicGitEngine,
    *,
    since: str,
    until: str | None = None,
    law_id: str | None = None,
) -> list[CorpusChange]:
    """İki tarih arasındaki madde/metadata dosya değişikliklerini döndürür."""
    since_date = validate_iso_date(since)
    until_date = validate_iso_date(until) if until else None
    from_sha = engine.resolve_commit_as_of(since_date)
    to_sha = engine.resolve_commit_as_of(until_date) if until_date else engine.head()
    if from_sha is None:
        raise ValueError(f"{since_date} tarihinde yürürlükte bir commit yok")
    if from_sha == to_sha:
        return []

    changes: list[CorpusChange] = []
    metadata_laws: set[str] = set()
    for change in engine.changed_files_between(from_sha, to_sha):
        parts = change.path.split("/")
        if len(parts) != 3 or parts[0] != "laws":
            continue
        changed_law, file_name = parts[1], parts[2]
        if law_id is not None and changed_law != law_id:
            continue
        article_id: str | None = None
        if file_name == "metadata.json":
            metadata_laws.add(changed_law)
        elif file_name.endswith(".md"):
            article_id = file_name[:-3]
        else:
            continue
        changes.append(
            CorpusChange(
                law_id=changed_law,
                article_id=article_id,
                status=_STATUS_MAP[change.status],  # type: ignore[index]
                path=change.path,
            )
        )

    # Hibrit replay'de makale metni değişmeyip yalnızca metadata.json değişebilir.
    # Bu durumda metadata geçmişindeki etkilenen maddeleri mantıksal değişiklik olarak ekle.
    existing = {(item.law_id, item.article_id) for item in changes}
    end_date = until_date or engine.effective_date(to_sha)
    for changed_law in sorted(metadata_laws):
        metadata = engine.get_law_metadata(changed_law, until_date)
        if metadata is None:
            continue
        for record in metadata.consolidation_history:
            if not (since_date <= record.effective_date <= end_date):
                continue
            for article_id in record.affected_articles:
                key = (changed_law, article_id)
                if key in existing:
                    continue
                existing.add(key)
                changes.append(
                    CorpusChange(
                        law_id=changed_law,
                        article_id=article_id,
                        status="güncellendi",
                        path=f"laws/{changed_law}/{article_id}.md",
                    )
                )

    return sorted(
        changes,
        key=lambda item: (item.law_id, item.article_id or "", item.status),
    )
