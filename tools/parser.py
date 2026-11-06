"""KKTC mevzuat metinlerini ayrıştıran, normalleştiren ve commit planına dönüştüren modül.

Modül üç katmanlı çalışır:

1. **Normalizasyon:** `windows-1254` / `iso-8859-9` / UTF-8 kodlamalarını ve mojibake
   bozulmalarını temizler; Word kaynaklı HTML'i blok yapısını koruyarak düz metne çevirir.
2. **Ayrıştırma:** Yasa kimliği/başlığı, kenar notlarındaki değişiklik referansları
   (`2(a) 34/2019`) ve maddeler (`madde-001`, `gecici-madde-001`) çıkarılır.
3. **Replay planı:** Konsolide metin, yürürlük tarihine göre sıralı bir
   `LegislativeCommitPlan` listesine dönüştürülür; liste doğrudan
   `DeterministicGitEngine`'e beslenebilir.

Not: `mahkemeler.net` yalnızca konsolide metni yayımladığı için Faz 2 "hibrit replay"
yaklaşımını uygular: ilk commit konsolide metnin tamamını taşır, sonraki commit'ler
`metadata.json` geçmişini büyütür ve ilgili maddeleri yeniden yazar.
"""

from __future__ import annotations

import codecs
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Final

from bs4 import BeautifulSoup

from tools.amendment_patcher import apply_article_patches
from tools.git_engine import COLONIAL_AUTHOR_NAME, DEFAULT_AUTHOR_NAME
from tools.models import (
    AmendmentRecord,
    AmendmentReference,
    ArticlePatch,
    LawHeader,
    LawMetadata,
    LegislativeCommitPlan,
    ParsedArticle,
    ParsedLaw,
    validate_iso_date,
)

__all__ = [
    "build_chronological_commits",
    "extract_amendment_laws",
    "extract_amendment_refs",
    "law_header_from_id",
    "load_law_date_overrides",
    "normalize_legal_text",
    "parse_articles",
    "parse_law_header",
    "turkish_title_case",
]

DEFAULT_LAW_DATES_OVERRIDE_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent / "data" / "law_dates_override.json"
)

_HTML_TAG_RE: Final[re.Pattern[str]] = re.compile(r"</?[A-Za-z][^>]*>")
_META_CHARSET_RE: Final[re.Pattern[bytes]] = re.compile(
    rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9._:\-]+)", re.IGNORECASE
)
_BLOCK_TAGS: Final[tuple[str, ...]] = (
    "p",
    "div",
    "tr",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "ul",
    "ol",
    "blockquote",
    "pre",
    "section",
    "article",
    "header",
    "footer",
)
_SPACE_CHARS_RE: Final[re.Pattern[str]] = re.compile(
    r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]"
)
_ZERO_WIDTH_RE: Final[re.Pattern[str]] = re.compile(r"[\u200b-\u200d\u2060\ufeff\u00ad]")
_INLINE_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t\f\v]+")

_MOJIBAKE_MARKERS: Final[tuple[str, ...]] = ("Ã", "Ä", "Å", "â€", "ð", "þ")
_MOJIBAKE_MAP: Final[dict[str, str]] = {
    "Ä±": "ı",
    "Ä°": "İ",
    "Ä\x9f": "ğ",
    "ÄŸ": "ğ",
    "Äž": "Ğ",
    "Å\x9f": "ş",
    "ÅŸ": "ş",
    "Åž": "Ş",
    "Ã¼": "ü",
    "Ãœ": "Ü",
    "Ã§": "ç",
    "Ã‡": "Ç",
    "Ã¶": "ö",
    "Ã–": "Ö",
    "Ã¢": "â",
    "Ã®": "î",
    "Ã»": "û",
    "â€“": "–",
    "â€”": "—",
    "â€™": "’",
    "â€œ": "“",
    "â€\x9d": "”",
    "â€¦": "…",
}

_ENCODING_ALIASES: Final[dict[str, str]] = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "windows-1254": "cp1254",
    "cp1254": "cp1254",
    "latin5": "iso8859-9",
    "iso-8859-9": "iso8859-9",
    "iso8859-9": "iso8859-9",
    "windows-1252": "cp1252",
    "cp1252": "cp1252",
}

_TURKISH_MONTHS: Final[dict[str, int]] = {
    "ocak": 1,
    "şubat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "eylül": 9,
    "ekim": 10,
    "kasım": 11,
    "aralık": 12,
}

_ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<year>(?:18|19|20)\d{2})-(?P<month>\d{2})-(?P<day>\d{2})\b"
)
MIN_LEGAL_YEAR: Final[int] = 1878
MAX_LEGAL_YEAR: Final[int] = 2026


def is_plausible_legal_date(value: str) -> bool:
    """Tarihin Kıbrıs İngiliz İdaresi başlangıcı ile 2026 arasında olduğunu doğrular."""
    return "1878-01-01" <= value <= "2026-12-31"
_LONG_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<month>"
    + "|".join(_TURKISH_MONTHS)
    + r")\s*,?\s+(?P<year>(?:18|19|20)\d{2})\b"
)
_DOTTED_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<day>\d{1,2})[./](?P<month>\d{1,2})[./](?P<year>(?:18|19|20)\d{2})\b"
)
_EDITION_YEAR_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<year>(?:18|19|20)\d{2})\s*(?:Bask[ıi]s[ıi]|Ed\.?)", re.IGNORECASE
)

