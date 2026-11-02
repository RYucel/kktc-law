"""`mahkemeler.net` mevzuat fihristi için asenkron ve idempotent indirici.

Modül iki katmandan oluşur:

1. `parse_mahkemeler_index` — fihrist HTML'ini `<a>` bağlantıları üzerinden tarar,
   bağlantı metninden veya URL yapısından `fasil-113`, `yasa-2024-42` ya da `anayasa`
   kimliğini çıkarır ve sonucu deterministik olarak sıralar.
2. `MahkemelerScraper` — `httpx.AsyncClient` + `asyncio.Semaphore` ile sınırlı eşzamanlılık,
   `tenacity` ile üstel geri çekilme (exponential backoff) ve `raw_cache/manifest.json`
   üzerinden SHA-256 doğrulamalı önbellek kullanır. Önbellek geçerliyse ağa hiç çıkmaz.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tools.models import CachedDocument, CacheManifest, LawIndexEntry

__all__ = [
    "CACHE_MANIFEST_FILENAME",
    "DEFAULT_INDEX_URL",
    "MahkemelerScraper",
    "filter_entries",
    "load_cache_manifest",
    "parse_mahkemeler_index",
    "write_cache_manifest",
]

logger = logging.getLogger(__name__)

#: Önbellek kökünde tutulan manifest dosyasının adı.
CACHE_MANIFEST_FILENAME = "manifest.json"

#: Gerçek KKTC Mevzuat Bilgi Sistemi yasa fihristi. Eski `www.../mevzuat/` yolu 404
#: döndürdüğü için portalın oturum başlatan adresi kullanılır.
DEFAULT_INDEX_URL = "https://mevzuat.mahkemeler.net/web/index?BackLink=YasaFihrist"
ANAYASA_URL = "https://www.mahkemeler.net/wp-content/uploads/2024/08/anayasa-4.doc"
ANAYASA_TITLE = "Kuzey Kıbrıs Türk Cumhuriyeti Anayasası"
ANAYASA_ENTRY = LawIndexEntry(
    law_id="anayasa",
    title=ANAYASA_TITLE,
    url=ANAYASA_URL,
    category="anayasa",
    number=None,
)


USER_AGENT = "kktc-law/0.1 (deterministic corpus builder)"
_PORTAL_GRID_PATH = "/MEVZUAT/Yasa/LoadDataForGrid"
_INDEX_PAGE_HINTS = ("fihrist", "fasil", "yasa", "index")
_HTML_SUFFIXES = frozenset({"", ".htm", ".html", ".asp", ".aspx"})

_ASSET_SUFFIXES = frozenset(
    {
        ".css",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".png",
        ".svg",
        ".webp",
        ".zip",
    }
)

_META_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)", re.IGNORECASE)
_FASIL_TEXT_RE = re.compile(r"\bfas[ıi]l\s*(?:no\.?|numarası)?\s*[:\-]?\s*(\d{1,3})\b")
_SAYI_YIL_RE = re.compile(r"\b(\d{1,3})\s*/\s*(\d{4})\b")
_FASIL_URL_RE = re.compile(r"fas[ıi]llar?[-_/ ]*(\d{1,3})")
_FASIL_URL_REV_RE = re.compile(r"(\d{1,3})[-_]fas[ıi]l")
_YASA_URL_RE = re.compile(r"yasa[-_/ ]*(\d{4})[-_/ ]*(\d{1,3})")
_YASA_URL_REV_RE = re.compile(r"yasa[-_/ ]*(\d{1,3})[-_/ ]*(\d{4})")
_YEAR_PATH_RE = re.compile(r"/(\d{4})/(\d{1,3})(?:[./]|$)")
_NUM_YEAR_URL_RE = re.compile(r"/(\d{1,3})[-_](\d{4})(?:[./]|$)")

_SKIPPED_SCHEMES = ("#", "javascript:", "mailto:", "tel:")

_Classification = tuple[Literal["anayasa", "fasil", "yasa"], str | None, str | None]


def _fold(text: str) -> str:
    """Türkçe'ye duyarlı küçük harf biçimi (`İ`->i, `I`->ı)."""
    return unicodedata.normalize("NFC", text).replace("İ", "i").replace("I", "ı").casefold()


