"""Uçtan uca külliyat inşa motoru: kaynaklar → parser → küresel Git zaman çizelgesi.

Boru hattı iki aşamalıdır:

1. Her mevzuat belgesi bağımsız olarak ayrıştırılır ve `build_chronological_commits`
   ile kendi kronolojik planı üretilir.
2. `merge_commit_plans` farklı yasalardan gelen planları `(effective_date, law_id,
   amending_law)` anahtarıyla tek bir monoton zaman çizelgesinde birleştirir. Aynı güne
   düşen commit'lerde yalnızca o günün **son** commit'i `vYYYY-MM-DD` etiketini taşır;
   böylece Git tag çakışması oluşmaz.

`CorpusBuilder` hem `raw_cache/` (manifest doğrulamalı) hem de ham HTML klasöründen
çalışabilir; sonuç `BuildSummary` ile deterministik olarak raporlanır.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Final

import typer

from tools.amendment_patcher import load_article_patches
from tools.doc_converter import DocumentConversionError, convert_document_to_html
from tools.git_engine import DeterministicGitEngine
from tools.models import (
    ArticlePatch,
    BuildSummary,
    LawHeader,
    LegislativeCommitPlan,
    ParsedLaw,
    SkippedLaw,
    validate_iso_date,
)
from tools.parser import (
    build_chronological_commits,
    law_header_from_id,
    load_law_date_overrides,
    parse_articles,
    parse_law_header,
)
from tools.scraper import DEFAULT_INDEX_URL, MahkemelerScraper, load_cache_manifest
from tools.stats import render_stats_markdown, statistics_from_parsed_laws

__all__ = [
    "CorpusBuilder",
    "DEFAULT_AMENDMENT_DATES_PATH",
    "load_amendment_dates",
    "merge_commit_plans",
    "render_corpus_json",
]

logger = logging.getLogger(__name__)

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_AMENDMENT_DATES_PATH: Final[Path] = PROJECT_ROOT / "data" / "amendment_dates.json"
DEFAULT_CORPUS_PATH: Final[Path] = PROJECT_ROOT / "corpus.json"
DEFAULT_REPRODUCE_PATH: Final[Path] = PROJECT_ROOT / "REPRODUCE.md"

SOURCE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".htm", ".html", ".doc", ".docx", ".rtf", ".pdf"}
)
_AMENDMENT_REF_RE: Final[re.Pattern[str]] = re.compile(r"^\d{1,3}/\d{4}$")


def _fold_query(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").casefold()



def _normalize_amendment_dates(mapping: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_reference, raw_date in mapping.items():
        reference = raw_reference.strip()
        if not _AMENDMENT_REF_RE.match(reference):
            raise ValueError(f"Geçersiz değişiklik yasası referansı: {raw_reference!r}")
        normalized[reference] = validate_iso_date(raw_date)
    return {reference: normalized[reference] for reference in sorted(normalized)}


def load_amendment_dates(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Değişiklik yasası yürürlük tarihleri JSON haritasını yükler.

    Varsayılan kaynak `data/amendment_dates.json` dosyasıdır. Haritada bulunmayan
    referanslar için parser `YYYY-01-01` varsayımını uygular.
    """
    source = Path(path) if path is not None else DEFAULT_AMENDMENT_DATES_PATH
    if not source.is_file():
        raise FileNotFoundError(f"Değişiklik tarihleri dosyası bulunamadı: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Değişiklik tarihleri bir JSON nesnesi olmalıdır: {source}")
    mapping = {str(key): str(value) for key, value in payload.items()}
    return _normalize_amendment_dates(mapping)


def _law_id_from_plan(plan: LegislativeCommitPlan) -> str:
    for path in sorted(plan.file_changes):
        parts = PurePosixPath(path.replace("\\", "/")).parts
        if len(parts) >= 3 and parts[0] == "laws":
            return parts[1]
    raise ValueError(
        "Plan dosya yollarından law_id çıkarılamadı: "
        f"{plan.commit_message!r}"
    )


