"""Külliyat için statik, deterministik HTML dashboard üretir.

Harici CDN/servis gerektirmez; özet kartları, SVG/CSS grafikleri, yasa tablosu ve
commit akışını tek bir `index.html` içine gömer. Çıktı Git snapshot'ından üretilir.
"""

from __future__ import annotations

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
