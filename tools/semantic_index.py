"""Deterministik yerel semantik/lexical indeks (SQLite FTS5 + BM25).

Dış vektör servisi gerektirmeden, Git snapshot'ındaki maddeleri FTS5 sanal tablosuna
yükler. Aynı depo ve aynı `as_of_date` için aynı sıralamayı üretir. Chroma/pgvector
gibi vektör arka uçları için `SemanticIndex` arayüzü adapte edilebilir.
"""

from __future__ import annotations

import re
import sqlite3

from tools.git_engine import DeterministicGitEngine
from tools.models import LawMetadata, SemanticHit, normalize_article_id

__all__ = ["SemanticIndex"]

_ARTICLE_FILE_RE = re.compile(r"^laws/(?P<law_id>[^/]+)/(?P<article_id>[^/]+)\.md$")
_HEADING_RE = re.compile(r"^#\s+(?:Geçici\s+)?Madde\s+\d+(?::\s*(?P<title>.+))?$")
_TOKEN_RE = re.compile(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]+")


def _tokenize(value: str) -> list[str]:
    folded = value.replace("İ", "i").replace("I", "ı").casefold()
    return [token for token in _TOKEN_RE.findall(folded) if len(token) > 1]


def _article_title(article_id: str, content: str) -> str:
    for line in content.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match is not None:
            return (match.group("title") or "").strip() or article_id
    return article_id


class SemanticIndex:
    """Git snapshot'ından kurulan yerel FTS5/BM25 indeksi."""

    def __init__(self, connection: sqlite3.Connection, commit_sha: str, effective_date: str):
        self.connection = connection
        self.commit_sha = commit_sha
        self.effective_date = effective_date
        self.connection.execute(
            """
            CREATE VIRTUAL TABLE articles USING fts5(
                law_id UNINDEXED,
                article_id UNINDEXED,
                title,
                content,
                commit_sha UNINDEXED,
                effective_date UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 0'
            )
            """
        )

    @classmethod
    def from_engine(
        cls,
        engine: DeterministicGitEngine,
        as_of_date: str | None = None,
    ) -> SemanticIndex:
        """Belirtilen tarihteki Git snapshot'ından indeks kurar."""
        commit_sha = engine.resolve_commit_as_of(as_of_date)
        effective_date = engine.effective_date(commit_sha) if commit_sha else ""
        index = cls(sqlite3.connect(":memory:"), commit_sha or "", effective_date)
        if commit_sha is None:
            return index

        metadata_by_law: dict[str, LawMetadata] = {
            metadata.law_id: metadata for metadata in engine.list_laws_as_of(as_of_date)
        }
        rows: list[tuple[str, str, str, str, str, str]] = []
        for path in engine.list_files_at_commit(commit_sha):
            match = _ARTICLE_FILE_RE.match(path)
            if match is None:
                continue
            law_id = match.group("law_id")
            article_id = match.group("article_id")
            content = engine.read_file_at_commit(path, commit_sha)
            if content is None:
                continue
            metadata = metadata_by_law.get(law_id)
            law_title = metadata.title if metadata is not None else law_id
            title = _article_title(article_id, content)
            rows.append(
                (
                    law_id,
                    article_id,
                    f"{law_title} {title}".strip(),
                    content,
                    commit_sha,
                    effective_date,
                )
            )
        index.connection.executemany(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        index.connection.commit()
        return index

    def search(
        self,
        query: str,
        *,
        law_id: str | None = None,
        limit: int = 10,
    ) -> list[SemanticHit]:
        """Sorguyu FTS5 BM25 ile arar ve commit ile tarih damgalı sonuç döndürür."""
        if limit < 1:
            raise ValueError("limit en az 1 olmalıdır")
        tokens = _tokenize(query)
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        sql = (
            "SELECT law_id, article_id, title, "
            "snippet(articles, 3, '[', ']', ' … ', 16), "
            "bm25(articles) AS rank, commit_sha, effective_date "
            "FROM articles WHERE articles MATCH ?"
        )
        parameters: list[object] = [fts_query]
        if law_id is not None:
            sql += " AND law_id = ?"
            parameters.append(law_id)
        sql += " ORDER BY rank LIMIT ?"
        parameters.append(limit)
        rows = self.connection.execute(sql, parameters).fetchall()
        return [
            SemanticHit(
                law_id=row[0],
                article_id=normalize_article_id(row[1]),
                title=row[2],
                snippet=row[3] or "",
                score=round(-float(row[4]), 6),
                commit_sha=row[5],
                effective_date=row[6],
            )
            for row in rows
        ]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SemanticIndex:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