def merge_commit_plans(
    plans_by_law: Sequence[Sequence[LegislativeCommitPlan]],
) -> list[LegislativeCommitPlan]:
    """Farklı yasaların planlarını monoton artan tek bir zaman çizelgesinde birleştirir.

    Sıralama anahtarı `(effective_date, law_id, amending_law or "")` olup eşitlik
    durumunda commit mesajı ve dosya yolları ile deterministik toplam sıra garanti edilir.

    Aynı `effective_date` için birden fazla commit varsa `vYYYY-MM-DD` etiketi o günün
    **son** commit'ine taşınır; diğer commit'lerin etiketi kaldırılır. Böylece aynı tag
    birden fazla kez oluşturulmaz.
    """
    keyed: list[tuple[str, LegislativeCommitPlan]] = []
    for group in plans_by_law:
        for plan in group:
            keyed.append((_law_id_from_plan(plan), plan))

    keyed.sort(
        key=lambda item: (
            item[1].effective_date,
            item[0],
            item[1].amending_law or "",
            item[1].commit_message,
            tuple(sorted(item[1].file_changes)),
        )
    )

    tags_by_date: dict[str, str] = {}
    last_index_by_date: dict[str, int] = {}
    for index, (_, plan) in enumerate(keyed):
        last_index_by_date[plan.effective_date] = index
        if plan.tag_name:
            tags_by_date[plan.effective_date] = plan.tag_name

    merged: list[LegislativeCommitPlan] = []
    for index, (_, plan) in enumerate(keyed):
        tag_name = tags_by_date.get(plan.effective_date)
        if last_index_by_date[plan.effective_date] != index:
            tag_name = None
        if tag_name == plan.tag_name:
            merged.append(plan)
        else:
            merged.append(plan.model_copy(update={"tag_name": tag_name}))
    return merged


def _checked_on_from(corpus_text: str) -> str:
    """Kök `corpus.json` metninden `checked_on` tarihini okur; yoksa/geçersizse hata verir."""
    try:
        payload = json.loads(corpus_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"corpus.json geçerli JSON değil: {exc}") from exc
    raw = payload.get("checked_on") if isinstance(payload, dict) else None
    if not isinstance(raw, str):
        raise ValueError("corpus.json içinde 'checked_on' bulunamadı; --cutoff verin")
    return validate_iso_date(raw)


def render_corpus_json(corpus_text: str, *, cutoff: str, has_future_branch: bool) -> str:
    """Kök `corpus.json` alanlarını koruyup `checked_on` ve `has_future_branch` değerlerini yazar."""
    payload = json.loads(corpus_text)
    if not isinstance(payload, dict):
        raise ValueError("corpus.json bir JSON nesnesi olmalıdır")
    payload["checked_on"] = validate_iso_date(cutoff)
    payload["has_future_branch"] = has_future_branch
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _read_required_text(path: str | os.PathLike[str], label: str) -> str:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"{label} bulunamadı: {source}")
    return source.read_text(encoding="utf-8")


