"""Değişiklik yasası metinlerindeki standart maddeleri gerçek yamalara dönüştürür.

Modül, değişiklik yasalarında geçen üç temel kalıbı destekler:

* maddiyi yeni metinle değiştirme,
* fıkra içindeki bir ibareyi değiştirme,
* maddiyi yürürlükten kaldırma.

Yamalar `ArticlePatch` modeline dönüştürülür ve `build_chronological_commits`
tarafından ilgili yürürlük tarihinde uygulanır.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from tools.models import ArticlePatch, ParsedArticle, normalize_article_id

__all__ = [
    "apply_article_patches",
    "load_article_patches",
    "parse_amendment_instructions",
]

_ARTICLE_NUM = r"(?P<num>\d{1,3})(?:\s*['’]?\s*[a-zçğıöşü]{1,8})?"
_QUOTE = r"['\"“”‘’]"

_REPLACE_ARTICLE_RE = re.compile(
    rf"esas\s+yasan[ıi]n\s+{_ARTICLE_NUM}\s+maddesi\s+kald[ıi]r[ıi]larak\s+yerine\s+"
    rf"(?:aşağıdaki\s+)?yeni\s+(?P=num)(?:\s*['’]?\s*[a-zçğıöşü]{{1,8}})?\s+madde\s+konur\s*:?\s*"
    rf"(?P<content>.*?)(?=\bEsas\s+Yasan[ıi]n\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_REPLACE_TEXT_RE = re.compile(
    rf"esas\s+yasan[ıi]n\s+{_ARTICLE_NUM}\s+maddesinin\s+"
    rf"\((?P<clause>[^)]+)\)\s+fıkrasında\s+yer\s+alan\s+"
    rf"{_QUOTE}(?P<old>.*?){_QUOTE}\s+ibaresi\s+ç[ıi]kar[ıi]larak\s+yerine\s+"
    rf"{_QUOTE}(?P<new>.*?){_QUOTE}\s+ibaresi\s+konur",
    re.IGNORECASE | re.DOTALL,
)
_REPEAL_ARTICLE_RE = re.compile(
    rf"esas\s+yasan[ıi]n\s+{_ARTICLE_NUM}\s+maddesi\s+yürürlükten\s+"
    r"kald[ıi]r[ıi]l[ıi]r",
    re.IGNORECASE,
)


def _clean_content(value: str) -> str:
    return value.strip().strip(":").strip()


def parse_amendment_instructions(amendment_law_text: str) -> list[ArticlePatch]:
    """Değişiklik yasası metninden `ArticlePatch` listesini çıkarır."""
    if not isinstance(amendment_law_text, str):
        raise TypeError("Değişiklik yasası metni str olmalıdır")
    text = amendment_law_text.replace("\r\n", "\n").replace("\r", "\n")
    patches: list[tuple[int, ArticlePatch]] = []

    for match in _REPLACE_ARTICLE_RE.finditer(text):
        patches.append(
            (
                match.start(),
                ArticlePatch(
                    action="replace_article",
                    target_article=match.group("num"),
                    new_content=_clean_content(match.group("content")),
                ),
            )
        )
    for match in _REPLACE_TEXT_RE.finditer(text):
        patches.append(
            (
                match.start(),
                ArticlePatch(
                    action="replace_text",
                    target_article=match.group("num"),
                    clause=match.group("clause").strip(),
                    old_text=match.group("old").strip(),
                    new_text=match.group("new").strip(),
                ),
            )
        )
    for match in _REPEAL_ARTICLE_RE.finditer(text):
        patches.append(
            (
                match.start(),
                ArticlePatch(
                    action="repeal_article",
                    target_article=match.group("num"),
                ),
            )
        )

    patches.sort(key=lambda item: item[0])
    return [patch for _, patch in patches]


def apply_article_patches(
    articles: Sequence[ParsedArticle],
    patches: Sequence[ArticlePatch],
) -> list[ParsedArticle]:
    """Yamaları maddelere uygular ve yeni `ParsedArticle` listesini döndürür."""
    current = {article.article_id: article for article in articles}
    for patch in patches:
        target = normalize_article_id(patch.target_article)
        article = current.get(target)
        if article is None:
            raise ValueError(f"Yama hedefi bulunamadı: {target}")
        if patch.action == "replace_article":
            current[target] = ParsedArticle(
                article_id=target,
                title=article.title,
                body=patch.new_content,
                amendments=article.amendments,
                is_repealed=False,
            )
        elif patch.action == "replace_text":
            if patch.old_text not in article.body:
                raise ValueError(
                    f"{target} içinde değiştirilecek ibare bulunamadı: {patch.old_text!r}"
                )
            current[target] = article.model_copy(
                update={"body": article.body.replace(patch.old_text, patch.new_text, 1)}
            )
        elif patch.action == "repeal_article":
            body = article.body.rstrip()
            marker = "[Bu madde yürürlükten kaldırılmıştır.]"
            if marker not in body:
                body = f"{body}\n\n{marker}" if body else marker
            current[target] = article.model_copy(
                update={"body": body, "is_repealed": True}
            )
    return [current[article.article_id] for article in articles]


def load_article_patches(path: str | Path) -> list[ArticlePatch]:
    """`data/amendment_patches.json` biçimindeki yama listesini yükler."""
    source = Path(path)
    if not source.is_file():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Yama dosyası bir JSON listesi olmalıdır: {source}")
    return [ArticlePatch.model_validate(item) for item in payload]