def _decode_html(value: str | bytes) -> str:
    """Bayt fihrist içeriğini BOM/meta charset/UTF-8 sırasıyla çözer."""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        raise TypeError(f"HTML metin veya byte olmalıdır, alınan: {type(value).__name__}")

    for bom, name in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if value.startswith(bom):
            return value.decode(name, errors="replace")

    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        pass

    candidates: list[str] = []
    match = _META_CHARSET_RE.search(value[:8192])
    if match is not None:
        candidates.append(match.group(1).decode("ascii", "ignore"))
    candidates.extend(["cp1254", "iso8859-9", "cp1252", "latin-1"])

    seen: set[str] = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            return value.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return value.decode("utf-8", errors="replace")


def _normalize_link_text(text: str) -> str:
    return " ".join(text.split()).strip(" -–—|")


def _law_from_text(text: str) -> _Classification | None:
    """Bağlantı metninden `(category, sayı, yıl)` üçlüsünü çıkarır."""
    folded = _fold(text)
    if "anayasa" in folded:
        return "anayasa", None, None
    fasil = _FASIL_TEXT_RE.search(folded)
    if fasil is not None:
        return "fasil", str(int(fasil.group(1))), None
    yasa = _SAYI_YIL_RE.search(folded)
    if yasa is not None:
        return "yasa", str(int(yasa.group(1))), yasa.group(2)
    return None


def _law_from_url(url: str) -> _Classification | None:
    """URL yolundan `(category, sayı, yıl)` üçlüsünü çıkarır."""
    path = _fold(unquote(urlparse(url).path))
    if "anayasa" in path:
        return "anayasa", None, None

    fasil = _FASIL_URL_RE.search(path)
    if fasil is None:
        fasil = _FASIL_URL_REV_RE.search(path)
    if fasil is not None:
        return "fasil", str(int(fasil.group(1))), None

    yasa = _YASA_URL_RE.search(path)
    if yasa is not None:
        return "yasa", str(int(yasa.group(2))), yasa.group(1)
    yasa = _YASA_URL_REV_RE.search(path)
    if yasa is not None:
        return "yasa", str(int(yasa.group(1))), yasa.group(2)
    yasa = _YEAR_PATH_RE.search(path)
    if yasa is not None:
        return "yasa", str(int(yasa.group(2))), yasa.group(1)
    yasa = _NUM_YEAR_URL_RE.search(path)
    if yasa is not None:
        return "yasa", str(int(yasa.group(1))), yasa.group(2)
    return None


def _law_from_number(value: str | None) -> _Classification | None:
    """Portalın `Numara` alanından (`Fasıl 113`, `42/2024`) sınıflandırma üretir."""
    if not value:
        return None
    text = " ".join(str(value).split())
    folded = _fold(text)
    if "anayasa" in folded:
        return "anayasa", None, None
    fasil = re.fullmatch(r"fas[ıi]l\s*(?:no\.?|numarası)?\s*[:\-]?\s*(\d{1,3})", folded)
    if fasil is not None:
        return "fasil", str(int(fasil.group(1))), None
    yasa = re.fullmatch(r"(\d{1,3})\s*/\s*(\d{4})", folded)
    if yasa is not None:
        return "yasa", str(int(yasa.group(1))), yasa.group(2)
    return None