_ANAYASA_RE: Final[re.Pattern[str]] = re.compile(
    r"kuzey\s+kıbrıs\s+türk\s+cumhuriyeti\s+anayasası|kktc\s+anayasası"
)
_FASIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\bfasıl\s*(?:no\.?|numarası)?\s*[:\-]?\s*(?P<number>\d{1,3})\b"
)
_BOLUM_NO_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*bölüm\s+(?P<number>\d{1,3})\b", re.IGNORECASE
)
_YASA_SAYILI_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<number>\d{1,3})\s*/\s*(?P<year>\d{4})\s*sayılı\b"
)
_YASA_SAYISI_RE: Final[re.Pattern[str]] = re.compile(
    r"\bsayısı\s*[:\-]?\s*(?P<number>\d{1,3})\s*/\s*(?P<year>\d{4})\b"
)
_YASA_BARE_RE: Final[re.Pattern[str]] = re.compile(r"\b(?P<number>\d{1,3})\s*/\s*(?P<year>\d{4})\b")

_LAW_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\d/])(?P<number>\d{1,3})\s*/\s*(?P<year>\d{2}|\d{4})(?![\d/])"
)
_ARTICLE_BEFORE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.])(?:madde\s+)?(?P<number>\d{1,3})\s*\.?\s*"
    r"(?P<clauses>(?:\([0-9A-Za-z]{1,3}\)){0,3})\s*[,;:\-–—]?\s*$",
    re.IGNORECASE,
)

_ACRONYMS: Final[frozenset[str]] = frozenset({"KKTC", "TRNC", "KHK", "TBMM", "AB", "BM", "TC"})
_GENERIC_TITLES: Final[frozenset[str]] = frozenset(
    {"", "yasa", "kanun", "sayılı yasa", "sayılı kanun", "tüzük", "khk"}
)


def _fold(text: str) -> str:
    """Türkçe'ye duyarlı, küçük harfli arama biçimi üretir (`İ`->i, `I`->ı)."""
    return unicodedata.normalize("NFC", text).replace("İ", "i").replace("I", "ı").casefold()


def _tr_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _tr_capitalize(word: str) -> str:
    lowered = _tr_lower(word)
    if not lowered:
        return lowered
    first = lowered[0]
    if first == "i":
        upper = "İ"
    elif first == "ı":
        upper = "I"
    else:
        upper = first.upper()
    return upper + lowered[1:]


def turkish_title_case(text: str) -> str:
    """Tümü büyük harfli Türkçe başlıkları kısaltmaları koruyarak başlık biçimine çevirir."""
    words: list[str] = []
    for word in text.split():
        core = word.strip(".,;:()[]\"'")
        if core and core.isupper() and (core in _ACRONYMS or len(core) <= 4):
            words.append(word)
        else:
            words.append(_tr_capitalize(word))
    return " ".join(words)


def _canonical_encoding(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().lower().replace("_", "-")
    if key in _ENCODING_ALIASES:
        return _ENCODING_ALIASES[key]
    try:
        codecs.lookup(key)
    except LookupError:
        return None
    return key


def _sniff_meta_charset(data: bytes) -> str | None:
    match = _META_CHARSET_RE.search(data[:8192])
    if match is None:
        return None
    return match.group(1).decode("ascii", "ignore")


def _decode_bytes(data: bytes, encoding: str | None = None) -> str:
    """Byte girdisini BOM, meta charset ve deneme sırasıyla çözer."""
    for bom, name in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(name, errors="replace")

    # Geçerli UTF-8, yanlış meta etiketinden daha güçlü bir kanıttır.
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    declared = _canonical_encoding(encoding) or _canonical_encoding(_sniff_meta_charset(data))
    candidates: list[str] = []
    if declared:
        candidates.append(declared)
    candidates.extend(["cp1254", "iso8859-9", "cp1252", "latin-1"])

    seen: set[str] = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            return data.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _mojibake_score(text: str) -> int:
    return sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)


def _repair_mojibake(text: str) -> str:
    """UTF-8'in yanlış tek baytlı kodlamayla çözülmesinden doğan bozulmaları onarır."""
    if not any(marker in text for marker in _MOJIBAKE_MARKERS):
        return text

    best = text
    best_score = _mojibake_score(text)
    for encoding in ("cp1254", "cp1252", "latin-1"):
        try:
            candidate = text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        score = _mojibake_score(candidate)
        if score < best_score:
            best, best_score = candidate, score

    for broken, fixed in _MOJIBAKE_MAP.items():
        best = best.replace(broken, fixed)
    return best


def _looks_like_html(text: str) -> bool:
    return bool(_HTML_TAG_RE.search(text))


