"""KKTC mevzuat külliyatının Pydantic v2 veri modelleri.

Modeller, `lexifina/global-law` standardındaki `metadata.json` şemasını ve Faz 1'in
deterministik Git motorunun ihtiyaç duyduğu alanları birlikte kodlar. Tüm tarihler
`YYYY-MM-DD` (ISO 8601, UTC gün) biçimindedir; modeller çalışma zamanı zaman damgası
içermez, böylece aynı girdi her makinede aynı çıktıyı üretir.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from tools.git_engine import DeterministicGitEngine

__all__ = [
    "AmendmentRecord",
    "AmendmentReference",
    "ArticleVersion",
    "BuildSummary",
    "CacheManifest",
    "CachedDocument",
    "CorpusStatistics",
    "DateSource",
    "ArticleAmendmentStat",
    "ArticlePatch",
    "AuditFinding",
    "AuditSource",
    "SemanticHit",
    "UltraViresReport",
    "LawHeader",
    "LawIndexEntry",
    "LawMetadata",
    "LawType",
    "LegislativeCommitPlan",
    "ParsedArticle",
    "ParsedLaw",
    "SearchHit",
    "SkippedLaw",
    "normalize_article_id",
    "validate_iso_date",
]

LawType = Literal["fasil", "yasa", "anayasa"]
DateSource = Literal["explicit", "override", "fallback_1959", "fallback_law_year"]

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FASIL_SLUG_RE = re.compile(r"^fasil-\d+$")
_YASA_SLUG_RE = re.compile(r"^yasa-\d{4}-\d+$")
_ANAYASA_SLUG = "anayasa"

_ARTICLE_RE = re.compile(
    r"""
    ^
    (?:
        (?P<prefix>
            madde
            |
            ge[cç]ici(?:[\s_-]*madde)?
        )
        [\s_-]*
    )?
    (?P<number>\d+)
    $
    """,
    re.VERBOSE,
)


def validate_iso_date(value: str) -> str:
    """`YYYY-MM-DD` biçimindeki bir tarihi doğrular ve normalize edilmiş halini döndürür.

    Raises:
        ValueError: Biçim hatalıysa veya takvimde böyle bir gün yoksa.
    """
    if not isinstance(value, str):
        raise ValueError(f"Tarih metin olmalıdır, alınan: {type(value).__name__}")
    candidate = value.strip()
    if not _ISO_DATE_RE.match(candidate):
        raise ValueError(f"Tarih 'YYYY-MM-DD' biçiminde olmalıdır, alınan: {value!r}")
    try:
        date.fromisoformat(candidate)
    except ValueError as exc:  # 2024-02-30 gibi geçersiz takvim günleri
        raise ValueError(f"Geçersiz takvim tarihi: {value!r}") from exc
    return candidate


def normalize_article_id(article_id: str) -> str:
    """Madde kimliğini standart dosya adı gövdesine dönüştürür.

    Örnekler:
        >>> normalize_article_id("madde-4")
        'madde-004'
        >>> normalize_article_id("Madde 12")
        'madde-012'
        >>> normalize_article_id("2")
        'madde-002'
        >>> normalize_article_id("Geçici Madde 3")
        'gecici-madde-003'
        >>> normalize_article_id("gecici-1")
        'gecici-madde-001'
    """
    if not isinstance(article_id, str) or not article_id.strip():
        raise ValueError("Madde kimliği boş olamaz")
    raw = article_id.strip()
    if raw.lower().endswith(".md"):
        raw = raw[:-3]
    # Türkçe büyük/küçük harf katlaması: 'İ' casefold ile 'i' + birleşik nokta üretir.
    folded = raw.replace("İ", "i").casefold()
    match = _ARTICLE_RE.match(folded)
    if match is None:
        raise ValueError(f"Madde kimliği çözümlenemedi: {article_id!r}")
    prefix_raw = match.group("prefix")
    prefix = "gecici-madde" if prefix_raw and prefix_raw.startswith("ge") else "madde"
    return f"{prefix}-{int(match.group('number')):03d}"


class AmendmentRecord(BaseModel):
    """Konsolidasyon geçmişindeki tek bir değişiklik yasası kaydı."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    amending_law: str = Field(
        min_length=1,
        description="Değişiklik yasasının resmi referansı (örn. '41/2018').",
    )
    effective_date: str = Field(
        description="Değişikliğin yürürlüğe girdiği gün, 'YYYY-MM-DD'.",
    )
    affected_articles: list[str] = Field(
        default_factory=list,
        description="Değişiklikten etkilenen madde kimlikleri (örn. ['madde-004']).",
    )
    commit_message: str = Field(
        min_length=1,
        description="Deterministik Git commit mesajı.",
    )

    @field_validator("effective_date")
    @classmethod
    def _check_effective_date(cls, value: str) -> str:
        return validate_iso_date(value)

    @field_validator("amending_law")
    @classmethod
    def _check_amending_law(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("amending_law boş olamaz")
        return value

    @field_validator("affected_articles", mode="before")
    @classmethod
    def _coerce_affected_articles(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("affected_articles")
    @classmethod
    def _normalize_affected_articles(cls, value: list[str]) -> list[str]:
        normalized = [normalize_article_id(item) for item in value]
        # Aynı madde birden fazla kez listelenirse tekilleştir; sıra korunur.
        seen: set[str] = set()
        result: list[str] = []
        for item in normalized:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @property
    def sort_key(self) -> tuple[str, str]:
        """Kronolojik sıralama anahtarı: (yürürlük tarihi, değişiklik yasası)."""
        return (self.effective_date, self.amending_law)


class ArticleVersion(BaseModel):
    """Bir maddenin belirli bir yürürlük tarihindeki sürümü."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    article_id: str = Field(
        description="Standart madde kimliği (örn. 'madde-004', 'gecici-madde-001').",
    )
    title: str = Field(min_length=1, description="Madde başlığı (örn. 'Kısa İsim').")
    content: str = Field(description="Madde gövdesi; Markdown, LF satır sonlu.")
    effective_date: str = Field(description="Sürümün yürürlüğe girdiği gün, 'YYYY-MM-DD'.")
    amending_law: str | None = Field(
        default=None,
        description="Sürümü üreten değişiklik yasası; ilk yürürlük metninde None.",
    )

    @field_validator("article_id")
    @classmethod
    def _check_article_id(cls, value: str) -> str:
        return normalize_article_id(value)

    @field_validator("effective_date")
    @classmethod
    def _check_effective_date(cls, value: str) -> str:
        return validate_iso_date(value)

    @field_validator("content")
    @classmethod
    def _normalize_content(cls, value: str) -> str:
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in text.split("\n")]
        return "\n".join(lines).strip("\n")

    @field_validator("amending_law")
    @classmethod
    def _check_amending_law(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("amending_law None ya da dolu bir metin olmalıdır")
        return value

    def to_markdown(self) -> str:
        """Maddeyi PRD §4.3 standardındaki Markdown gövdesine dönüştürür."""
        label = "Geçici Madde" if self.article_id.startswith("gecici-madde") else "Madde"
        heading = f"# {label} {self.article_id_number}: {self.title}"
        return f"{heading}\n\n{self.content}\n"

    @property
    def article_id_number(self) -> int:
        """Madde kimliğindeki sayısal değeri döndürür."""
        return int(self.article_id.rsplit("-", 1)[-1])


class LawMetadata(BaseModel):
    """Bir yasa/fasıl dizininin `metadata.json` içeriği."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str = Field(min_length=1, description="Külliçi slug (örn. 'fasil-113').")
    type: LawType = Field(description="Mevzuat türü: fasil, yasa veya anayasa.")
    number: str = Field(min_length=1, description="Resmi numara (örn. '113', '42/2024').")
    title: str = Field(min_length=1, description="Yasanın resmi adı.")
    original_date: str = Field(description="İlk yürürlük tarihi, 'YYYY-MM-DD'.")
    date_source: DateSource = Field(
        default="explicit",
        description="Tarihin kaynağı: explicit, override, fallback_1959 veya fallback_law_year.",
    )
    consolidation_history: list[AmendmentRecord] = Field(default_factory=list)

    @field_validator("law_id")
    @classmethod
    def _check_law_id(cls, value: str) -> str:
        candidate = value.strip()
        if not (
            _FASIL_SLUG_RE.match(candidate)
            or _YASA_SLUG_RE.match(candidate)
            or candidate == _ANAYASA_SLUG
        ):
            raise ValueError(
                "law_id 'fasil-<sayı>', 'yasa-<YYYY>-<sayı>' veya 'anayasa' olmalıdır: "
                f"{value!r}"
            )
        return candidate

    @field_validator("original_date")
    @classmethod
    def _check_original_date(cls, value: str) -> str:
        return validate_iso_date(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> LawMetadata:
        if self.type == "fasil" and not _FASIL_SLUG_RE.match(self.law_id):
            raise ValueError(f"type='fasil' için law_id 'fasil-<sayı>' olmalıdır: {self.law_id!r}")
        if self.type == "yasa" and not _YASA_SLUG_RE.match(self.law_id):
            raise ValueError(
                f"type='yasa' için law_id 'yasa-<YYYY>-<sayı>' olmalıdır: {self.law_id!r}"
            )
        if self.type == "anayasa" and self.law_id != _ANAYASA_SLUG:
            raise ValueError(f"type='anayasa' için law_id 'anayasa' olmalıdır: {self.law_id!r}")

        for record in self.consolidation_history:
            if record.effective_date < self.original_date:
                raise ValueError(
                    "Değişiklik kaydı ilk yürürlük tarihinden önce olamaz: "
                    f"{record.amending_law} ({record.effective_date})"
                )
        return self

    @model_validator(mode="before")
    @classmethod
    def _sort_history(cls, data: Any) -> Any:
        """Konsolidasyon geçmişini kronolojik ve deterministik sıraya sokar."""
        if not isinstance(data, dict):
            return data
        history = data.get("consolidation_history")
        if not isinstance(history, list):
            return data
        sortable: list[tuple[tuple[str, str], Any]] = []
        for item in history:
            if isinstance(item, AmendmentRecord):
                key = item.sort_key
            elif isinstance(item, dict):
                raw_date = str(item.get("effective_date", ""))
                raw_law = str(item.get("amending_law", ""))
                key = (raw_date, raw_law)
            else:  # Şema dışı öğe: doğrulamayı pydantic'e bırak.
                return data
            sortable.append((key, item))
        ordered = [item for _, item in sorted(sortable, key=lambda pair: pair[0])]
        return {**data, "consolidation_history": ordered}

    def history_for(self, article_id: str) -> list[AmendmentRecord]:
        """Belirli bir maddeyi etkileyen değişiklik kayıtlarını kronolojik döndürür."""
        target = normalize_article_id(article_id)
        return [
            record
            for record in self.consolidation_history
            if target in record.affected_articles
        ]


_LAW_ID_RE = re.compile(r"^(?:fasil-\d+|yasa-\d{4}-\d+|anayasa)$")
_AMENDMENT_LAW_RE = re.compile(r"^\d{1,3}/\d{4}$")


class LawHeader(BaseModel):
    """Ayrıştırılan bir mevzuat metninin kimlik bilgileri (`metadata.json` öncesi)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str = Field(min_length=1)
    type: LawType
    number: str = Field(min_length=1)
    title: str = ""
    original_date: str | None = None
    date_source: DateSource = "explicit"

    @field_validator("law_id")
    @classmethod
    def _check_law_id(cls, value: str) -> str:
        candidate = value.strip()
        if not _LAW_ID_RE.match(candidate):
            raise ValueError(
                "law_id 'fasil-<sayı>', 'yasa-<YYYY>-<sayı>' veya 'anayasa' olmalıdır: "
                f"{value!r}"
            )
        return candidate

    @field_validator("original_date")
    @classmethod
    def _check_original_date(cls, value: str | None) -> str | None:
        return None if value is None else validate_iso_date(value)

    @model_validator(mode="after")
    def _check_type_consistency(self) -> LawHeader:
        if self.type == "fasil" and not self.law_id.startswith("fasil-"):
            raise ValueError(f"type='fasil' için law_id 'fasil-<sayı>' olmalıdır: {self.law_id!r}")
        if self.type == "yasa" and not _YASA_SLUG_RE.match(self.law_id):
            raise ValueError(
                f"type='yasa' için law_id 'yasa-<YYYY>-<sayı>' olmalıdır: {self.law_id!r}"
            )
        if self.type == "anayasa" and self.law_id != _ANAYASA_SLUG:
            raise ValueError(f"type='anayasa' için law_id 'anayasa' olmalıdır: {self.law_id!r}")
        return self

    def to_metadata(self, history: list[AmendmentRecord] | None = None) -> LawMetadata:
        """Başlık bilgisinden bir `LawMetadata` üretir; tarih yoksa hata verir."""
        if self.original_date is None:
            raise ValueError(f"original_date olmadan metadata üretilemez: {self.law_id}")
        return LawMetadata(
            law_id=self.law_id,
            type=self.type,
            number=self.number,
            title=self.title or self.law_id,
            original_date=self.original_date,
            date_source=self.date_source,
            consolidation_history=list(history or []),
        )


class AmendmentReference(BaseModel):
    """Bir metinde tespit edilen tek bir değişiklik yasası referansı."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: str = Field(description="Normalleştirilmiş 'SAYI/YIL' referansı (örn. '34/2019').")
    number: str = Field(min_length=1, description="Değişiklik yasasının sayısı (örn. '34').")
    year: int = Field(ge=1800, le=2100, description="Değişiklik yasasının 4 haneli yılı.")
    article_ref: str | None = Field(
        default=None,
        description="Referansın bağlı olduğu madde/fıkra (örn. '2(a)', '4(1)(b)').",
    )
    raw: str = Field(min_length=1, description="Metinde geçen ham ifade.")
    position: int = Field(default=0, ge=0, description="Metindeki karakter konumu.")

    @model_validator(mode="after")
    def _check_consistency(self) -> AmendmentReference:
        expected = f"{self.number}/{self.year}"
        if self.reference != expected:
            raise ValueError(
                f"reference '{self.reference}' ile number/year '{expected}' uyuşmuyor"
            )
        if not _AMENDMENT_LAW_RE.match(self.reference):
            raise ValueError(f"Geçersiz değişiklik yasası referansı: {self.reference!r}")
        return self

    @property
    def sort_key(self) -> tuple[int, int, int]:
        """Kronolojik sıralama anahtarı: (yıl, sayı, metin konumu)."""
        return (self.year, int(self.number), self.position)


class ParsedArticle(BaseModel):
    """Ayrıştırılmış tek bir madde (konsolide metin)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    article_id: str
    title: str = ""
    body: str = ""
    amendments: list[str] = Field(default_factory=list)
    is_repealed: bool = False

    @field_validator("article_id")
    @classmethod
    def _check_article_id(cls, value: str) -> str:
        return normalize_article_id(value)

    @field_validator("body")
    @classmethod
    def _normalize_body(cls, value: str) -> str:
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in text.split("\n")]
        return "\n".join(lines).strip("\n")

    @field_validator("amendments")
    @classmethod
    def _check_amendments(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for reference in value:
            candidate = reference.strip()
            if not _AMENDMENT_LAW_RE.match(candidate):
                raise ValueError(f"Geçersiz değişiklik referansı: {reference!r}")
            if candidate not in seen:
                seen.add(candidate)
                result.append(candidate)
        return result

    @property
    def number(self) -> int:
        """Madde kimliğindeki sayı (örn. 'madde-004' -> 4)."""
        return int(self.article_id.rsplit("-", 1)[-1])

    @property
    def is_temporary(self) -> bool:
        """Maddenin geçici madde olup olmadığını döndürür."""
        return self.article_id.startswith("gecici-madde-")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content(self) -> str:
        """PRD §4.3 standardındaki Markdown madde gövdesi."""
        label = "Geçici Madde" if self.is_temporary else "Madde"
        heading = f"# {label} {self.number}"
        if self.title:
            heading = f"{heading}: {self.title}"
        if not self.body:
            return f"{heading}\n"
        return f"{heading}\n\n{self.body}\n"


class ParsedLaw(BaseModel):
    """Bir mevzuat dosyasının ayrıştırılmış bütünü."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    header: LawHeader
    articles: list[ParsedArticle]

    @model_validator(mode="after")
    def _check_unique_articles(self) -> ParsedLaw:
        seen: set[str] = set()
        for article in self.articles:
            if article.article_id in seen:
                raise ValueError(f"Yinelenen madde kimliği: {article.article_id}")
            seen.add(article.article_id)
        return self

    @property
    def law_id(self) -> str:
        """Mevzuat slug'ı (örn. 'fasil-113')."""
        return self.header.law_id

    @property
    def title(self) -> str:
        """Mevzuat başlığı."""
        return self.header.title

    def article(self, article_id: str) -> ParsedArticle | None:
        """Kimliğe göre maddeyi döndürür."""
        target = normalize_article_id(article_id)
        return next((item for item in self.articles if item.article_id == target), None)


class LegislativeCommitPlan(BaseModel):
    """`DeterministicGitEngine.apply_legislative_commit` çağrısına hazır tek bir commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    effective_date: str
    author_name: str = Field(min_length=1)
    commit_message: str = Field(min_length=1)
    file_changes: dict[str, str]
    tag_name: str | None = None
    amending_law: str | None = None
    affected_articles: list[str] = Field(default_factory=list)

    @field_validator("effective_date")
    @classmethod
    def _check_effective_date(cls, value: str) -> str:
        return validate_iso_date(value)

    @field_validator("tag_name")
    @classmethod
    def _check_tag_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if not re.match(r"^v\d{4}-\d{2}-\d{2}$", candidate):
            raise ValueError(f"Tag adı 'vYYYY-MM-DD' biçiminde olmalıdır: {value!r}")
        validate_iso_date(candidate[1:])
        return candidate

    @field_validator("affected_articles")
    @classmethod
    def _check_affected(cls, value: list[str]) -> list[str]:
        return [normalize_article_id(item) for item in value]

    def apply_to(self, engine: DeterministicGitEngine) -> str:
        """Planı verilen motora uygular ve oluşan commit SHA-1'ini döndürür."""
        return engine.apply_legislative_commit(
            effective_date=self.effective_date,
            author_name=self.author_name,
            commit_message=self.commit_message,
            file_changes=dict(self.file_changes),
            tag_name=self.tag_name,
        )


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_HEX_RE = re.compile(r"^[0-9a-f]{40}$")


class LawIndexEntry(BaseModel):
    """`mahkemeler.net` fihristinde tespit edilen tek bir mevzuat bağlantısı."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    category: LawType
    number: str | None = None

    @field_validator("law_id")
    @classmethod
    def _check_law_id(cls, value: str) -> str:
        candidate = value.strip()
        if not _LAW_ID_RE.match(candidate):
            raise ValueError(
                "law_id 'fasil-<sayı>', 'yasa-<YYYY>-<sayı>' veya 'anayasa' olmalıdır: "
                f"{value!r}"
            )
        return candidate

    @field_validator("number")
    @classmethod
    def _check_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        return candidate or None

    @model_validator(mode="after")
    def _check_category_consistency(self) -> LawIndexEntry:
        if self.category == "fasil":
            if not _FASIL_SLUG_RE.match(self.law_id):
                raise ValueError(f"category='fasil' için geçersiz law_id: {self.law_id!r}")
            if self.number is not None and self.number != self.law_id.split("-", 1)[1]:
                raise ValueError(
                    f"number '{self.number}' ile law_id '{self.law_id}' uyuşmuyor"
                )
        elif self.category == "yasa":
            if not _YASA_SLUG_RE.match(self.law_id):
                raise ValueError(f"category='yasa' için geçersiz law_id: {self.law_id!r}")
            if self.number is not None:
                _, year, number = self.law_id.split("-")
                expected = f"{number}/{year}"
                if self.number != expected:
                    raise ValueError(
                        f"number '{self.number}' ile law_id '{self.law_id}' uyuşmuyor"
                    )
        else:
            if self.law_id != _ANAYASA_SLUG:
                raise ValueError(
                    f"category='anayasa' için law_id 'anayasa' olmalıdır: {self.law_id!r}"
                )
            if self.number is not None:
                raise ValueError("Anayasa girdisinde number None olmalıdır")
        return self


class CachedDocument(BaseModel):
    """`raw_cache/` içine kaydedilen ham bir belgenin manifest kaydı."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str
    url: str = Field(min_length=1)
    sha256: str
    encoding: str | None = None
    raw_path: str = Field(min_length=1)
    content_length: int = Field(ge=0)

    @field_validator("law_id")
    @classmethod
    def _check_law_id(cls, value: str) -> str:
        candidate = value.strip()
        if not _LAW_ID_RE.match(candidate):
            raise ValueError(f"Geçersiz law_id: {value!r}")
        return candidate

    @field_validator("sha256")
    @classmethod
    def _check_sha256(cls, value: str) -> str:
        candidate = value.strip().lower()
        if not _SHA256_HEX_RE.match(candidate):
            raise ValueError(f"SHA-256 64 hex karakter olmalıdır: {value!r}")
        return candidate

    @field_validator("encoding")
    @classmethod
    def _check_encoding(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        return candidate or None

    @field_validator("raw_path")
    @classmethod
    def _check_raw_path(cls, value: str) -> str:
        candidate = value.strip().replace("\\", "/")
        path = PurePosixPath(candidate)
        if not candidate or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"raw_path cache dizinine göre göreli olmalıdır: {value!r}")
        return path.as_posix()


class CacheManifest(BaseModel):
    """`raw_cache/manifest.json` kök nesnesi; anahtarlar `law_id`'dir."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, CachedDocument] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_keys(self) -> CacheManifest:
        for key, document in self.entries.items():
            if key != document.law_id:
                raise ValueError(
                    f"Manifest anahtarı '{key}' ile law_id '{document.law_id}' uyuşmuyor"
                )
        return self

    @property
    def sorted_entries(self) -> list[CachedDocument]:
        """`law_id`'ye göre alfabetik sıralı belge listesi."""
        return [self.entries[key] for key in sorted(self.entries)]

    def get(self, law_id: str) -> CachedDocument | None:
        """Anahtara göre önbellek kaydını döndürür."""
        return self.entries.get(law_id)

    def upsert(self, document: CachedDocument) -> None:
        """Belge kaydını ekler veya günceller."""
        self.entries[document.law_id] = document


class SkippedLaw(BaseModel):
    """İnşa sırasında atlanan yasa ve atlanma nedeni."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class LawAmendmentStat(BaseModel):
    """En çok değişiklik geçiren yasa satırı."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str
    title: str
    amendment_count: int = Field(ge=0)


class ArticleAmendmentStat(BaseModel):
    """En çok değişiklik geçiren madde satırı."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str
    article_id: str
    title: str
    amendment_count: int = Field(ge=0)


class ArticlePatch(BaseModel):
    """Değişiklik yasasından çıkarılan tek bir madde yaması."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: Literal["replace_article", "replace_text", "repeal_article"]
    target_article: str
    new_content: str = ""
    old_text: str = ""
    new_text: str = ""
    clause: str | None = None
    effective_date: str | None = None
    amending_law: str | None = None
    law_id: str | None = None

    @field_validator("target_article")
    @classmethod
    def _check_target_article(cls, value: str) -> str:
        return normalize_article_id(value)

    @field_validator("effective_date")
    @classmethod
    def _check_effective_date(cls, value: str | None) -> str | None:
        return None if value is None else validate_iso_date(value)


class SemanticHit(BaseModel):
    """Yerel FTS5/BM25 hibrit aramasında dönen doğrulanmış madde."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str
    article_id: str
    title: str = ""
    snippet: str = ""
    score: float
    commit_sha: str
    effective_date: str


class AuditSource(BaseModel):
    """Ultra Vires denetiminde kullanılan Git snapshot kaynağı."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str
    article_id: str
    title: str = ""
    norm_level: int = Field(ge=1, le=3)
    content: str
    commit_sha: str
    effective_date: str


class AuditFinding(BaseModel):
    """Ultra Vires denetiminde tespit edilen deterministik bulgu."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    severity: Literal["high", "medium", "low"]
    code: str
    message: str
    law_id: str | None = None
    article_id: str | None = None


class UltraViresReport(BaseModel):
    """Ultra Vires denetim raporu."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of_date: str
    snapshot_commit: str
    subject_level: str
    findings: list[AuditFinding] = Field(default_factory=list)
    sources: list[AuditSource] = Field(default_factory=list)


class CorpusStatistics(BaseModel):
    """Külliyatın deterministik istatistik ve sağlık özeti."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_laws: int = Field(ge=0)
    law_type_counts: dict[str, int] = Field(default_factory=dict)
    total_articles: int = Field(ge=0)
    active_articles: int = Field(ge=0)
    repealed_articles: int = Field(ge=0)
    total_commits: int = Field(ge=0)
    min_effective_date: str | None = None
    max_effective_date: str | None = None
    date_source_counts: dict[str, int] = Field(default_factory=dict)
    top_laws: list[LawAmendmentStat] = Field(default_factory=list)
    top_articles: list[ArticleAmendmentStat] = Field(default_factory=list)


class BuildSummary(BaseModel):
    """Bir külliyat inşasının deterministik özeti."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    law_count: int = Field(ge=0)
    article_count: int = Field(ge=0)
    commit_count: int = Field(ge=0)
    head_sha: str
    skipped_laws: list[SkippedLaw] = Field(default_factory=list)

    @field_validator("head_sha")
    @classmethod
    def _check_head_sha(cls, value: str) -> str:
        candidate = value.strip().lower()
        if not _SHA1_HEX_RE.match(candidate):
            raise ValueError(f"HEAD SHA-1 40 hex karakter olmalıdır: {value!r}")
        return candidate



class SearchHit(BaseModel):
    """Tarihsel mevzuat aramasında eşleşen tek bir satır."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    law_id: str
    article_id: str
    line_number: int = Field(ge=1)
    snippet: str = Field(min_length=1)

    @field_validator("law_id")
    @classmethod
    def _check_law_id(cls, value: str) -> str:
        candidate = value.strip()
        if not _LAW_ID_RE.match(candidate):
            raise ValueError(f"Geçersiz law_id: {value!r}")
        return candidate

    @field_validator("article_id")
    @classmethod
    def _check_article_id(cls, value: str) -> str:
        return normalize_article_id(value)
