"""Lexifina uyumlu, salt-okunur KKTC mevzuat sorgulama CLI'si.

Tüm komutlar Git nesne veritabanından (`git show`, `git log`, `git grep`, `git diff`)
okur; `git checkout` kullanmaz ve working tree'yi değiştirmez. Zaman çözümlemesi
`DeterministicGitEngine.resolve_commit_as_of` üzerinden yapılır; 1970 öncesi
`In-force` trailer'ı tüm komutlarda aynı biçimde uygulanır.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from tools.changelog import collect_changes, collect_commit_log
from tools.dashboard import build_dashboard
from tools.git_engine import DeterministicGitEngine, GitEngineError, is_repealed_text
from tools.models import LawMetadata, SearchHit, normalize_article_id
from tools.stats import render_stats_markdown, statistics_from_engine

DEFAULT_REPO_PATH = Path("corpus_repo")


def _force_utf8_console() -> None:
    """Windows'un cp1252 konsolunda Türkçe karakterlerin çökmesini önler."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - ortam bağımlı
            continue


_force_utf8_console()

app = typer.Typer(
    add_completion=False,
    help="KKTC mevzuatını Git geçmişinden salt-okunur olarak sorgular.",
)


@dataclass(slots=True)
class _State:
    engine: DeterministicGitEngine
    json_output: bool


def _state(ctx: typer.Context) -> _State:
    state = ctx.obj
    if isinstance(state, _State):
        return state
    raise typer.BadParameter("CLI bağlamı başlatılamadı")


def _emit_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def _fail(message: str, *, code: int = 1) -> NoReturn:
    typer.secho(f"Hata: {message}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code=code)


@app.callback()
def main(
    ctx: typer.Context,
    repo_path: Annotated[
        Path,
        typer.Option(
            "--repo-path",
            envvar="KKTC_LAW_REPO_PATH",
            help="Külliyat Git deposu (varsayılan: KKTC_LAW_REPO_PATH veya ./corpus_repo).",
        ),
    ] = DEFAULT_REPO_PATH,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Çıktıyı JSON olarak üret."),
    ] = False,
) -> None:
    """Tüm alt komutlar için ortak repo yolu ve çıktı biçimi seçenekleri."""
    ctx.obj = _State(
        engine=DeterministicGitEngine(repo_path, initialize=False),
        json_output=json_output,
    )


@app.command()
def laws(
    ctx: typer.Context,
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Sorgu tarihi (YYYY-MM-DD); boşsa HEAD."),
    ] = None,
) -> None:
    """Belirtilen tarihte yürürlükte olan yasaları listeler."""
    state = _state(ctx)
    try:
        items: list[LawMetadata] = state.engine.list_laws_as_of(as_of)
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc))

    if state.json_output:
        _emit_json([item.model_dump(mode="json") for item in items])
        return
    if not items:
        typer.echo("Kayıt bulunamadı.")
        return
    for item in items:
        typer.echo(f"{item.law_id}\t{item.original_date}\t{item.title}")


@app.command()
def read(
    ctx: typer.Context,
    law_id: Annotated[str, typer.Argument(help="Yasa kimliği, örn. fasil-113.")],
    article_id: Annotated[
        str,
        typer.Argument(help="Madde kimliği; 'madde-002', '2' veya 'gecici-1' olabilir."),
    ],
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Sorgu tarihi (YYYY-MM-DD); boşsa HEAD."),
    ] = None,
) -> None:
    """Bir maddenin belirtilen tarihteki metnini yazdırır."""
    state = _state(ctx)
    try:
        normalized = normalize_article_id(article_id)
        content = state.engine.read_article_as_of(law_id, normalized, as_of)
        sha = state.engine.resolve_commit_as_of(as_of)
        metadata = (
            state.engine.get_law_metadata(law_id, as_of) if sha is not None else None
        )
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc), code=2)

    if content is None:
        _fail(
            f"{law_id}/{normalized}, {as_of or 'HEAD'} itibarıyla bulunamadı"
        )

    repealed = is_repealed_text(content)
    if state.json_output:
        _emit_json(
            {
                "law_id": law_id,
                "article_id": normalized,
                "law_title": metadata.title if metadata is not None else law_id,
                "commit_sha": sha,
                "effective_date": state.engine.effective_date(sha) if sha else None,
                "as_of_date": as_of,
                "repealed": repealed,
                "content": content,
            }
        )
        return

    if repealed:
        typer.secho(
            "Uyarı: Bu madde yürürlükten kaldırılmış (mülga) görünüyor.",
            err=True,
            fg=typer.colors.YELLOW,
        )
    typer.echo(content, nl=False)


@app.command()
def history(
    ctx: typer.Context,
    law_id: Annotated[str, typer.Argument(help="Yasa kimliği, örn. fasil-113.")],
    article_id: Annotated[
        str,
        typer.Argument(help="Madde kimliği; 'madde-004' veya '4' olabilir."),
    ],
) -> None:
    """Bir maddenin tüm değişiklik geçmişini kronolojik olarak gösterir."""
    state = _state(ctx)
    try:
        items = state.engine.get_article_history(law_id, article_id)
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc), code=2)

    if state.json_output:
        _emit_json(items)
        return
    if not items:
        typer.echo("Kayıt bulunamadı.")
        return
    for item in items:
        typer.echo(
            f"{item['effective_date']}\t{item['status']}\t"
            f"{item['commit_sha'][:8]}\t{item['subject']}\t({item['author_name']})"
        )