def _html_to_text(html: str) -> str:
    """Word kaynaklı HTML'i blok sınırlarını koruyarak düz metne çevirir."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "head", "meta", "link"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_after("\n\n")
    return soup.get_text(separator="\n")


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    text = _SPACE_CHARS_RE.sub(" ", text)
    text = _ZERO_WIDTH_RE.sub("", text)

    lines: list[str] = []
    for raw_line in text.split("\n"):
        lines.append(_INLINE_SPACE_RE.sub(" ", raw_line).strip())

    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        if line:
            collapsed.append(line)
            previous_blank = False
        elif collapsed and not previous_blank:
            collapsed.append("")
            previous_blank = True

    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)


def normalize_legal_text(value: str | bytes, *, encoding: str | None = None) -> str:
    """Mevzuat metnini temiz UTF-8 NFC düz metne dönüştürür.

    Args:
        value: HTML, düz metin veya ham byte içeriği.
        encoding: Byte girdisi için isteğe bağlı kodlama ipucu.

    Returns:
        `<script>/<style>` temizlenmiş, blok yapısı `\\n` ile korunmuş, `\\xa0` ve
        fazla boşlukları tekilleştirilmiş, ardışık boş satırları tek boş satıra
        indirgenmiş metin.
    """
    if isinstance(value, bytes):
        text = _decode_bytes(value, encoding)
    elif isinstance(value, str):
        text = value
    else:
        raise TypeError(f"Metin veya byte bekleniyordu, alınan: {type(value).__name__}")

    text = text.lstrip("\ufeff")
    text = _repair_mojibake(text)
    if _looks_like_html(text):
        text = _html_to_text(text)
    text = unicodedata.normalize("NFC", text)
    return _normalize_whitespace(text)


def _header_region(text: str, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    lines = [line for line in text.split("\n") if line.strip()][:max_lines]
    return "\n".join(lines)[:max_chars]


def _valid_date(year: int, month: int, day: int) -> str | None:
    if not MIN_LEGAL_YEAR <= year <= MAX_LEGAL_YEAR:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_original_date(region: str) -> str | None:
    folded = _fold(region)
    for pattern in (_ISO_DATE_RE, _LONG_DATE_RE, _DOTTED_DATE_RE):
        for match in pattern.finditer(folded):
            month_raw = match.groupdict().get("month")
            if month_raw is None:
                continue
            if month_raw.isdigit():
                month = int(month_raw)
            else:
                month = _TURKISH_MONTHS.get(month_raw, 0)
            if not 1 <= month <= 12:
                continue
            parsed = _valid_date(int(match.group("year")), month, int(match.group("day")))
            if parsed is not None:
                return parsed
    return None


def _extract_earliest_date(text: str) -> str | None:
    """Metindeki geçerli tarihlerin en eskisini döndürür (uzun fihristler için)."""
    dates: list[str] = []
    for line in text.splitlines():
        parsed = _extract_original_date(line)
        if parsed is not None:
            dates.append(parsed)
    return min(dates) if dates else None


@lru_cache(maxsize=4)
def load_law_date_overrides(
    path: str | Path | None = None,
) -> dict[str, str]:
    """`data/law_dates_override.json` doğrulanmış tarih eşlemesini yükler."""
    source = Path(path) if path is not None else DEFAULT_LAW_DATES_OVERRIDE_PATH
    if not source.is_file():
        return {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Tarih override dosyası JSON nesnesi olmalıdır: {source}")
    result: dict[str, str] = {}
    for raw_law_id, raw_date in payload.items():
        law_id = str(raw_law_id).strip()
        if not re.fullmatch(r"(?:fasil-\d+|yasa-\d{4}-\d+|anayasa)", law_id):
            raise ValueError(f"Geçersiz law_id override anahtarı: {raw_law_id!r}")
        result[law_id] = validate_iso_date(str(raw_date))
    return {law_id: result[law_id] for law_id in sorted(result)}


def _earliest_amendment_year(text: str) -> int | None:
    references = extract_amendment_refs(text)
    years = [reference.year for reference in references]
    years.extend(int(match.group("year")) for match in _EDITION_YEAR_RE.finditer(text))
    return min(years) if years else None


def _resolve_law_date(
    law_id: str,
    law_type: str,
    document_date: str | None,
    text: str,
    overrides: Mapping[str, str],
) -> tuple[str | None, str]:
    """Tarih çözümleme sırası: override → belge → tür fallback."""
    if law_id in overrides:
        override_date = validate_iso_date(overrides[law_id])
        if not is_plausible_legal_date(override_date):
            raise ValueError(f"Geçersiz tarih override: {law_id} -> {override_date}")
        return override_date, "override"
    if document_date is not None:
        return validate_iso_date(document_date), "explicit"
    if law_type == "fasil":
        earliest_year = _earliest_amendment_year(text)
        year = earliest_year if earliest_year is not None and earliest_year < 1959 else 1959
        return f"{year:04d}-01-01", "fallback_1959"
    if law_type == "yasa":
        match = re.fullmatch(r"yasa-(\d{4})-\d+", law_id)
        if match is not None:
            year = int(match.group(1))
            if MIN_LEGAL_YEAR <= year <= MAX_LEGAL_YEAR:
                return f"{year:04d}-01-01", "fallback_law_year"
    return None, "explicit"


def _clean_title_candidate(candidate: str) -> str:
    text = candidate.strip(" .,;:-\t")
    text = re.sub(r"^(?:sayılı\s+yasa|sayılı\s+kanun|sayılı|yasa|kanun)\s+", "", text, flags=re.I)
    text = re.sub(r"^kuzey\s+kıbrıs\s+türk\s+cumhuriyeti\s+", "", text, flags=re.I)
    text = text.strip(" .,;:-\t")
    if len(text) < 3:
        return ""
    if _fold(text) in _GENERIC_TITLES:
        return ""
    return turkish_title_case(text)


def _looks_like_title(line: str) -> bool:
    folded = _fold(line)
    if not 3 <= len(line) <= 160:
        return False
    if not any(character.isalpha() for character in line):
        return False
    if folded.startswith(("madde ", "geçici madde", "bölüm", "kısım", "yürürlük", "kabul")):
        return False
    if line.startswith(("(", "[")) or "http" in folded or "www." in folded:
        return False
    if _extract_original_date(line) is not None:
        return False
    return len(line.split()) <= 18


def _title_after_match(region: str, match: re.Match[str]) -> str:
    lines = region.split("\n")
    offset = region.count("\n", 0, match.start())
    line_start = region.rfind("\n", 0, match.start()) + 1
    line_end = region.find("\n", match.start())
    if line_end == -1:
        line_end = len(region)
    line = region[line_start:line_end]

    remainder = line[: match.start() - line_start] + " " + line[match.end() - line_start :]
    candidate = _clean_title_candidate(remainder)
    if candidate:
        return candidate

    for next_line in lines[offset + 1 : offset + 5]:
        stripped = next_line.strip()
        if not stripped or stripped.isdigit():
            continue
        if not _looks_like_title(stripped):
            return ""
        return _clean_title_candidate(stripped) or turkish_title_case(stripped)
    return ""


def parse_law_header(
    value: str | bytes,
    *,
    date_overrides: Mapping[str, str] | None = None,
) -> LawHeader:
    """Mevzuat metninin başlık bölgesinden kimlik, başlık ve yürürlük tarihini çıkarır.

    Tarih sırası: doğrulanmış `date_overrides` → belge içi tarih → tür fallback'i.
    Fasıllar için 1959 Revize Baskısı (veya daha eski değişiklik yılı), yasalar için
    `law_id` içindeki yıl kullanılır.
    """
    text = normalize_legal_text(value)
    region = _header_region(text)
    folded = _fold(region)
    document_date = _extract_original_date(region)
    if document_date is None:
        # Word kaynaklı uzun fihristlerde yürürlük tarihi ilk 40 satırdan sonra
        # gelebilir; tam metindeki en eski geçerli tarihi geri dönüş olarak kullan.
        document_date = _extract_earliest_date(text)
    overrides = (
        load_law_date_overrides()
        if date_overrides is None
        else {
            str(law_id): validate_iso_date(str(raw_date))
            for law_id, raw_date in date_overrides.items()
        }
    )

    def make_header(
        law_id: str,
        law_type: str,
        number: str,
        title: str,
    ) -> LawHeader:
        original_date, date_source = _resolve_law_date(
            law_id, law_type, document_date, text, overrides
        )
        return LawHeader(
            law_id=law_id,
            type=law_type,  # type: ignore[arg-type]
            number=number,
            title=title,
            original_date=original_date,
            date_source=date_source,  # type: ignore[arg-type]
        )

    anayasa_match = _ANAYASA_RE.search(folded)
    if anayasa_match is not None:
        return make_header(
            law_id="anayasa",
            law_type="anayasa",
            number="1",
            title="Kuzey Kıbrıs Türk Cumhuriyeti Anayasası",
        )

    fasil_match = _FASIL_RE.search(folded)
    if fasil_match is not None:
        number = str(int(fasil_match.group("number")))
        return make_header(
            law_id=f"fasil-{number}",
            law_type="fasil",
            number=number,
            title=_title_after_match(region, fasil_match),
        )

    bolum_match = _BOLUM_NO_RE.match(folded)
    if bolum_match is not None:
        number = str(int(bolum_match.group("number")))
        return make_header(
            law_id=f"fasil-{number}",
            law_type="fasil",
            number=number,
            title=_title_after_match(region, bolum_match),
        )

    for pattern in (_YASA_SAYILI_RE, _YASA_SAYISI_RE, _YASA_BARE_RE):
        yasa_match = pattern.search(folded)
        if yasa_match is None:
            continue
        if pattern is _YASA_BARE_RE and "yasa" not in folded and "kanun" not in folded:
            continue
        number = str(int(yasa_match.group("number")))
        year = yasa_match.group("year")
        return make_header(
            law_id=f"yasa-{year}-{number}",
            law_type="yasa",
            number=f"{number}/{year}",
            title=_title_after_match(region, yasa_match),
        )

    raise ValueError("Mevzuat başlığı çözümlenemedi: Fasıl, Sayı/Yıl veya Anayasa ifadesi yok")


def _fallback_title(text: str) -> str:
    """Belge gövdesinden ilk anlamlı başlık satırını çıkarır."""
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        folded = _fold(line)
        if folded.startswith(("sayı:", "kuzey kıbrıs", "resmi gazete", "madde ")):
            continue
        if line[0].isdigit():
            continue
        for marker in ("İÇDÜZENİ", "İÇ DÜZENİ", "İÇİNDEKİLER"):
            if marker in line.upper():
                line = line.upper().split(marker, 1)[0].strip()
                break
        if len(line) < 3:
            continue
        return _clean_title_candidate(line) or turkish_title_case(line)
    return ""


def law_header_from_id(
    law_id: str,
    value: str | bytes,
    *,
    date_overrides: Mapping[str, str] | None = None,
) -> LawHeader:
    """Bilinen `law_id` ile belge metninden başlık/tarih fallback'i üretir.

    Portal belgelerinin bir kısmında başlıkta `Sayı: NN/YYYY` satırı yoktur; bu
    durumda önbellek anahtarı kimlik için, belgenin ilk anlamlı satırı başlık için
    kullanılır. Tarih yine `_resolve_law_date` sırasına tabidir.
    """
    text = normalize_legal_text(value)
    if law_id.startswith("fasil-"):
        law_type = "fasil"
        number = law_id.removeprefix("fasil-")
    elif law_id.startswith("yasa-"):
        law_type = "yasa"
        match = re.fullmatch(r"yasa-(\d{4})-(\d+)", law_id)
        if match is None:
            raise ValueError(f"Geçersiz law_id: {law_id!r}")
        number = f"{int(match.group(2))}/{match.group(1)}"
    elif law_id == "anayasa":
        law_type = "anayasa"
        number = "1"
    else:
        raise ValueError(f"Geçersiz law_id: {law_id!r}")

    document_date = _extract_original_date(_header_region(text))
    if document_date is None:
        document_date = _extract_earliest_date(text)
    overrides = (
        load_law_date_overrides()
        if date_overrides is None
        else {
            str(key): validate_iso_date(str(raw_date))
            for key, raw_date in date_overrides.items()
        }
    )
    original_date, date_source = _resolve_law_date(
        law_id, law_type, document_date, text, overrides
    )
    return LawHeader(
        law_id=law_id,
        type=law_type,  # type: ignore[arg-type]
        number=number,
        title=_fallback_title(text) or law_id,
        original_date=original_date,
        date_source=date_source,  # type: ignore[arg-type]
    )




def _extract_article_context(context: str) -> str | None:
    match = _ARTICLE_BEFORE_RE.search(context)
    if match is None:
        return None
    number = str(int(match.group("number")))
    clauses = match.group("clauses") or ""
    return f"{number}{clauses}"


def extract_amendment_refs(value: str | bytes) -> list[AmendmentReference]:
    """Metindeki değişiklik yasası referanslarını kronolojik sırayla döndürür.

    Desteklenen desenler: `2(a) 34/2019`, `3. 41/2018`, `4(1)(b) 12/1995`, `28/1974`,
    `12/85` (-> `12/1985`). Tam tarihler (`01/06/1951`) değişiklik referansı sayılmaz.
    """
    text = normalize_legal_text(value)
    references: list[AmendmentReference] = []

    for match in _LAW_REF_RE.finditer(text):
        year_raw = match.group("year")
        if len(year_raw) not in (2, 4):
            continue
        number = str(int(match.group("number")))
        if number == "0":
            continue
        if len(year_raw) == 2:
            short_year = int(year_raw)
            # Koloni dönemi ve 2000 sonrası yasalar: 27+ iki haneli yıllar 19YY,
            # 00-26 ise 20YY'dir. Böylece 12/47 -> 1947 olur, 2047 gibi gelecek
            # tarih üretilmez.
            year = 1900 + short_year if short_year >= 27 else 2000 + short_year
        else:
            year = int(year_raw)
        if not MIN_LEGAL_YEAR <= year <= MAX_LEGAL_YEAR:
            continue

        context = text[max(0, match.start() - 48) : match.start()]
        article_ref = _extract_article_context(context)
        reference = f"{number}/{year}"
        raw = f"{article_ref} {reference}" if article_ref else reference
        references.append(
            AmendmentReference(
                reference=reference,
                number=number,
                year=year,
                article_ref=article_ref,
                raw=raw,
                position=match.start(),
            )
        )

    references.sort(key=lambda item: item.sort_key)
    unique: list[AmendmentReference] = []
    seen: set[tuple[str, str | None]] = set()
    for reference in references:
        key = (reference.reference, reference.article_ref)
        if key in seen:
            continue
        seen.add(key)
        unique.append(reference)
    return unique


def extract_amendment_laws(value: str | bytes) -> list[str]:
    """Yalnızca normalleştirilmiş `SAYI/YIL` referanslarını kronolojik döndürür."""
    return [reference.reference for reference in extract_amendment_refs(value)]


_MODERN_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<prefix>geçici\s+madde|madde)\s+(?P<number>\d{1,3})\s*[.:]?\s*"
)
_FASIL_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<number>\d{1,3})\.\s*")
_INLINE_ARTICLE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?P<number>\d{1,3})\.\s+(?=[A-ZÇĞİÖŞÜ(])"
)
_REPEAL_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:mülga|ilga|kaldırılmıştır)\b")
_BODY_STARTERS: Final[frozenset[str]] = frozenset({"bu", "bir", "her", "işbu", "aşağıdaki"})
_STRUCTURAL_TITLE_WORDS: Final[frozenset[str]] = frozenset(
    {
        "birinci",
        "ikinci",
        "üçüncü",
        "dördüncü",
        "beşinci",
        "altıncı",
        "yedinci",
        "sekizinci",
        "dokuzuncu",
        "onuncu",
        "bölüm",
        "kısım",
        "fasıl",
        "madde",
        "yürürlük",
        "kabul",
    }
)


def _is_marginal_title_line(line: str) -> bool:
    """Yeni maddenin numarasından hemen önce gelen kısa kenar başlığını tanır."""
    text = line.strip()
    if not 2 <= len(text) <= 90:
        return False
    if text.endswith((".", ";", ":", ",", "!", "?")):
        return False
    if not text[0].isupper():
        return False
    words = text.split()
    if not 1 <= len(words) <= 5:
        return False
    if _fold(text) in _STRUCTURAL_TITLE_WORDS:
        return False
    if any(word.isdigit() for word in words):
        return False
    return any(character.isalpha() for character in text)


def _is_amendment_reference_line(line: str) -> bool:
    text = line.strip()
    if not text or not extract_amendment_refs(text):
        return False
    remainder = _LAW_REF_RE.sub("", text)
    remainder = re.sub(r"\([0-9A-Za-z]{1,6}\)", "", remainder)
    remainder = re.sub(r"[\d\s()\[\],;:.\-–—_/]+", "", remainder)
    return not remainder


def _transfer_marginal_notes(drafts: list[_ArticleDraft]) -> None:
    """Madde sonundaki kenar başlıklarını ve referansları sonraki maddeye taşır."""
    for current, following in zip(drafts, drafts[1:], strict=False):
        titles: list[str] = []
        references: list[str] = []
        while True:
            while current.body_lines and not current.body_lines[-1].strip():
                current.body_lines.pop()
            if not current.body_lines:
                break
            line = current.body_lines[-1].strip()
            if _is_amendment_reference_line(line):
                references.insert(0, line)
                current.body_lines.pop()
                continue
            if _is_marginal_title_line(line):
                titles.insert(0, line)
                current.body_lines.pop()
                continue
            break
        if not titles and not references:
            continue
        if titles:
            combined = " ".join(titles)
            if not following.title:
                following.title = combined
        if references:
            following.body_lines = references + following.body_lines



@dataclass
class _ArticleDraft:
    """Ayrıştırma sırasında biriken geçici madde taslağı."""

    article_id: str
    title: str
    body_lines: list[str]

    def to_model(self) -> ParsedArticle:
        body = _normalize_whitespace("\n".join(self.body_lines)).strip("\n")
        title = _normalize_article_title(self.title)
        haystack = _fold(f"{title}\n{body}")
        return ParsedArticle(
            article_id=self.article_id,
            title=title,
            body=body,
            amendments=extract_amendment_laws(f"{title}\n{body}"),
            is_repealed=bool(_REPEAL_RE.search(haystack)),
        )


def _normalize_article_title(text: str) -> str:
    candidate = text.strip(" .;:")
    if candidate and candidate.isupper():
        return turkish_title_case(candidate)
    return candidate


def _looks_like_article_title(text: str) -> bool:
    candidate = text.strip()
    if not 2 <= len(candidate) <= 90:
        return False
    if candidate.endswith((".", ";", ":")):
        return False
    if candidate.startswith(("(", "[")):
        return False
    if len(candidate.split()) > 8:
        return False
    return sum(character.isalpha() for character in candidate) >= 2


def _has_body_after(lines: list[str], index: int) -> bool:
    for offset in range(index + 1, min(index + 5, len(lines))):
        candidate = lines[offset].strip()
        if not candidate:
            continue
        return _match_article_marker(candidate, previous_blank=True, last_main=0) is None
    return False


def _match_article_marker(
    line: str, *, previous_blank: bool, last_main: int
) -> tuple[int, bool, str] | None:
    folded = _fold(line)
    modern = _MODERN_MARKER_RE.match(folded)
    if modern is not None:
        prefix = modern.group("prefix")
        number = int(modern.group("number"))
        return number, prefix.startswith("geçici"), line[modern.end() :]

    if not previous_blank:
        return None
    fasil = _FASIL_MARKER_RE.match(line)
    if fasil is None:
        return None
    number = int(fasil.group("number"))
    if number > 999:
        return None
    if number <= last_main:
        # Word belgelerinde önce "MADDELERİN DİZİNİ" yer alır ve asıl metin
        # yeniden 1'den başlar. Yalnızca belirgin bir sıfırlanmada kabul et.
        if not (number == 1 and last_main >= 10):
            return None
    return number, False, line[fasil.end() :]


def _resolve_article_title(
    rest: str, lines: list[str], marker_index: int
) -> tuple[str, list[str], int]:
    remainder = rest.strip()
    next_index = marker_index + 1
    if remainder:
        folded_remainder = _fold(remainder)
        starts_body = any(
            folded_remainder == starter or folded_remainder.startswith(f"{starter} ")
            for starter in _BODY_STARTERS
        )
        if _looks_like_article_title(remainder) and not starts_body:
            return remainder, [], next_index
        return "", [remainder], next_index

    probe = next_index
    while probe < len(lines) and not lines[probe].strip():
        probe += 1
    if probe < len(lines):
        candidate = lines[probe].strip()
        if (
            candidate
            and _looks_like_article_title(candidate)
            and _has_body_after(lines, probe)
        ):
            return candidate, [], probe + 1
    return "", [], next_index


def _extract_preamble(text: str) -> str:
    """Anayasa metnindeki `BAŞLANGIÇ` bölümünü ilk maddeye kadar çıkarır."""
    lines = text.splitlines()
    start: int | None = None
    for index, raw_line in enumerate(lines):
        folded = _fold(raw_line.strip())
        if "5 mayıs" not in folded:
            continue
        window = " ".join(lines[index : min(len(lines), index + 8)])
        if "1985" in window and (
            "halkoylaması" in _fold(window) or "halk oylaması" in _fold(window)
        ):
            start = index
            break
    if start is None:
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            folded = _fold(stripped)
            if not folded.startswith("başlangıç"):
                continue
            # İçindekilerdeki noktalı satırları atla; asıl başlık kısa olur.
            if stripped.count(".") > 2:
                continue
            start = index + 1
            break
    if start is None:
        return ""
    collected: list[str] = []
    for raw_line in lines[start:]:
        folded = _fold(raw_line.strip())
        if folded.startswith(("birinci kısım", "madde 1", "1. ")):
            break
        if folded.startswith("madde ") and any(ch.isdigit() for ch in folded):
            break
        collected.append(raw_line)
    return _normalize_whitespace("\n".join(collected)).strip()


def _split_inline_article_markers(text: str) -> str:
    """Satır başında olmayan `1. Madde` işaretlerini blok sınırlarına ayırır."""
    if re.search(r"(?m)^\d{1,3}\.\s", text):
        return text

    def replace(match: re.Match[str]) -> str:
        start = match.start()
        prefix = text[max(0, start - 2) : start]
        line_prefix = text[text.rfind("\n", 0, start) + 1 : start].strip().lower()
        if start == 0 or "\n" in prefix:
            return match.group(0)
        if line_prefix.endswith(("madde", "geçici madde")):
            return match.group(0)
        return "\n\n" + match.group(0)

    return _INLINE_ARTICLE_RE.sub(replace, text)


def parse_articles(value: str | bytes) -> list[ParsedArticle]:
    """Modern (`Madde 1.`) ve Fasıl (`1.`) biçimlerini tek geçişte ayrıştırır.

    Fasıl numaralandırmasında bir satırın ana madde sayılabilmesi için boş satırla
    ayrılmış olması ve önceki ana maddeden büyük bir numara taşıması gerekir. Böylece
    `(1)` fıkraları ve `1951` gibi yıllar madde başlangıcı sanılmaz.
    """
    text = normalize_legal_text(value)
    text = _split_inline_article_markers(text)
    lines = text.split("\n")

    drafts: list[_ArticleDraft] = []
    current: _ArticleDraft | None = None
    last_main = 0
    previous_blank = True
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            previous_blank = True
            if current is not None and current.body_lines and current.body_lines[-1] != "":
                current.body_lines.append("")
            index += 1
            continue

        marker = _match_article_marker(line, previous_blank=previous_blank, last_main=last_main)
        if marker is None:
            if current is not None:
                current.body_lines.append(line)
            previous_blank = False
            index += 1
            continue

        number, is_temporary, rest = marker
        if not is_temporary:
            if number == 1 and last_main >= 10:
                # Fihrist/İçindekiler bölümünden asıl madde metnine geçiş.
                last_main = 0
            last_main = max(last_main, number)
        title, leading_body, next_index = _resolve_article_title(rest, lines, index)
        prefix = "gecici-madde" if is_temporary else "madde"
        current = _ArticleDraft(
            article_id=f"{prefix}-{number:03d}",
            title=title,
            body_lines=list(leading_body),
        )
        drafts.append(current)
        previous_blank = False
        index = next_index

    articles: dict[str, ParsedArticle] = {}
    preamble = _extract_preamble(text) if _ANAYASA_RE.search(_fold(text)) else ""
    if preamble and not any(draft.article_id == "madde-000" for draft in drafts):
        drafts.insert(
            0,
            _ArticleDraft(
                article_id="madde-000",
                title="Başlangıç",
                body_lines=preamble.splitlines(),
            ),
        )
    _transfer_marginal_notes(drafts)
    for draft in drafts:
        article = draft.to_model()
        existing = articles.get(article.article_id)
        if existing is None or len(article.body) > len(existing.body):
            articles[article.article_id] = article
    return list(articles.values())


def _metadata_json(metadata: LawMetadata) -> str:
    return (
        json.dumps(metadata.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False)
        + "\n"
    )


def _sorted_file_changes(files: Mapping[str, str]) -> dict[str, str]:
    return {path: files[path] for path in sorted(files)}


def build_chronological_commits(
    parsed_law: ParsedLaw,
    amendment_dates: Mapping[str, str] | None = None,
    *,
    original_date: str | None = None,
    patches: Sequence[ArticlePatch] | None = None,
) -> list[LegislativeCommitPlan]:
    """Ayrıştırılmış yasayı kronolojik commit planlarına dönüştürür.

    Args:
        parsed_law: `parse_law_header` + `parse_articles` çıktısı.
        amendment_dates: `{"28/1974": "1974-12-15"}` biçiminde yürürlük tarihleri.
            Haritada bulunmayan bir referans için `YYYY-01-01` varsayılır.
        original_date: Başlıkta tarih yoksa kullanılacak ilk yürürlük günü.

    Returns:
        Kronolojik sıralı planlar; her biri `apply_to(engine)` ile uygulanabilir.

    Raises:
        ValueError: Yürürlük tarihi çözümlenemezse veya bir değişiklik ilk yürürlükten
            önceye düşerse.
    """
    header = parsed_law.header
    resolved_original = original_date or header.original_date
    if resolved_original is None:
        raise ValueError(
            f"{header.law_id} için ilk yürürlük tarihi bulunamadı; 'original_date' verin"
        )
    resolved_original = validate_iso_date(resolved_original)
    if not is_plausible_legal_date(resolved_original):
        raise ValueError(f"{header.law_id} için geçersiz ilk yürürlük tarihi: {resolved_original}")

    date_overrides: dict[str, str] = {}
    for reference, raw_date in (amendment_dates or {}).items():
        override_date = validate_iso_date(raw_date)
        if not is_plausible_legal_date(override_date):
            raise ValueError(
                f"{reference} için geçersiz değişiklik tarihi: {override_date}"
            )
        date_overrides[reference] = override_date

    affected: dict[str, set[str]] = {}
    for article in parsed_law.articles:
        for reference in article.amendments:
            affected.setdefault(reference, set()).add(article.article_id)

    timeline: list[tuple[str, str, list[str]]] = []
    for reference, article_ids in affected.items():
        number_raw, year_raw = reference.split("/", 1)
        effective = date_overrides.get(reference, f"{int(year_raw):04d}-01-01")
        if not is_plausible_legal_date(effective):
            continue
        if effective < resolved_original:
            # Konsolide belgelerde eski tarihli atıflar (örn. 25/1949) tarihsel
            # dipnot olabilir; ilk yürürlükten önceki kayıtları geçmişe ekleme.
            continue
        timeline.append((effective, reference, sorted(article_ids)))

    patches_by_date: dict[str, list[ArticlePatch]] = {}
    for patch in patches or []:
        if patch.effective_date is None:
            continue
        effective = validate_iso_date(patch.effective_date)
        if not is_plausible_legal_date(effective) or effective < resolved_original:
            continue
        patches_by_date.setdefault(effective, []).append(patch)
    for effective, patch_group in sorted(patches_by_date.items()):
        references = sorted(
            {patch.amending_law or "yama" for patch in patch_group}
        )
        article_ids = sorted({patch.target_article for patch in patch_group})
        timeline.append((effective, references[0], article_ids))

    def reference_order(reference: str) -> int:
        first = reference.split("/", 1)[0]
        return int(first) if first.isdigit() else 0

    timeline.sort(
        key=lambda item: (item[0], reference_order(item[1]), item[1])
    )

    law_prefix = f"laws/{header.law_id}"
    articles_by_id = {article.article_id: article for article in parsed_law.articles}
    current_articles = dict(articles_by_id)
    title = header.title or header.law_id

    root_files = {
        f"{law_prefix}/{article.article_id}.md": article.content
        for article in parsed_law.articles
    }
    root_files[f"{law_prefix}/metadata.json"] = _metadata_json(header.to_metadata([]))
    root_author = (
        COLONIAL_AUTHOR_NAME
        if header.type == "fasil" and resolved_original < "1960-08-16"
        else DEFAULT_AUTHOR_NAME
    )

    plans = [
        LegislativeCommitPlan(
            effective_date=resolved_original,
            author_name=root_author,
            commit_message=(
                f"{header.law_id}: {resolved_original} — ilk yürürlük ({title})"
            ),
            file_changes=_sorted_file_changes(root_files),
            law=f"{header.law_id} {header.title}".strip(),
            kind="baseline",
            tag_name=f"v{resolved_original}",
        )
    ]

    history: list[AmendmentRecord] = []
    applied_patch_dates: set[str] = set()
    for effective, reference, article_ids in timeline:
        if effective in patches_by_date and effective not in applied_patch_dates:
            patched = apply_article_patches(
                list(current_articles.values()),
                patches_by_date[effective],
            )
            current_articles = {article.article_id: article for article in patched}
            article_ids = sorted(
                set(article_ids)
                | {patch.target_article for patch in patches_by_date[effective]}
            )
            applied_patch_dates.add(effective)
        message = (
            f"{header.law_id}: {effective} — {reference} ({', '.join(article_ids)})"
        )
        history.append(
            AmendmentRecord(
                amending_law=reference,
                effective_date=effective,
                affected_articles=article_ids,
                commit_message=message,
            )
        )
        files = {
            f"{law_prefix}/metadata.json": _metadata_json(header.to_metadata(history))
        }
        for article_id in article_ids:
            article = current_articles.get(article_id)
            if article is not None:
                files[f"{law_prefix}/{article_id}.md"] = article.content
        plans.append(
            LegislativeCommitPlan(
                effective_date=effective,
                author_name=DEFAULT_AUTHOR_NAME,
                commit_message=message,
                file_changes=_sorted_file_changes(files),
                law=reference,
                kind="law",
                tag_name=f"v{effective}",
                amending_law=reference,
                affected_articles=article_ids,
            )
        )

    return plans
