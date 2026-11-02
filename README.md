# kktc-law — KKTC Mevzuatı için Deterministik Git Külliyatı

[![CI](https://github.com/RYucel/kktc-law/actions/workflows/ci-and-update.yml/badge.svg)](https://github.com/RYucel/kktc-law/actions/workflows/ci-and-update.yml)
[![License: MIT](https://img.shields.io/github/license/RYucel/kktc-law)](https://github.com/RYucel/kktc-law/blob/main/LICENSE)

Kuzey Kıbrıs Türk Cumhuriyeti mevzuatını (Fasıllar, Yasalar, Anayasa) her maddesi ayrı bir
Markdown dosyası, her yürürlük/değişiklik olayı ise tarihsel ve **deterministik** bir Git commit'i
olacak şekilde kodlayan araç seti.

Kapsamlı kullanım, Python API, MCP ve GitHub otomasyon rehberi: [GUIDE.md](GUIDE.md).

## Durum

| Faz | İçerik | Durum |
| --- | --- | --- |
| 1 | Proje iskeleti, veri modelleri, deterministik Git motoru | Tamamlandı |
| 2 | Parser ve normalizasyon motoru | Tamamlandı |
| 3 | Scraper ve uçtan uca pipeline | Tamamlandı |
| 4 | `law.py` CLI ve FastMCP sunucusu | Tamamlandı |
| 5 | Gerçek veriyle pilot külliyat, filtreleme ve dayanıklılık | Tamamlandı |
| 6 | Tarih fallback, kenar başlığı hizalaması ve platform bağımsız belge dönüştürücü | Tamamlandı |
| 7 | Çapraz tarih eşleştirme, `STATS.md` ve GitHub Actions | Tamamlandı |
| 8 | Tarih anomalisi koruması, KKTC Anayasası ve gerçek yama motoru | Tamamlandı |
| 9 | TurkLex Sentinel: FTS5 hibrit arama ve Ultra Vires denetimi | Tamamlandı |

## Kurulum

```sh
uv sync --extra dev
```

## Testler

```sh
uv run pytest -q
```

## Temel Bileşenler

- `tools/models.py` — Pydantic v2 şemaları: `LawMetadata`, `AmendmentRecord`, `ArticleVersion`
  ve parser çıktıları (`LawHeader`, `ParsedArticle`, `ParsedLaw`, `LegislativeCommitPlan`).
- `tools/git_engine.py` — `DeterministicGitEngine`: sabit kimlik/tarih, UTF-8 + LF, alfabetik
  sıralı commit'ler; `git checkout` kullanmadan `git rev-list` + `git show` ile tarihsel okuma.
- `tools/parser.py` — `normalize_legal_text`, `parse_law_header`, `law_header_from_id`,
  `extract_amendment_refs`, `parse_articles` ve `build_chronological_commits`;
  `windows-1254`/UTF-8 ve mojibake normalizasyonu, `[1959 Baskısı]`/dipnot tarih fallback'i,
  kenar başlıklarının sonraki maddeye aktarımı ve kronolojik replay planı.
- `tools/scraper.py` — `parse_mahkemeler_index` ve `MahkemelerScraper`: asenkron fihrist taraması,
  `SHA-256` manifestli idempotent `raw_cache/`, eşzamanlılık sınırı ve `tenacity` geri çekilmesi.
  Gerçek `mevzuat.mahkemeler.net` DataTables portalı, iframe/alt kategori keşfi ve
  `--limit`/`--match` filtreleri desteklenir.
- `tools/doc_converter.py` — `.docx` için `python-docx`, `.pdf` için `pypdf`, eski `.doc` için
  LibreOffice/Word ve saf Python binary metin fallback'i; çıktı parser'a uygun UTF-8 HTML'dir.
- `tools/build_corpus.py` — `merge_commit_plans` ile küresel kronolojik birleştirme, `CorpusBuilder`
  ile `--from-cache`/`--from-dir` kaynaklarından deterministik külliyat inşası ve `typer` CLI.
  `--limit`, tekrarlanabilir `--match`, `--strict`, tarih override dosyası ve
  `BuildSummary.skipped_laws` desteği vardır.

- `tools/stats.py` — Git deposundan veya inşa planlarından deterministik `CorpusStatistics`
  üretir; `STATS.md` raporunu ve `kktc-law stats` / MCP `get_corpus_statistics` çıktısını besler.
- `tools/amendment_patcher.py` — değişiklik yasalarındaki madde değiştirme, ibare değiştirme ve
  yürürlükten kaldırma kalıplarını `ArticlePatch` yamalarına çevirir; `build_chronological_commits`
  bu yamaları ilgili tarihte uygulayarak gerçek `git diff` üretir.
- `data/law_dates_override.json` — doğrulanmış yasa tarihleri; KKTC Anayasası için `1985-05-05`.
- `data/amendment_patches.json` — isteğe bağlı gerçek metin yamaları.
- `tools/changelog.py` — tarih/yasa filtreli commit günlüğü (`log`) ve iki tarih arasındaki
  eklenen/değişen/silinen madde-metadata değişikliklerini (`changes`) üretir.
- `tools/dashboard.py` — Git snapshot'ından dış servis gerektirmeyen statik HTML dashboard
  (özet kartları, SVG/CSS grafikleri, yasa tablosu, commit akışı) üretir.
- `tools/semantic_index.py` — Git snapshot'ından kurulan yerel SQLite FTS5/BM25 indeksi;
  dış vektör servisi gerektirmeyen deterministik semantik arama.
- `tools/ultra_vires.py` — normlar hiyerarşisi, atıf çözümleme, mülga atıf ve yetki aşımı
  sinyallerini deterministik üreten Ultra Vires denetçisi.
- `tools/sentinel.py` — TurkLex Sentinel orchestrator'ı: exact Git + FTS5 hibrit retrieval,
  tarihsel snapshot, Ultra Vires bulguları ve LLM'e hazır audit prompt'u.
- `tools/law.py` — salt-okunur `kktc-law` CLI'si: `laws`, `read`, `history`, `diff`, `search` ve
  `stats` komutları; `--as-of`, `--repo-path` ve `--json` desteği.
- `tools/mcp_server.py` — `FastMCP("kktc-law")` sunucusu: `list_laws`, `get_law_article`,
  `get_article_amendment_history`, `compare_article_versions` ve `search_legislation` araçları.
- `corpus.json` — `lexifina/global-law` uyumlu külliyat üst verisi.
- `REPRODUCE.md` — deterministik yeniden üretim sözleşmesi ve komutları.

## Sorgulama CLI'si

Tüm sorgular Git nesne veritabanından okur; `git checkout` kullanılmaz ve çalışma ağacı
değiştirilmez. Tarih çözümlemesi 1970 öncesi `Effective-Date` trailer'ı dahil tüm komutlarda
aynı motordan geçer. Varsayılan repo yolu `KKTC_LAW_REPO_PATH` ortam değişkeni veya
`./corpus_repo`'dur.

```sh
# Belirli tarihte yürürlükte olan yasalar
uv run kktc-law laws --as-of 1960-01-01

# Madde metni; "2" -> madde-002, "gecici-1" -> gecici-madde-001
uv run kktc-law read fasil-113 2 --as-of 2020-01-01
uv run kktc-law read yasa-2024-42 gecici-1

# Değişiklik geçmişi ve iki tarih arasındaki unified diff
uv run kktc-law history fasil-113 4
uv run kktc-law diff yasa-2024-42 1 --from 2020-01-01 --to 2025-01-01

# Tarih damgalı tam metin araması (isteğe bağlı --law filtresiyle)
uv run kktc-law search "kaldırılmıştır" --as-of 2020-01-01
uv run kktc-law search "veri" --as-of 2025-01-01 --law yasa-2024-42

# Külliyat istatistikleri
uv run kktc-law stats
uv run kktc-law --json stats

# Commit günlüğü ve tarih aralığındaki değişiklikler
uv run kktc-law log --law fasil-113 --since 1980-01-01 --until 2000-01-01
uv run kktc-law changes --since 1980-01-01 --until 2000-01-01 --law fasil-113

# Statik görsel dashboard
uv run kktc-law dashboard --output dashboard

# JSON çıktısı ve açık repo yolu
uv run kktc-law --repo-path corpus_repo --json laws
```

`changes`, hem gerçek dosya farklarını hem de yalnızca `metadata.json` üzerinden işaretlenen
mantıksal madde güncellemelerini listeler. `dashboard/index.html` dış servis gerektirmez;
`Deploy dashboard` workflow'u bu klasörü GitHub Pages'e yayınlar.

`read`, mülga görünen maddelerde metni yine yazdırır ancak stderr'e uyarı verir; bulunamayan
madde veya yasa için sıfırdan farklı çıkış kodu döner.

## MCP Sunucusu

Sunucu hedef repoyu `KKTC_LAW_REPO_PATH` ortam değişkeninden veya `--repo-path` argümanından
alır. MCP istemcileri beş aracı doğrudan çağırabilir; tüm yanıtlar yapılandırılmış dict/JSON'dur.

```sh
uv run python -m tools.mcp_server --repo-path corpus_repo
```

Örnek istemci yapılandırması:

```json
{
  "mcpServers": {
    "kktc-law": {
      "command": "uv",
      "args": ["run", "python", "-m", "tools.mcp_server", "--repo-path", "corpus_repo"]
    }
  }
}
```

## Canlı Pilot Külliyat

Gerçek portal fihristi `https://mevzuat.mahkemeler.net/web/index?BackLink=YasaFihrist`
üzerinden okunur. Portal verisini DataTables/AJAX ile yüklediği için scraper önce fihrist
sayfasını, ardından `MEVZUAT/Yasa/LoadDataForGrid` uç noktasını kullanır. Belgeler `.doc`,
`.docx` ve `.pdf` olabildiğinden inşa sırasında HTML'e dönüştürülür.

```sh
# Beş temel Fasılı canlı indir ve külliyatı inşa et
uv run python -m tools.build_corpus \
  --repo-path corpus_repo \
  --from-cache raw_cache \
  --scrape \
  --limit 5 \
  --match fasil-113 --match fasil-154 --match fasil-3 --match fasil-4 --match fasil-5
```

`--match` birden fazla kez verilebilir ve yasa kimliği veya başlığında büyük/küçük harf
duyarsız arama yapar. `--limit` filtre sonrası en fazla kaç yasanın işleneceğini belirler.
Varsayılan modda bir yasa boş/bozuk çıkarsa tüm inşa durmaz; yasa `skipped_laws` listesine
nedeniyle birlikte yazılır. Hata yerine hemen durmak için `--strict` kullanın.

JSON özet örneği:

```json
{
  "law_count": 2,
  "article_count": 387,
  "commit_count": 18,
  "skipped_laws": [
    {"law_id": "fasil-3", "reason": "ilk yürürlük tarihi bulunamadı"}
  ]
}
```

### Tarih Fallback ve Kenar Başlıkları

Yürürlük tarihi çözümleme sırası:

1. `data/law_dates_override.json` içindeki doğrulanmış `law_id` tarihi (`override`),
2. belge içindeki açık/ilk tarih (`explicit`),
3. Fasıllar için 1959 Revize Baskısı veya daha eski değişiklik yılı (`fallback_1959`),
4. Yasalar için `yasa-YYYY-NN` kimliğindeki yıl (`fallback_law_year`).

Seçilen kaynak `LawMetadata.date_source` alanında saklanır. Ayrıca bir sonraki maddenin
numarasından hemen önce gelen kısa kenar başlıkları ve değişiklik referansları önceki maddenin
gövdesinden çıkarılıp sonraki maddenin `title`/`amendments` alanına aktarılır.

```sh
uv run kktc-law read fasil-113 1   # sonunda "Tefsir" kalmaz
uv run kktc-law read fasil-113 2   # "# Madde 2: Tefsir"
```

### Çapraz Tarih ve İstatistikler

İnşa sırasında külliyattaki `yasa-YYYY-NN` kayıtlarının `explicit`/`override` tarihleri,
değişiklik referanslarıyla (`NN/YYYY`) otomatik eşleştirilir. Manuel
`data/amendment_dates.json` kayıtları her zaman önceliklidir. Böylece örneğin `42/2024`
değişikliği, kaynak yasa külliyatta varsa `2024-01-01` yerine gerçek yürürlük tarihinde
commit edilir.

Her inşada kök commit'e `STATS.md` eklenir. Güncel depodan rapor almak için:

```sh
uv run kktc-law --repo-path corpus_repo stats
uv run kktc-law --repo-path corpus_repo --json stats
```

GitHub Actions `.github/workflows/ci-and-update.yml` her push/PR'da test/lint; haftalık cron
ve manuel tetiklemede LibreOffice kurulumu, canlı 50 yasa senkronizasyonu ve iki bağımsız
inşanın aynı `HEAD` SHA-1'ini üretmesini doğrular.

### Tarih Koruması, Anayasa ve Yama Motoru

- İki haneli yıllar güvenli biçimde çözümlenir: `27+` yıllar `19YY`, `00-26` yıllar `20YY`.
  Böylece `12/47` → `1947`, `27/2047` ise geçersiz sayılır.
- Geçerli tarih aralığı `1878-01-01` – `2026-12-31`; dışındaki değişiklikler kabul edilmez.
- KKTC Anayasası fihriste her zaman `law_id="anayasa"` olarak eklenir ve `1985-05-05`
  doğrulanmış tarihiyle külliyata girer. Preamble `madde-000` olarak, geçici maddeler
  `gecici-madde-NNN` olarak parse edilir.
- Değişiklik yasası metinleri `parse_amendment_instructions` ile yamalara dönüştürülebilir;
  `--amendment-patches` / `data/amendment_patches.json` ile gerçek metin farkı üretilir.

## TurkLex Sentinel

`kktc-law` MCP sunucusu artık hibrit hukuk ajanı katmanını da sunar. LLM çağrısı yapılmaz;
ajan deterministik Git snapshot'ı, FTS5/BM25 semantik eşleşmeleri ve Ultra Vires bulgularını
birleştirip dış LLM'e verilecek kaynaklı prompt'u üretir.

```text
semantic_search(query, as_of_date, law_id, limit)
audit_norm(subject_text, as_of_date, subject_level)
turklex_audit(clause_text, event_date, subject_level)
```

- `semantic_search`: madde metinlerini commit SHA ve yürürlük tarihiyle döndürür.
- `audit_norm`: Anayasa > Fasıl/Yasa > ikincil norm hiyerarşisini, atıfları ve mülga
  maddeleri denetler.
- `turklex_audit`: exact + semantik kaynakları, Ultra Vires bulgularını ve `audit_prompt`
  alanını tek pakette döndürür.

Örnek:

```python
from tools.git_engine import DeterministicGitEngine
from tools.sentinel import audit_contract_clause

engine = DeterministicGitEngine("corpus_repo", initialize=False)
result = audit_contract_clause(
    "Anayasa'nın 1. maddesini değiştirir.",
    "2020-01-01",
    engine,
)
print(result["snapshot_commit"], result["findings"])
```

## Örnek Çıktı

```text
$ uv run kktc-law --repo-path corpus_repo laws --as-of 2025-01-01
anayasa    1985-05-05  Kuzey Kıbrıs Türk Cumhuriyeti Anayasası
fasil-113  1951-02-16  Şirketler
fasil-154  1960-08-16  Ceza Yasası
...
```

```text
$ uv run kktc-law --repo-path corpus_repo stats
Toplam yasa        : 50
Toplam madde       : 1960
Aktif madde        : 1949
Mülga madde        : 11
Tarihsel commit    : 346
Tarih aralığı      : 1899-06-02 → 2026-01-01
Yasa türleri       : anayasa 1, fasil 44, yasa 5
```

```text
$ uv run kktc-law --repo-path corpus_repo read fasil-113 2 --as-of 2020-01-01
# Madde 2: Tefsir

Bu Yasada, metin başka türlü gerektirmedikçe ...
```

## Parser Kullanımı

```python
from pathlib import Path

from tools.git_engine import DeterministicGitEngine
from tools.models import ParsedLaw
from tools.parser import build_chronological_commits, parse_articles, parse_law_header

raw = Path("tests/fixtures/fasil_113_sample.html").read_bytes()
law = ParsedLaw(header=parse_law_header(raw), articles=parse_articles(raw))
plans = build_chronological_commits(
    law,
    {"28/1974": "1974-12-15", "41/2018": "2018-09-05"},
)

engine = DeterministicGitEngine("corpus_repo")
for plan in plans:
    plan.apply_to(engine)
```

Haritada bulunmayan değişiklik referansları için yürürlük tarihi `YYYY-01-01` olarak varsayılır.

Ayrıntılı ürün gereksinimleri için `PRD.md`, katkı ve mimari kuralları için `AGENTS.md` dosyalarına
bakın.