class CorpusBuilder:
    """Birden fazla mevzuat belgesini tek bir deterministik Git külliyatına dönüştürür."""

    def __init__(
        self,
        repo_path: str | os.PathLike[str],
        *,
        amendment_dates: Mapping[str, str] | None = None,
        amendment_dates_path: str | os.PathLike[str] | None = None,
        corpus_path: str | os.PathLike[str] | None = None,
        reproduce_path: str | os.PathLike[str] | None = None,
        law_date_overrides: Mapping[str, str] | None = None,
        law_dates_override_path: str | os.PathLike[str] | None = None,
        patches: Sequence[ArticlePatch] | None = None,
        patches_path: str | os.PathLike[str] | None = None,
        limit: int | None = None,
        matches: Sequence[str] | None = None,
        strict: bool = False,
        cutoff: str | None = None,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit en az 1 olmalıdır")
        self.repo_path = Path(repo_path).expanduser()
        if amendment_dates is not None:
            self.amendment_dates = _normalize_amendment_dates(amendment_dates)
        else:
            self.amendment_dates = load_amendment_dates(amendment_dates_path)
        self.law_date_overrides = (
            {
                str(law_id): validate_iso_date(str(raw_date))
                for law_id, raw_date in law_date_overrides.items()
            }
            if law_date_overrides is not None
            else load_law_date_overrides(law_dates_override_path)
        )
        self.patches = (
            list(patches)
            if patches is not None
            else load_article_patches(
                patches_path or (PROJECT_ROOT / "data" / "amendment_patches.json")
            )
        )
        self.corpus_text = _read_required_text(corpus_path or DEFAULT_CORPUS_PATH, "corpus.json")
        self.reproduce_text = _read_required_text(
            reproduce_path or DEFAULT_REPRODUCE_PATH, "REPRODUCE.md"
        )
        self.cutoff = (
            validate_iso_date(cutoff) if cutoff is not None else _checked_on_from(self.corpus_text)
        )
        self.limit = limit
        self.matches = [item.strip() for item in (matches or []) if item.strip()]
        self.strict = strict

    def build_from_dir(self, source_dir: str | os.PathLike[str]) -> BuildSummary:
        """Bir klasördeki tüm `.html`/`.htm` belgelerinden külliyat inşa eder."""
        directory = Path(source_dir)
        if not directory.is_dir():
            raise NotADirectoryError(f"Kaynak dizin bulunamadı: {directory}")
        files = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
        )
        if not files:
            raise ValueError(f"Kaynak dizinde HTML/HTM belgesi yok: {directory}")
        return self._build(
            [(None, path.read_bytes(), path.suffix.lower()) for path in files]
        )

    def build_from_cache(self, cache_dir: str | os.PathLike[str]) -> BuildSummary:
        """`raw_cache/` dizininden, manifest SHA-256 doğrulamasıyla inşa eder."""
        directory = Path(cache_dir)
        manifest = load_cache_manifest(directory)
        if not manifest.entries:
            return self.build_from_dir(directory)

        documents: list[tuple[str, bytes, str]] = []
        for document in manifest.sorted_entries:
            path = directory / document.raw_path
            if not path.is_file():
                raise FileNotFoundError(
                    f"{document.law_id} için önbellek dosyası bulunamadı: {path}"
                )
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != document.sha256:
                raise ValueError(
                    f"{document.law_id} önbellek dosyası bozulmuş: "
                    f"beklenen {document.sha256}, bulunan {digest}"
                )
            documents.append((document.law_id, data, path.suffix.lower()))
        return self._build(documents)

    def _should_include(self, law_id: str, title: str) -> bool:
        if not self.matches:
            return True
        haystack = f"{_fold_query(law_id)}\n{_fold_query(title)}"
        return any(_fold_query(pattern) in haystack for pattern in self.matches)

    @staticmethod
    def _record_skip(
        skipped: list[SkippedLaw], law_id: str | None, reason: str
    ) -> None:
        normalized_reason = " ".join(str(reason).split()) or "bilinmeyen hata"
        label = (law_id or "bilinmeyen").strip() or "bilinmeyen"
        skipped.append(SkippedLaw(law_id=label, reason=normalized_reason))
        logger.warning("Yasa atlandı (%s): %s", label, normalized_reason)

    @staticmethod
    def _cross_link_amendment_dates(laws: Sequence[ParsedLaw]) -> dict[str, str]:
        """Külliyattaki yasaların açık tarihlerini değişiklik tarihleriyle eşleştirir."""
        cross_linked: dict[str, str] = {}
        for law in sorted(laws, key=lambda item: item.header.law_id):
            header = law.header
            if (
                header.type == "yasa"
                and header.date_source in {"explicit", "override"}
                and header.original_date is not None
            ):
                cross_linked.setdefault(header.number, header.original_date)
        return cross_linked

    def _build(
        self,
        documents: Sequence[tuple[str | None, bytes, str | None]],
    ) -> BuildSummary:
        skipped: list[SkippedLaw] = []
        candidates: list[tuple[LawHeader, bytes]] = []

        for source_law_id, raw, suffix in documents:
            label = source_law_id or "bilinmeyen"
            try:
                html = convert_document_to_html(raw, suffix or "")
            except (DocumentConversionError, OSError) as exc:
                if self.strict:
                    raise
                self._record_skip(skipped, label, f"belge dönüştürme: {exc}")
                continue

            try:
                header = parse_law_header(html, date_overrides=self.law_date_overrides)
            except Exception as exc:
                if source_law_id is None:
                    if self.strict:
                        raise
                    self._record_skip(skipped, label, f"başlık ayrıştırma: {exc}")
                    continue
                try:
                    header = law_header_from_id(
                        source_law_id,
                        html,
                        date_overrides=self.law_date_overrides,
                    )
                except Exception as fallback_exc:
                    if self.strict:
                        raise
                    self._record_skip(
                        skipped,
                        label,
                        f"başlık ayrıştırma: {fallback_exc}",
                    )
                    continue
                logger.warning(
                    "%s başlığı belgeden çözümlenemedi; önbellek kimliği kullanıldı: %s",
                    source_law_id,
                    exc,
                )

            if source_law_id is not None and source_law_id != header.law_id:
                logger.warning(
                    "Önbellek anahtarı '%s' ile belge başlığı '%s' uyuşmuyor; "
                    "önbellek kimliği kullanılıyor.",
                    source_law_id,
                    header.law_id,
                )
                try:
                    header = law_header_from_id(
                        source_law_id,
                        html,
                        date_overrides=self.law_date_overrides,
                    )
                except Exception as exc:
                    if self.strict:
                        raise
                    self._record_skip(skipped, source_law_id, f"başlık ayrıştırma: {exc}")
                    continue

            if not self._should_include(header.law_id, header.title):
                continue
            candidates.append((header, html))

        candidates.sort(key=lambda pair: pair[0].law_id)
        unique_candidates: list[tuple[LawHeader, bytes]] = []
        seen: set[str] = set()
        for header, html in candidates:
            law_id = header.law_id
            if law_id in seen:
                reason = f"yinelenen mevzuat kimliği: {law_id}"
                if self.strict:
                    raise ValueError(reason)
                self._record_skip(skipped, law_id, reason)
                continue
            seen.add(law_id)
            unique_candidates.append((header, html))
        if self.limit is not None:
            unique_candidates = unique_candidates[: self.limit]
        if not unique_candidates:
            raise ValueError("Filtreyle eşleşen yasa bulunamadı")

        parsed_laws: list[ParsedLaw] = []
        for header, html in unique_candidates:
            law_id = header.law_id
            try:
                articles = parse_articles(html)
                if not articles:
                    raise ValueError(f"{law_id} için hiç madde ayrıştırılamadı")
                parsed_laws.append(ParsedLaw(header=header, articles=articles))
            except Exception as exc:
                if self.strict:
                    raise
                self._record_skip(skipped, law_id, f"madde ayrıştırma: {exc}")
                continue
        if not parsed_laws:
            raise ValueError("Hiçbir yasa için madde ayrıştırılamadı")

        effective_amendment_dates = self._cross_link_amendment_dates(parsed_laws)
        effective_amendment_dates.update(self.amendment_dates)
        plans_by_law: list[list[LegislativeCommitPlan]] = []
        built_laws: list[ParsedLaw] = []
        article_count = 0
        for law in parsed_laws:
            try:
                law_patches = [
                    patch
                    for patch in self.patches
                    if patch.law_id is None or patch.law_id == law.header.law_id
                ]
                plans = build_chronological_commits(
                    law,
                    effective_amendment_dates,
                    patches=law_patches,
                )
            except Exception as exc:
                if self.strict:
                    raise
                self._record_skip(
                    skipped,
                    law.header.law_id,
                    f"commit planı: {exc}",
                )
                continue
            plans_by_law.append(plans)
            built_laws.append(law)
            article_count += len(law.articles)

        if not plans_by_law:
            raise ValueError("Hiçbir yasa için commit planı üretilemedi")

        merged = merge_commit_plans(plans_by_law)
        if not merged:
            raise ValueError("Uygulanacak commit planı üretilemedi")

        cutoff = self.cutoff
        in_force = [plan for plan in merged if plan.effective_date <= cutoff]
        if not in_force:
            raise ValueError(f"{cutoff} tarihinde yürürlükte olan hiçbir yasa yok")
        has_future = len(in_force) < len(merged)

        # Kök commit `main` ve `future` tarafından paylaşılır; bu yüzden istatistikler
        # yalnızca kesim tarihinde yürürlükte olan planlardan ve yasalardan hesaplanır.
        stat_laws = [
            law
            for law in built_laws
            if law.header.original_date is None or law.header.original_date <= cutoff
        ]
        stats = statistics_from_parsed_laws(
            [law.header for law in stat_laws],
            stat_laws,
            [plan.effective_date for plan in in_force],
        )
        first = merged[0]
        root_files = dict(first.file_changes)
        root_files["corpus.json"] = render_corpus_json(
            self.corpus_text, cutoff=cutoff, has_future_branch=has_future
        )
        root_files["REPRODUCE.md"] = self.reproduce_text
        root_files["STATS.md"] = render_stats_markdown(stats)
        merged[0] = first.model_copy(
            update={
                "file_changes": {
                    path: root_files[path] for path in sorted(root_files)
                }
            }
        )

        engine = DeterministicGitEngine(self.repo_path)
        if engine.has_commits():
            raise ValueError(
                f"Hedef repo zaten commit içeriyor: {self.repo_path}. "
                "Sıfırdan inşa için boş bir dizin kullanın."
            )

        for plan in merged:
            plan.apply_to(engine)
        future_sha = engine.finalize_branches(cutoff)

        skipped.sort(key=lambda item: (item.law_id, item.reason))
        return BuildSummary(
            law_count=len(plans_by_law),
            article_count=article_count,
            commit_count=len(merged),
            head_sha=engine.head(),
            cutoff=cutoff,
            future_sha=future_sha,
            skipped_laws=skipped,
        )