@app.command()
def diff(
    ctx: typer.Context,
    law_id: Annotated[str, typer.Argument(help="Yasa kimliği, örn. fasil-113.")],
    article_id: Annotated[
        str,
        typer.Argument(help="Madde kimliği; 'madde-004' veya '4' olabilir."),
    ],
    from_date: Annotated[
        str,
        typer.Option("--from", help="Başlangıç tarihi (YYYY-MM-DD)."),
    ],
    to_date: Annotated[
        str,
        typer.Option("--to", help="Bitiş tarihi (YYYY-MM-DD)."),
    ],
) -> None:
    """İki tarih arasındaki madde metni farkını unified diff olarak gösterir."""
    state = _state(ctx)
    try:
        normalized = normalize_article_id(article_id)
        patch = state.engine.diff_article(law_id, normalized, from_date, to_date)
        from_sha = state.engine.resolve_commit_as_of(from_date)
        to_sha = state.engine.resolve_commit_as_of(to_date)
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc), code=2)

    if state.json_output:
        _emit_json(
            {
                "law_id": law_id,
                "article_id": normalized,
                "from_date": from_date,
                "to_date": to_date,
                "from_commit": from_sha,
                "to_commit": to_sha,
                "changed": bool(patch),
                "diff": patch,
            }
        )
        return
    if not patch:
        typer.echo("Fark yok.")
        return
    typer.echo(patch, nl=False)


@app.command()
def stats(ctx: typer.Context) -> None:
    """Külliyatın yasa, madde, commit ve tarih kaynağı istatistiklerini gösterir."""
    state = _state(ctx)
    try:
        statistics = statistics_from_engine(state.engine)
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc))
    if state.json_output:
        _emit_json(statistics.model_dump(mode="json"))
        return
    typer.echo(render_stats_markdown(statistics), nl=False)


@app.command()
def dashboard(
    ctx: typer.Context,
    output: Annotated[
        Path,
        typer.Option("--output", help="Dashboard HTML çıktı dizini."),
    ] = Path("dashboard"),
) -> None:
    """Git snapshot'ından statik HTML istatistik/görselleştirme panosu üretir."""
    state = _state(ctx)
    try:
        target = build_dashboard(state.engine, output)
    except (GitEngineError, ValueError, OSError) as exc:
        _fail(str(exc))
    if state.json_output:
        _emit_json({"dashboard": str(target)})
        return
    typer.echo(f"Dashboard üretildi: {target}")


@app.command()
def log(
    ctx: typer.Context,
    law_id: Annotated[
        str | None,
        typer.Option("--law", help="Yalnızca belirli bir yasanın commit'lerini göster."),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Başlangıç yürürlük tarihi (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="Bitiş yürürlük tarihi (YYYY-MM-DD)."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="En fazla commit sayısı."),
    ] = 50,
) -> None:
    """Tarihsel commit günlüğünü yürürlük tarihi ve yasa filtresiyle gösterir."""
    state = _state(ctx)
    try:
        entries = collect_commit_log(
            state.engine,
            law_id=law_id,
            since=since,
            until=until,
            limit=limit,
        )
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc))
    if state.json_output:
        _emit_json([entry.model_dump(mode="json") for entry in entries])
        return
    if not entries:
        typer.echo("Commit bulunamadı.")
        return
    for entry in entries:
        typer.echo(
            f"{entry.effective_date}\t{entry.commit_sha[:8]}\t"
            f"{entry.subject}\t({entry.author_name})"
        )


@app.command()
def changes(
    ctx: typer.Context,
    since: Annotated[
        str,
        typer.Option("--since", help="Başlangıç yürürlük tarihi (YYYY-MM-DD)."),
    ],
    until: Annotated[
        str | None,
        typer.Option("--until", help="Bitiş yürürlük tarihi; boşsa HEAD."),
    ] = None,
    law_id: Annotated[
        str | None,
        typer.Option("--law", help="Yalnızca belirli bir yasanın değişikliklerini göster."),
    ] = None,
) -> None:
    """İki tarih arasında eklenen/değişen/silinen madde ve metadata dosyalarını gösterir."""
    state = _state(ctx)
    try:
        items = collect_changes(
            state.engine,
            since=since,
            until=until,
            law_id=law_id,
        )
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc), code=2)
    if state.json_output:
        _emit_json([item.model_dump(mode="json") for item in items])
        return
    if not items:
        typer.echo("Değişiklik yok.")
        return
    for item in items:
        label = item.article_id or "metadata.json"
        typer.echo(f"{item.law_id}\t{label}\t{item.status}\t{item.path}")


@app.command()
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Aranacak sabit metin.")],
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Sorgu tarihi (YYYY-MM-DD); boşsa HEAD."),
    ] = None,
    law_id: Annotated[
        str | None,
        typer.Option("--law", help="Yalnızca belirli bir yasa içinde ara."),
    ] = None,
) -> None:
    """Mevzuat içinde tarih damgalı tam metin araması yapar."""
    state = _state(ctx)
    try:
        hits: list[SearchHit] = state.engine.search_legislation(query, as_of, law_id)
    except (GitEngineError, ValueError) as exc:
        _fail(str(exc), code=2)

    if state.json_output:
        _emit_json([hit.model_dump(mode="json") for hit in hits])
        return
    if not hits:
        typer.echo("Eşleşme bulunamadı.")
        return
    for hit in hits:
        typer.echo(
            f"{hit.law_id}/{hit.article_id}:{hit.line_number}: {hit.snippet}"
        )


if __name__ == "__main__":
    app()
