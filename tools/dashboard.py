"""Külliyat için statik, deterministik HTML dashboard üretir.

Harici CDN/servis gerektirmez; özet kartları, SVG/CSS grafikleri, yasa tablosu ve
commit akışını tek bir `index.html` içine gömer. Çıktı Git snapshot'ından üretilir.
"""

from __future__ import annotations

import difflib
from collections import Counter
from html import escape
from pathlib import Path

from tools.changelog import collect_commit_log
from tools.git_engine import DeterministicGitEngine
from tools.stats import statistics_from_engine

__all__ = ["build_dashboard", "render_dashboard"]

_TYPE_COLORS = {
    "anayasa": "#0ea5a4",
    "fasil": "#2563eb",
    "yasa": "#f59e0b",
}
_SOURCE_COLORS = {
    "explicit": "#0ea5a4",
    "override": "#8b5cf6",
    "fallback_1959": "#64748b",
    "fallback_law_year": "#f59e0b",
}


def _fmt(value: object) -> str:
    return escape(str(value))


def _bar_chart(items: list[tuple[str, int]], color: str = "#2563eb") -> str:
    if not items:
        return '<p class="muted">Veri yok.</p>'
    maximum = max(count for _, count in items) or 1
    rows = []
    for label, count in items:
        width = max(2, round(count / maximum * 100))
        rows.append(
            '<div class="bar-row">'
            f'<span class="bar-label">{_fmt(label)}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{width}%;'
            f'background:{color}"></span></span>'
            f'<span class="bar-value">{count}</span>'
            "</div>"
        )
    return "".join(rows)


def _donut(stats) -> str:
    counts = stats.law_type_counts
    total = sum(counts.values())
    if total == 0:
        return '<p class="muted">Veri yok.</p>'
    segments: list[str] = []
    cursor = 0.0
    for law_type, color in _TYPE_COLORS.items():
        count = counts.get(law_type, 0)
        if count == 0:
            continue
        end = cursor + count / total * 100
        segments.append(f"{color} {cursor:.2f}% {end:.2f}%")
        cursor = end
    legend = "".join(
        f'<li><span class="dot" style="background:{color}"></span>'
        f"{_fmt(law_type)} <strong>{counts.get(law_type, 0)}</strong></li>"
        for law_type, color in _TYPE_COLORS.items()
    )
    return (
        f'<div class="donut-wrap"><div class="donut" style="background:conic-gradient('
        f'{", ".join(segments)}"><div class="donut-hole"><strong>{total}</strong>'
        '<span>yasa</span></div></div>'
        f'<ul class="legend">{legend}</ul></div>'
    )