app = typer.Typer(
    add_completion=False,
    help="KKTC mevzuatını ham kaynaklardan deterministik bir Git külliyatına dönüştürür.",
)


def _scrape_into(
    cache_dir: Path,
    index_url: str,
    *,
    limit: int | None = None,
    matches: Sequence[str] | None = None,
    strict: bool = False,
) -> None:
    """Fihristi tarayıp `cache_dir` altındaki ham önbelleği doldurur."""

    async def run() -> None:
        async with MahkemelerScraper(
            cache_dir, index_url=index_url, strict=strict
        ) as scraper:
            await scraper.scrape(limit=limit, matches=matches)
            for skipped in scraper.skipped:
                logger.warning(
                    "İndirilemeyen belge (%s): %s",
                    skipped["law_id"],
                    skipped["reason"],
                )

    asyncio.run(run())


@app.command()
def build(
    repo_path: Annotated[
        Path,
        typer.Option("--repo-path", help="Hedef Git deposu dizini."),
    ],
    from_cache: Annotated[
        Path | None,
        typer.Option("--from-cache", help="SHA-256 manifestli raw_cache dizini."),
    ] = None,
    from_dir: Annotated[
        Path | None,
        typer.Option("--from-dir", help="Ham HTML/HTM belgelerini içeren kaynak dizin."),
    ] = None,
    scrape: Annotated[
        bool,
        typer.Option(
            "--scrape",
            help="İnşa öncesi fihristi tarayıp --from-cache dizinini doldurur.",
        ),
    ] = False,
    scrape_only: Annotated[
        bool,
        typer.Option(
            "--scrape-only",
            help="Yalnızca fihristi tarayıp önbelleği doldurur; külliyat inşa etmez.",
        ),
    ] = False,
    index_url: Annotated[
        str,
        typer.Option("--index-url", help="--scrape için mahkemeler.net fihrist adresi."),
    ] = DEFAULT_INDEX_URL,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="En fazla işlenecek/indirilecek yasa sayısı."),
    ] = None,
    match: Annotated[
        list[str] | None,
        typer.Option(
            "--match",
            help="Yasa kimliği veya başlığında aranacak metin; birden fazla verilebilir.",
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Atlanabilir hatalarda devam etmek yerine hemen hata ver.",
        ),
    ] = False,
    amendment_dates: Annotated[
        Path | None,
        typer.Option("--amendment-dates", help="Değişiklik yürürlük tarihleri JSON dosyası."),
    ] = None,
    law_dates_override: Annotated[
        Path | None,
        typer.Option(
            "--law-dates-override",
            help="Yasa ilk yürürlük tarihleri override JSON dosyası.",
        ),
    ] = None,
    amendment_patches: Annotated[
        Path | None,
        typer.Option(
            "--amendment-patches",
            help="Gerçek metin yamalarını içeren JSON dosyası.",
        ),
    ] = None,
    corpus_json: Annotated[
        Path | None,
        typer.Option("--corpus-json", help="Kök corpus.json dosyası."),
    ] = None,
    reproduce: Annotated[
        Path | None,
        typer.Option("--reproduce", help="Kök REPRODUCE.md dosyası."),
    ] = None,
    cutoff: Annotated[
        str | None,
        typer.Option(
            "--cutoff",
            help="Kesim tarihi (YYYY-MM-DD); verilmezse corpus.json 'checked_on' kullanılır.",
        ),
    ] = None,
) -> None:
    """Kaynaklardan külliyatı inşa eder ve deterministik özeti JSON olarak yazar."""
    if (from_cache is None) == (from_dir is None):
        raise typer.BadParameter(
            "--from-cache veya --from-dir seçeneklerinden tam olarak biri verilmelidir."
        )
    if limit is not None and limit < 1:
        raise typer.BadParameter("--limit en az 1 olmalıdır")
    if scrape_only and from_cache is None:
        raise typer.BadParameter("--scrape-only yalnızca --from-cache ile kullanılabilir.")
    if scrape or scrape_only:
        if from_cache is None:
            raise typer.BadParameter(
                "--scrape/--scrape-only yalnızca --from-cache ile birlikte kullanılabilir."
            )
        _scrape_into(
            from_cache,
            index_url,
            limit=limit,
            matches=match,
            strict=strict,
        )
    if scrape_only:
        manifest = load_cache_manifest(from_cache)
        typer.echo(
            json.dumps(
                {"cached_documents": len(manifest.entries)},
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        return

    if cutoff is not None:
        try:
            cutoff = validate_iso_date(cutoff)
        except ValueError as exc:
            raise typer.BadParameter(f"--cutoff geçersiz: {exc}") from exc

    builder = CorpusBuilder(
        repo_path,
        amendment_dates_path=amendment_dates,
        law_dates_override_path=law_dates_override,
        patches_path=amendment_patches,
        corpus_path=corpus_json,
        reproduce_path=reproduce,
        limit=limit,
        matches=match,
        strict=strict,
        cutoff=cutoff,
    )
    if from_cache is not None:
        summary = builder.build_from_cache(from_cache)
    else:
        assert from_dir is not None
        summary = builder.build_from_dir(from_dir)

    typer.echo(
        json.dumps(
            summary.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    app()