def filter_entries(
    entries: Sequence[LawIndexEntry],
    *,
    limit: int | None = None,
    matches: Sequence[str] | None = None,
) -> list[LawIndexEntry]:
    """Yasa girdilerini kimlik/başlık eşleşmesine göre filtreler ve sınırlar.

    Args:
        entries: Fihristten gelen girdiler.
        limit: En fazla döndürülecek yasa sayısı; `None` ise sınırsız.
        matches: `law_id` veya başlık içinde aranacak metinler (OR semantiği).

    Returns:
        `law_id` sıralı, filtrelenmiş ve `limit` ile sınırlanmış girdiler.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit en az 1 olmalıdır")
    patterns = [_fold(item.strip()) for item in (matches or []) if item.strip()]
    selected: list[LawIndexEntry] = []
    for entry in entries:
        haystack = f"{_fold(entry.law_id)}\n{_fold(entry.title)}"
        if patterns and not any(pattern in haystack for pattern in patterns):
            continue
        selected.append(entry)
    selected.sort(key=lambda entry: (0 if _fold(entry.law_id) in patterns else 1, entry.law_id))
    if limit is not None:
        selected = selected[:limit]
    return selected


def discover_index_pages(html: str | bytes, base_url: str) -> list[str]:
    """Fihrist HTML'inden keşfedilmesi gereken iframe/alt kategori adreslerini çıkarır.

    Portal sayfası kendi verisini AJAX ile yüklediği için burada yalnızca aynı
    alan adındaki HTML/iframe adresleri döndürülür. `parse_mahkemeler_index`
    doğrudan bağlantıları zaten işler; bu fonksiyon eksik kalan alt sayfaları bulur.
    """
    parsed_base = urlparse(base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        raise ValueError(f"Geçersiz base_url: {base_url!r}")
    soup = BeautifulSoup(_decode_html(html), "lxml")
    candidates: list[str] = []

    for iframe in soup.find_all("iframe", src=True):
        candidates.append(urljoin(base_url, str(iframe.get("src"))))

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(_SKIPPED_SCHEMES):
            continue
        absolute = urljoin(base_url, href)
        target = urlparse(absolute)
        if target.scheme not in {"http", "https"}:
            continue
        if target.netloc.lower() != parsed_base.netloc.lower():
            continue
        if PurePosixPath(target.path).suffix.lower() not in _HTML_SUFFIXES:
            continue
        if target.path.rstrip("/") == parsed_base.path.rstrip("/"):
            continue
        if _law_from_text(anchor.get_text(" ")) or _law_from_url(absolute):
            continue
        text = _fold(f"{anchor.get_text(' ')} {PurePosixPath(target.path).name}")
        if any(hint in text for hint in _INDEX_PAGE_HINTS):
            candidates.append(absolute)

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.split("#", 1)[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result



def _make_entry(
    classification: _Classification,
    *,
    title: str,
    url: str,
) -> LawIndexEntry:
    category, number, year = classification
    if category == "anayasa":
        law_id = "anayasa"
        number_value = None
    elif category == "fasil":
        assert number is not None
        law_id = f"fasil-{int(number)}"
        number_value = str(int(number))
    else:
        assert number is not None and year is not None
        law_id = f"yasa-{year}-{int(number)}"
        number_value = f"{int(number)}/{year}"
    return LawIndexEntry(
        law_id=law_id,
        title=title or law_id,
        url=url,
        category=category,
        number=number_value,
    )


def parse_mahkemeler_index(
    html: str | bytes, base_url: str
) -> list[LawIndexEntry]:
    """Fihrist HTML'inden mevzuat bağlantılarını çıkarır.

    Args:
        html: Fihrist sayfasının ham içeriği.
        base_url: Göreli bağlantıları çözmek için kullanılan sayfa adresi.

    Returns:
        `law_id`'ye göre alfabetik sıralı, tekilleştirilmiş girdiler. Aynı yasa birden
        fazla bağlantıyla geçerse en uzun (en açıklayıcı) başlık korunur.

    Raises:
        ValueError: `base_url` geçerli bir HTTP(S) adresi değilse.
    """
    parsed_base = urlparse(base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        raise ValueError(f"Geçersiz base_url: {base_url!r}")

    soup = BeautifulSoup(_decode_html(html), "lxml")
    candidates: list[LawIndexEntry] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        folded_href = _fold(href)
        if not href or folded_href.startswith(_SKIPPED_SCHEMES):
            continue

        absolute = urljoin(base_url, href)
        parsed_target = urlparse(absolute)
        if parsed_target.scheme not in {"http", "https"}:
            continue
        if parsed_target.netloc.lower() != parsed_base.netloc.lower():
            continue
        if PurePosixPath(parsed_target.path).suffix.lower() in _ASSET_SUFFIXES:
            continue
        if parsed_target.path.rstrip("/") == parsed_base.path.rstrip("/"):
            continue

        title = _normalize_link_text(anchor.get_text(" "))
        if not title:
            title = _normalize_link_text(str(anchor.get("title", "")))

        classification = _law_from_text(title) or _law_from_url(absolute)
        if classification is None:
            continue
        candidates.append(_make_entry(classification, title=title, url=absolute))

    best: dict[str, LawIndexEntry] = {}
    for entry in candidates:
        current = best.get(entry.law_id)
        if current is None or (-len(entry.title), entry.title, entry.url) < (
            -len(current.title),
            current.title,
            current.url,
        ):
            best[entry.law_id] = entry

    best.setdefault("anayasa", ANAYASA_ENTRY)
    return [best["anayasa"]] + [best[law_id] for law_id in sorted(best) if law_id != "anayasa"]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_cache_manifest(cache_dir: str | os.PathLike[str]) -> CacheManifest:
    """`raw_cache/manifest.json` dosyasını okur; yoksa boş manifest döndürür."""
    path = Path(cache_dir) / CACHE_MANIFEST_FILENAME
    if not path.is_file():
        return CacheManifest()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return CacheManifest.model_validate(payload)


def write_cache_manifest(
    cache_dir: str | os.PathLike[str], manifest: CacheManifest
) -> Path:
    """Manifesti anahtarları sıralı, deterministik JSON olarak yazar."""
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json")
    entries = payload.get("entries", {})
    payload["entries"] = {key: entries[key] for key in sorted(entries)}
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"

    target = directory / CACHE_MANIFEST_FILENAME
    temporary = directory / f".{CACHE_MANIFEST_FILENAME}.tmp"
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, target)
    return target


class MahkemelerScraper:
    """`mahkemeler.net` belgelerini önbellekli ve sınırlı eşzamanlılıkla indirir."""

    def __init__(
        self,
        cache_dir: str | os.PathLike[str],
        *,
        index_url: str = DEFAULT_INDEX_URL,
        max_concurrency: int = 4,
        timeout: float = 30.0,
        max_attempts: int = 4,
        client: httpx.AsyncClient | None = None,
        strict: bool = False,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency en az 1 olmalıdır")
        if max_attempts < 1:
            raise ValueError("max_attempts en az 1 olmalıdır")
        if timeout <= 0:
            raise ValueError("timeout pozitif olmalıdır")

        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_url = index_url
        self.max_concurrency = max_concurrency
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.strict = strict
        self.skipped: list[dict[str, str]] = []
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client = client
        self._owns_client = False

    async def __aenter__(self) -> MahkemelerScraper:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Yalnızca bu nesnenin oluşturduğu HTTP istemcisini kapatır."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            self._owns_client = True
        return self._client

    async def _get_response(self, url: str) -> httpx.Response:
        client = await self._get_client()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
            retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                async with self._semaphore:
                    response = await client.get(url)
                response.raise_for_status()
                if not response.content:
                    raise ValueError(f"Boş yanıt alındı: {url}")
                return response
        raise RuntimeError("ulaşılamaz")  # pragma: no cover - tenacity reraise eder

    async def _post_portal_grid(
        self, endpoint: str, page_url: str, payload: dict[str, str]
    ) -> dict[str, object]:
        client = await self._get_client()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
            retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                async with self._semaphore:
                    response = await client.post(
                        endpoint,
                        data=payload,
                        headers={
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": page_url,
                        },
                    )
                response.raise_for_status()
                if not response.content:
                    raise ValueError(f"Boş portal yanıtı: {endpoint}")
                if "json" not in (response.headers.get("content-type") or "").lower():
                    raise ValueError("Portal yanıtı JSON değil")
                return response.json()
        raise RuntimeError("ulaşılamaz")  # pragma: no cover - tenacity reraise eder

    async def _fetch_portal_entries(
        self, page_url: str, html: str
    ) -> list[LawIndexEntry]:
        """DataTables tabanlı gerçek mevzuat portalından tüm fihrist satırlarını okur."""
        endpoint = urljoin(page_url, _PORTAL_GRID_PATH)
        rows: list[dict[str, object]] = []
        start = 0
        length = 5000
        while True:
            payload = {
                "draw": str(start // length + 1),
                "start": str(start),
                "length": str(length),
                "NeyeGore": "arama",
                "Numara": "",
                "YasaAd": "",
                "YasaMetin": "",
                "YasaTurPkey": "",
            }
            response = await self._post_portal_grid(endpoint, page_url, payload)
            page_rows = response.get("data")
            if not isinstance(page_rows, list) or not page_rows:
                break
            rows.extend(item for item in page_rows if isinstance(item, dict))
            total_raw = response.get("recordsFiltered") or response.get("recordsTotal") or 0
            try:
                total = int(total_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                total = len(rows)
            if len(rows) >= total or len(page_rows) < length:
                break
            start += len(page_rows)

        entries: list[LawIndexEntry] = []
        for row in rows:
            title = _normalize_link_text(str(row.get("YasaAd") or ""))
            classification = _law_from_number(str(row.get("Numara") or ""))
            if classification is None:
                classification = _law_from_text(title)
            if classification is None:
                continue
            link = str(row.get("DownloadLink") or "").strip()
            if not link:
                continue
            absolute = urljoin(page_url, link)
            suffix = PurePosixPath(urlparse(absolute).path).suffix.lower()
            if suffix not in {".doc", ".docx", ".pdf", ".rtf", ".htm", ".html"}:
                continue
            entries.append(_make_entry(classification, title=title, url=absolute))
        return self._dedupe_entries(entries)

    @staticmethod
    def _is_portal_page(page_url: str, html: str) -> bool:
        folded_url = _fold(page_url)
        folded_html = _fold(html[:200_000])
        return (
            "yasafihrist" in folded_url
            or "yasaizgaracore.js" in folded_html
            or "loaddataforgrid" in folded_html
        )

    async def fetch_index(self, index_url: str | None = None) -> list[LawIndexEntry]:
        """Fihrist sayfasını (iframe/alt kategoriler dahil) indirip girdileri döndürür.

        Gerçek `mevzuat.mahkemeler.net` sayfası yasaları DataTables/AJAX ile yükler;
        bu durumda portal uç noktası otomatik keşfedilir ve JSON satırları okunur.
        Statik HTML fihristlerde ise bağlantılar ve iframe/alt kategori sayfaları
        sınırlı derinlikte taranır.
        """
        url = index_url or self.index_url
        if not url:
            raise ValueError("index_url boş olamaz")

        queue: list[tuple[str, int]] = [(url, 0)]
        visited: set[str] = set()
        entries: list[LawIndexEntry] = []
        while queue and len(visited) < 20:
            current_url, depth = queue.pop(0)
            if current_url in visited:
                continue
            visited.add(current_url)
            response = await self._get_response(current_url)
            page_url = str(response.url)
            page_html = _decode_html(response.content)

            if self._is_portal_page(page_url, page_html):
                try:
                    portal_entries = await self._fetch_portal_entries(page_url, page_html)
                except Exception as exc:
                    if self.strict:
                        raise
                    logger.warning("Portal fihristi okunamadı (%s): %s", page_url, exc)
                    portal_entries = []
                if portal_entries:
                    entries.extend(portal_entries)
                    continue

            entries.extend(parse_mahkemeler_index(response.content, base_url=page_url))
            if depth >= 2:
                continue
            for child in discover_index_pages(response.content, page_url):
                if child not in visited:
                    queue.append((child, depth + 1))

        if not any(entry.law_id == "anayasa" for entry in entries):
            entries.append(ANAYASA_ENTRY)
        return self._dedupe_entries(entries)


    def _cached_document(
        self, law_id: str, manifest: CacheManifest
    ) -> CachedDocument | None:
        document = manifest.get(law_id)
        if document is None:
            return None
        path = self.cache_dir / document.raw_path
        if not path.is_file():
            return None
        if _sha256_bytes(path.read_bytes()) != document.sha256:
            return None
        return document

    async def _fetch_one(
        self,
        entry: LawIndexEntry,
        manifest: CacheManifest,
        force_refresh: bool,
    ) -> CachedDocument:
        if not force_refresh:
            cached = self._cached_document(entry.law_id, manifest)
            if cached is not None:
                if cached.url != entry.url:
                    return cached.model_copy(update={"url": entry.url})
                return cached

        response = await self._get_response(entry.url)
        content = response.content
        target_suffix = PurePosixPath(urlparse(entry.url).path).suffix.lower()
        if target_suffix not in {".doc", ".docx", ".pdf", ".rtf", ".htm", ".html"}:
            target_suffix = ".html"
        raw_path = f"{entry.law_id}{target_suffix}"
        target = self.cache_dir / raw_path
        target.write_bytes(content)
        try:
            os.chmod(target, 0o644)
        except OSError:
            pass

        return CachedDocument(
            law_id=entry.law_id,
            url=entry.url,
            sha256=_sha256_bytes(content),
            encoding=response.charset_encoding or response.encoding,
            raw_path=raw_path,
            content_length=len(content),
        )

    @staticmethod
    def _dedupe_entries(entries: Sequence[LawIndexEntry]) -> list[LawIndexEntry]:
        unique: dict[str, LawIndexEntry] = {}
        for entry in entries:
            current = unique.get(entry.law_id)
            if current is None:
                unique[entry.law_id] = entry
                continue
            if current.url != entry.url:
                logger.warning(
                    "%s için birden fazla URL bulundu; deterministik seçim yapılıyor: "
                    "%r / %r",
                    entry.law_id,
                    current.url,
                    entry.url,
                )
            if (-len(entry.title), entry.url) < (-len(current.title), current.url):
                unique[entry.law_id] = entry
        return [unique[law_id] for law_id in sorted(unique)]

    async def fetch_law(
        self, entry: LawIndexEntry, *, force_refresh: bool = False
    ) -> CachedDocument:
        """Tek bir belgeyi indirir veya geçerli önbellek kaydını döndürür."""
        manifest = load_cache_manifest(self.cache_dir)
        document = await self._fetch_one(entry, manifest, force_refresh)
        manifest.upsert(document)
        write_cache_manifest(self.cache_dir, manifest)
        return document

    async def fetch_all(
        self,
        entries: Sequence[LawIndexEntry],
        *,
        force_refresh: bool = False,
        limit: int | None = None,
        matches: Sequence[str] | None = None,
    ) -> CacheManifest:
        """Belgeleri eşzamanlılık sınırı içinde indirir ve manifesti yazar.

        Tek bir belge indirilemezse `--strict` verilmedikçe diğerlerine devam edilir;
        hata `self.skipped` listesine ve loga yazılır.
        """
        ordered = self._dedupe_entries(filter_entries(entries, limit=limit, matches=matches))
        manifest = load_cache_manifest(self.cache_dir)
        results = await asyncio.gather(
            *(
                self._fetch_one(entry, manifest, force_refresh)
                for entry in ordered
            ),
            return_exceptions=True,
        )
        for entry, result in zip(ordered, results, strict=True):
            if isinstance(result, BaseException):
                reason = f"{type(result).__name__}: {result}"
                self.skipped.append({"law_id": entry.law_id, "reason": reason})
                logger.warning("Belge indirilemedi (%s): %s", entry.law_id, reason)
                if self.strict:
                    raise result
                continue
            manifest.upsert(result)
        write_cache_manifest(self.cache_dir, manifest)
        return manifest

    async def scrape(
        self,
        *,
        force_refresh: bool = False,
        limit: int | None = None,
        matches: Sequence[str] | None = None,
    ) -> tuple[list[LawIndexEntry], CacheManifest]:
        """Fihristi indirir, seçilen belgeleri önbelleğe alır ve ikisini döndürür."""
        entries = await self.fetch_index()
        selected = filter_entries(entries, limit=limit, matches=matches)
        manifest = await self.fetch_all(
            selected,
            force_refresh=force_refresh,
            limit=limit,
            matches=matches,
        )
        return selected, manifest