def _decade_chart(commits: list) -> str:
    decades: Counter[int] = Counter()
    for entry in commits:
        try:
            year = int(entry.effective_date[:4])
        except (TypeError, ValueError):
            continue
        decades[(year // 10) * 10] += 1
    if not decades:
        return '<p class="muted">Commit verisi yok.</p>'
    maximum = max(decades.values()) or 1
    bars = []
    for decade in sorted(decades):
        count = decades[decade]
        height = max(5, round(count / maximum * 130))
        bars.append(
            '<div class="timeline-bar" title="'
            f'{decade}s: {count} commit"><span style="height:{height}px"></span>'
            f'<small>{str(decade)[2:]}s</small></div>'
        )
    return '<div class="timeline">' + "".join(bars) + "</div>"


def _law_rows(laws) -> str:
    rows = []
    for law in sorted(laws, key=lambda item: item.law_id):
        titles = _fmt(law.title)
        rows.append(
            f'<tr data-search="{_fmt(law.law_id + " " + law.title).lower()}">'
            f'<td><code>{_fmt(law.law_id)}</code></td>'
            f'<td><span class="badge badge-{_fmt(law.type)}">{_fmt(law.type)}</span></td>'
            f"<td>{titles}</td>"
            f"<td>{_fmt(law.original_date)}</td>"
            f"<td>{_fmt(law.date_source)}</td>"
            f"<td class=\"num\">{len(law.consolidation_history)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _commit_rows(commits) -> str:
    rows = []
    for entry in commits[:25]:
        rows.append(
            "<tr>"
            f"<td>{_fmt(entry.effective_date)}</td>"
            f"<td><code>{_fmt(entry.commit_sha[:8])}</code></td>"
            f"<td>{_fmt(entry.law_id)}</td>"
            f"<td>{_fmt(entry.subject)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _top_article_rows(stats) -> str:
    rows = []
    for item in stats.top_articles:
        rows.append(
            "<tr>"
            f"<td><code>{_fmt(item.law_id)}</code></td>"
            f"<td><code>{_fmt(item.article_id)}</code></td>"
            f"<td>{_fmt(item.title)}</td>"
            f'<td class="num">{item.amendment_count}</td>'
            "</tr>"
        )
    return "".join(rows)


def _law_text(engine: DeterministicGitEngine, law_id: str, commit_sha: str) -> str:
    """Bir yasanın tüm madde dosyalarını sıralı biçimde birleştirir."""
    prefix = f"laws/{law_id}/"
    paths = [
        path
        for path in engine.list_files_at_commit(commit_sha)
        if path.startswith(prefix) and path.endswith(".md")
    ]
    parts: list[str] = []
    for path in sorted(paths):
        content = engine.read_file_at_commit(path, commit_sha)
        if content is None:
            continue
        file_name = path.rsplit("/", 1)[-1]
        parts.append(f"<!-- {file_name} -->\n{content.rstrip()}")
    return "\n\n".join(parts)


def _side_by_side(old: str, new: str, limit: int = 700) -> str:
    """Eski/yeni tam yasa metnini satır hizalı iki sütunlu diff olarak üretir."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    rows: list[str] = []
    old_no = new_no = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                old_no += 1
                new_no += 1
                rows.append(
                    "<tr>"
                    f'<td class="diff-ln">{old_no}</td>'
                    f'<td class="diff-cell">{escape(old_lines[i1 + offset])}</td>'
                    f'<td class="diff-ln">{new_no}</td>'
                    f'<td class="diff-cell">{escape(new_lines[j1 + offset])}</td>'
                    "</tr>"
                )
            continue
        old_chunk = old_lines[i1:i2]
        new_chunk = new_lines[j1:j2]
        for index in range(max(len(old_chunk), len(new_chunk))):
            old_line = old_chunk[index] if index < len(old_chunk) else None
            new_line = new_chunk[index] if index < len(new_chunk) else None
            if old_line is not None:
                old_no += 1
            if new_line is not None:
                new_no += 1
            old_html = (
                f'<td class="diff-cell diff-del">{escape(old_line)}</td>'
                if old_line is not None
                else '<td class="diff-cell diff-empty"></td>'
            )
            new_html = (
                f'<td class="diff-cell diff-add">{escape(new_line)}</td>'
                if new_line is not None
                else '<td class="diff-cell diff-empty"></td>'
            )
            rows.append(
                "<tr>"
                f'<td class="diff-ln">{old_no if old_line is not None else ""}</td>'
                f"{old_html}"
                f'<td class="diff-ln">{new_no if new_line is not None else ""}</td>'
                f"{new_html}"
                "</tr>"
            )
        if len(rows) > limit:
            break
    if len(rows) > limit:
        rows = rows[:limit]
        rows.append(
            '<tr><td class="diff-ln"></td>'
            '<td class="diff-cell diff-meta" colspan="3">… diff kısaltıldı …</td></tr>'
        )
    return (
        '<table class="diff-table"><colgroup>'
        '<col style="width:42px"><col><col style="width:42px"><col>'
        "</colgroup><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _diff_section(engine: DeterministicGitEngine, commits: list, max_commits: int = 220) -> str:
    """Commit başına, değişen yasanın tam metnini eski/yeni yan yana gösterir."""
    cards: list[str] = []
    for entry in commits[:max_commits]:
        parent = engine.parent_commit(entry.commit_sha)
        if parent is None:
            continue
        changed = engine.changed_files_between(parent, entry.commit_sha)
        law_ids: set[str] = set()
        for change in changed:
            parts = change.path.split("/")
            if len(parts) == 3 and parts[0] == "laws":
                law_ids.add(parts[1])
        for law_id in sorted(law_ids):
            old = _law_text(engine, law_id, parent)
            new = _law_text(engine, law_id, entry.commit_sha)
            if old == new:
                continue
            diff_html = _side_by_side(old, new)
            change_count = len(
                [
                    line
                    for line in difflib.unified_diff(
                        old.splitlines(), new.splitlines(), lineterm=""
                    )
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                ]
            )
            search = f"{law_id} {entry.subject}".lower()
            cards.append(
                f'<details class="diff-card" data-search="{_fmt(search)}">'
                f'<summary><span class="diff-date">{_fmt(entry.effective_date)}</span>'
                f'<strong>{_fmt(law_id)} · tam yasa metni</strong>'
                f'<span class="diff-subject">{_fmt(entry.subject)}</span>'
                f'<span class="diff-sha">{_fmt(entry.commit_sha[:8])} · '
                f"{change_count} satır değişimi</span></summary>"
                f'<div class="diff-body">{diff_html}</div></details>'
            )
            if len(cards) >= 60:
                break
        if len(cards) >= 60:
            break
    if not cards:
        return (
            '<p class="muted">Bu snapshot aralığında yasa metni farkı bulunamadı. '
            "Hibrit replay'de bazı commit'ler yalnızca metadata güncellemesidir.</p>"
        )
    return (
        '<div class="toolbar"><input id="diff-search" type="search" '
        'placeholder="Yasa veya commit konusunda ara…"></div>'
        '<div id="diff-list">' + "".join(cards) + "</div>"
    )


def render_dashboard(engine: DeterministicGitEngine) -> str:
    """Mevcut Git snapshot'ından dashboard HTML'i üretir."""
    stats = statistics_from_engine(engine)
    laws = engine.list_laws_as_of(None)
    commits = collect_commit_log(engine, limit=100000)
    head = engine.head()
    source_items = [
        (key, stats.date_source_counts.get(key, 0))
        for key in _SOURCE_COLORS
    ]
    top_law_items = [
        (f"{item.law_id} · {item.title[:38]}", item.amendment_count)
        for item in stats.top_laws
    ]
    diff_html = _diff_section(engine, commits)

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kktc-law · KKTC Mevzuat Külliyatı</title>
<style>
:root {{
  --ink:#0f172a; --muted:#64748b; --bg:#f4f7fb; --card:#ffffff;
  --line:#e2e8f0; --accent:#0ea5a4; --blue:#2563eb; --gold:#f59e0b;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
a {{ color:var(--blue); text-decoration:none; }}
header {{ background:linear-gradient(120deg,#0f172a 0%,#134e4a 65%,#0d9488 100%);
  color:#f8fafc; padding:42px clamp(18px,4vw,64px) 34px; }}
header .eyebrow {{ color:#99f6e4; text-transform:uppercase; letter-spacing:.18em;
  font-size:12px; font-weight:700; }}
h1 {{ margin:8px 0 10px; font-size:clamp(30px,4vw,52px); line-height:1.05; }}
header p {{ max-width:780px; color:#cbd5e1; margin:0; }}
header code {{ color:#99f6e4; background:rgba(255,255,255,.1); padding:2px 6px;
  border-radius:5px; }}
main {{ max-width:1280px; margin:0 auto; padding:26px clamp(14px,3vw,34px) 60px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
  gap:14px; margin-top:-48px; position:relative; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:18px; box-shadow:0 8px 22px rgba(15,23,42,.06); }}
.card .k {{ color:var(--muted); font-size:12px; text-transform:uppercase;
  letter-spacing:.08em; font-weight:700; }}
.card .v {{ font-size:30px; font-weight:800; margin-top:5px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
  gap:18px; margin-top:18px; }}
.panel {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:20px; box-shadow:0 8px 22px rgba(15,23,42,.04); }}
.panel h2 {{ font-size:17px; margin:0 0 14px; }}
.panel h2 span {{ color:var(--muted); font-weight:500; font-size:13px; }}
.muted {{ color:var(--muted); }}
.donut-wrap {{ display:flex; align-items:center; gap:24px; flex-wrap:wrap; }}
.donut {{ width:150px; height:150px; border-radius:50%; position:relative; }}
.donut-hole {{ position:absolute; inset:28px; background:var(--card); border-radius:50%;
  display:flex; flex-direction:column; align-items:center; justify-content:center; }}
.donut-hole strong {{ font-size:28px; }} .donut-hole span {{ color:var(--muted); font-size:11px; }}
.legend {{ list-style:none; padding:0; margin:0; display:grid; gap:8px; }}
.legend li {{ display:flex; align-items:center; gap:8px; color:var(--muted); }}
.dot {{ width:10px; height:10px; border-radius:3px; display:inline-block; }}
.bar-row {{ display:grid; grid-template-columns:170px 1fr 36px; gap:8px;
  align-items:center; margin:8px 0; font-size:13px; }}
.bar-label {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#334155; }}
.bar-track {{ background:#eef2f7; border-radius:99px; height:10px; overflow:hidden; }}
.bar-fill {{ display:block; height:100%; border-radius:99px; }}
.bar-value {{ text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }}
.timeline {{ display:flex; align-items:flex-end; gap:5px; min-height:150px;
  overflow-x:auto; padding-top:10px; }}
.timeline-bar {{ display:flex; flex-direction:column; align-items:center; gap:5px;
  min-width:26px; justify-content:flex-end; }}
.timeline-bar span {{ width:18px; background:linear-gradient(180deg,#0ea5a4,#0f766e);
  border-radius:4px 4px 1px 1px; display:block; }}
.timeline-bar small {{ color:var(--muted); font-size:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; color:var(--muted); font-size:11px; text-transform:uppercase;
  letter-spacing:.06em; border-bottom:1px solid var(--line); padding:8px; }}
td {{ border-bottom:1px solid #f1f5f9; padding:8px; vertical-align:top; }}
tr:hover td {{ background:#f8fafc; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:99px; font-size:11px;
  font-weight:700; color:#fff; }}
.badge-anayasa {{ background:#0ea5a4; }} .badge-fasil {{ background:#2563eb; }}
.badge-yasa {{ background:#d97706; }}
.diff-card {{ border:1px solid var(--line); border-radius:10px; margin:10px 0;
  background:#fbfdff; overflow:hidden; }}
.diff-card summary {{ cursor:pointer; padding:10px 12px; display:flex; gap:10px;
  align-items:center; flex-wrap:wrap; }}
.diff-card summary strong {{ font-size:13px; }}
.diff-date, .diff-sha {{ color:var(--muted); font-size:11px; font-family:ui-monospace,monospace; }}
.diff-subject {{ color:#475569; font-size:12px; flex:1; min-width:180px; }}
.diff-body {{ border-top:1px solid var(--line); padding:10px 12px; overflow:auto;
  background:#fff; max-height:620px; }}
.diff-table {{ width:100%; border-collapse:collapse; table-layout:fixed;
  font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace; }}
.diff-table td {{ padding:0 6px; vertical-align:top; white-space:pre-wrap;
  overflow-wrap:anywhere; border-bottom:1px solid #f1f5f9; }}
.diff-ln {{ color:#94a3b8; text-align:right; user-select:none; background:#f8fafc; }}
.diff-cell {{ color:#334155; }}
.diff-del {{ background:#fdecec; color:#991b1b; }}
.diff-add {{ background:#e8f8ee; color:#166534; }}
.diff-empty {{ background:#f8fafc; }}
.diff-meta {{ color:#64748b; font-style:italic; }}
.toolbar {{ display:flex; justify-content:space-between; gap:12px; align-items:center;
  margin-bottom:10px; flex-wrap:wrap; }}
input[type=search] {{ border:1px solid var(--line); border-radius:9px; padding:9px 12px;
  min-width:260px; font:inherit; background:#fff; }}
.table-wrap {{ max-height:560px; overflow:auto; }}
footer {{ color:var(--muted); font-size:12px; text-align:center; padding:24px; }}
@media (max-width:600px) {{ .bar-row {{ grid-template-columns:110px 1fr 30px; }} }}
</style>
</head>
<body>
<header>
  <div class="eyebrow">TurkLex · deterministic law corpus</div>
  <h1>KKTC Mevzuat Külliyatı</h1>
  <p>Her yasa bir Git ağacı, her yürürlük olayı bir commit. Görünüm
  <code>{_fmt(head[:12])}</code> snapshot'ından üretildi; tarih aralığı
  <strong>{_fmt(stats.min_effective_date or "-")}</strong> –
  <strong>{_fmt(stats.max_effective_date or "-")}</strong>.</p>
</header>
<main>
  <section class="cards">
    <div class="card"><div class="k">Yasa</div><div class="v">{stats.total_laws}</div></div>
    <div class="card"><div class="k">Madde</div><div class="v">{stats.total_articles}</div></div>
    <div class="card"><div class="k">Aktif madde</div>
      <div class="v">{stats.active_articles}</div></div>
    <div class="card"><div class="k">Mülga</div><div class="v">{stats.repealed_articles}</div></div>
    <div class="card"><div class="k">Commit</div><div class="v">{stats.total_commits}</div></div>
  </section>

  <div class="grid">
    <section class="panel">
      <h2>Yasa türleri <span>norm dağılımı</span></h2>
      {_donut(stats)}
    </section>
    <section class="panel">
      <h2>Tarih kaynakları <span>metadata.date_source</span></h2>
      {_bar_chart(source_items, "#0ea5a4")}
    </section>
    <section class="panel" style="grid-column:span 2">
      <h2>Yürürlük zaman çizelgesi <span>on yıllara göre commit</span></h2>
      {_decade_chart(commits)}
    </section>
    <section class="panel" style="grid-column:span 2">
      <h2>En çok değişen yasalar <span>değişiklik kaydı</span></h2>
      {_bar_chart(top_law_items, "#2563eb")}
    </section>
    <section class="panel">
      <h2>En çok değişen maddeler</h2>
      <div class="table-wrap">
        <table><thead><tr><th>Yasa</th><th>Madde</th><th>Başlık</th>
        <th class="num">#</th></tr></thead>
        <tbody>{_top_article_rows(stats)}</tbody></table>
      </div>
    </section>
  </div>

  <section class="panel" style="margin-top:18px">
    <div class="toolbar">
      <h2 style="margin:0">Yasalar <span>{stats.total_laws} kayıt</span></h2>
      <input id="law-search" type="search" placeholder="Yasa kimliği veya başlıkta ara…">
    </div>
    <div class="table-wrap">
      <table id="law-table">
        <thead><tr><th>Kimlik</th><th>Tür</th><th>Başlık</th><th>İlk yürürlük</th>
        <th>Kaynak</th><th class="num">Değişiklik</th></tr></thead>
        <tbody>{_law_rows(laws)}</tbody>
      </table>
    </div>
  </section>

  <section class="panel" style="margin-top:18px">
    <h2>Son commit'ler <span>en yeni 25</span></h2>
    <div class="table-wrap">
      <table><thead><tr><th>Tarih</th><th>Commit</th><th>Yasa</th><th>Konu</th></tr></thead>
      <tbody>{_commit_rows(commits)}</tbody></table>
    </div>
  </section>

  <section class="panel" style="margin-top:18px">
    <h2>Yasa metni diff görünümü <span>eski / yeni yan yana</span></h2>
    <p class="muted">Değişen yasanın tam metni: solda önceki sürüm, sağda yeni sürüm.
    Kırmızı satırlar kaldırılan/eskimiş, yeşil satırlar eklenen/yeni metindir.</p>
    {diff_html}
  </section>
</main>
<footer>kktc-law · deterministik Git külliyatı · bu sayfa dış servis kullanmaz</footer>
<script>
const search = document.getElementById('law-search');
search.addEventListener('input', () => {{
  const q = search.value.toLocaleLowerCase('tr');
  document.querySelectorAll('#law-table tbody tr').forEach(row => {{
    row.style.display = row.dataset.search.includes(q) ? '' : 'none';
  }});
}});
const diffSearch = document.getElementById('diff-search');
if (diffSearch) {{
  diffSearch.addEventListener('input', () => {{
    const q = diffSearch.value.toLocaleLowerCase('tr');
    document.querySelectorAll('#diff-list .diff-card').forEach(card => {{
      card.style.display = card.dataset.search.includes(q) ? '' : 'none';
    }});
  }});
}}
</script>
</body>
</html>
"""


def build_dashboard(engine: DeterministicGitEngine, output_dir: str | Path) -> Path:
    """Dashboard HTML'ini `output_dir/index.html` altına yazar."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "index.html"
    target.write_text(render_dashboard(engine), encoding="utf-8", newline="\n")
    return target
