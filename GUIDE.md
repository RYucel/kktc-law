# kktc-law — Kapsamlı Kullanım ve Mimari Rehberi

Bu rehber, `kktc-law` uygulamasının tüm kullanıcı arayüzlerini, Python API'lerini,
veri modelini, determinizm kurallarını, MCP araçlarını ve GitHub otomasyonunu tek
belgede açıklar. İnsan geliştiriciler ve LLM tabanlı hukuk ajanları için hazırlanmıştır.

- Depo: <https://github.com/RYucel/kktc-law>
- Dashboard: <https://ryucel.github.io/kktc-law/>
- Lisans: MIT (kod); mevzuat metinleri için `DATA_NOTICE.md`
- Sürüm: `v0.1.0`

---

## 1. Uygulama ne yapar?

`kktc-law`, Kuzey Kıbrıs Türk Cumhuriyeti mevzuatını **deterministik bir Git külliyatı**
olarak kodlar:

- Her yasa/fasıl bir dizindir.
- Her madde ayrı bir Markdown dosyasıdır.
- Her yürürlük veya değişiklik olayı ayrı bir Git commit'idir.
- Aynı ham girdilerden her makinede **aynı Git ağacı ve commit SHA-1'i** üretilir.
- Tarihsel sorgular `git checkout` kullanmadan çalışır; çalışma ağacı değişmez.

Temel yetenekler:

| Alan | Yetenek |
| --- | --- |
| Zaman yolculuğu | Belirli tarihte yürürlükte olan metni okuma |
| Değişiklik takibi | Madde diff'i, commit günlüğü, tarih aralığı değişiklikleri |
| Arama | Git grep tabanlı tam metin + yerel FTS5/BM25 semantik arama |
| Raporlama | `STATS.md`, JSON istatistikleri, statik dashboard |
| Hukuk ajanı | Exact Git + semantik retrieval + Ultra Vires denetimi |
| Otomasyon | GitHub Actions CI, haftalık canlı senkron ve GitHub Pages |

---

## 2. Depo yapısı

```text
kktc-law/
├── tools/
│   ├── git_engine.py         deterministik Git motoru + salt-okunur sorgular
│   ├── parser.py             HTML/metin ayrıştırma, tarih cascade, maddeler
│   ├── scraper.py            mahkemeler.net fihristi ve önbellek
│   ├── doc_converter.py      .doc/.docx/.pdf → HTML
│   ├── build_corpus.py       uçtan uca külliyat inşası + CLI
│   ├── amendment_patcher.py  gerçek metin yamaları
│   ├── models.py             Pydantic veri modelleri
│   ├── stats.py              istatistik ve STATS.md
│   ├── changelog.py          log + changes
│   ├── dashboard.py          statik HTML dashboard
│   ├── semantic_index.py     SQLite FTS5/BM25 indeksi
│   ├── ultra_vires.py        norm hiyerarşisi / Ultra Vires denetimi
│   ├── sentinel.py           TurkLex Sentinel orkestratörü
│   ├── law.py                kktc-law CLI
│   └── mcp_server.py         FastMCP sunucusu
├── data/
│   ├── amendment_dates.json      manuel değişiklik tarihleri
│   ├── law_dates_override.json   doğrulanmış ilk yürürlük tarihleri
│   └── amendment_patches.json    gerçek metin yamaları
├── tests/                    pytest testleri ve fixture'lar
├── raw_cache/                ham belgeler + manifest (git dışı)
├── corpus_repo/              üretilen deterministik külliyat (git dışı)
├── dashboard/index.html      yayınlanan statik dashboard
└── STATS.md                  corpus_repo içinde üretilen rapor
```

Külliyat reposunun iç yapısı:

```text
corpus_repo/
├── corpus.json
├── REPRODUCE.md
├── STATS.md
└── laws/
    ├── anayasa/
    │   ├── metadata.json
    │   ├── madde-000.md          # preamble
    │   ├── madde-001.md
    │   └── gecici-madde-001.md
    ├── fasil-113/
    │   ├── metadata.json
    │   ├── madde-001.md
    │   └── ...
    └── yasa-2024-42/
        └── ...
```

### Madde kimlikleri

| Tür | Format | Örnek |
| --- | --- | --- |
| Normal madde | `madde-NNN` | `madde-002` |
| Geçici madde | `gecici-madde-NNN` | `gecici-madde-001` |
| Anayasa preamble | `madde-000` | `madde-000` |

`normalize_article_id` şu girdileri kabul eder:

```text
madde-2, Madde 12, 2, gecici-1, Geçici Madde 3
→ madde-002, madde-012, madde-002, gecici-madde-001, gecici-madde-003
```

### `metadata.json`

```json
{
  "law_id": "fasil-113",
  "type": "fasil",
  "number": "113",
  "title": "Şirketler",
  "original_date": "1951-02-16",
  "date_source": "explicit",
  "consolidation_history": [
    {
      "amending_law": "30/1983",
      "effective_date": "1983-01-01",
      "affected_articles": ["madde-002", "madde-018"],
      "commit_message": "fasil-113: 1983-01-01 — 30/1983 (...)"
    }
  ]
}
```

`date_source` değerleri:

- `explicit`: belge içinde açık tarih
- `override`: `data/law_dates_override.json` kaydı
- `fallback_1959`: Fasıllar için 1959 Revize Baskısı / daha eski değişiklik yılı
- `fallback_law_year`: `yasa-YYYY-NN` kimliğindeki yıl

### Commit formatı

```text
<law_id>: <YYYY-MM-DD> — <değişiklik yasası> (<etkilenen maddeler>)

Effective-Date: YYYY-MM-DD
```

1970 öncesi tarihler Git'te `1970-01-01` zaman damgasına taşınır; gerçek tarih
`Effective-Date` trailer'ındadır.

---

## 3. Determinizm kuralları

- `git init -b main`
- `core.autocrlf=false`, `core.fileMode=false`
- Sabit `GIT_AUTHOR_*` / `GIT_COMMITTER_*` kimlikleri
- UTC gece yarısı commit zamanı
- UTF-8 (BOM'suz), NFC, LF satır sonu, tek trailing newline
- Alfabetik dosya ve JSON anahtar sıralaması
- `json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False)`
- Çalışma zamanı zaman damgası **yok**
- `raw_cache/`, `corpus_repo/` ana repoya dahil edilmez

Aynı ham girdi ve araç sürümleriyle iki bağımsız inşa aynı `HEAD` SHA-1'ini üretmelidir.

---

## 4. Kurulum

```sh
uv sync --extra dev
uv run pytest -q
uv run ruff check .
```

Gereksinimler:

- Python 3.11+
- Git 2.40+
- `.doc` dönüşümü için: LibreOffice (`soffice`) veya Microsoft Word/pywin32
- `.docx`: `python-docx`
- `.pdf`: `pypdf`

---

## 5. CLI: `kktc-law`

### Ortak seçenekler

```sh
uv run kktc-law --repo-path corpus_repo --json <komut>
```

- `--repo-path`: Külliyat deposu. Varsayılan: `KKTC_LAW_REPO_PATH` ortam değişkeni
  veya `./corpus_repo`.
- `--json`: Makine-okunur JSON çıktısı.

Tüm sorgular salt-okunurdur; `git checkout` çalıştırmaz.

### `laws`

Belirli tarihte yürürlükte olan yasalar:

```sh
uv run kktc-law laws --as-of 1960-01-01
uv run kktc-law --json laws --as-of 2025-01-01
```

### `read`

Madde metni:

```sh
uv run kktc-law read fasil-113 2 --as-of 2020-01-01
uv run kktc-law read yasa-2024-42 gecici-1
```

- Kısa kimlikler normalize edilir (`2`, `gecici-1`).
- Mülga metinlerde stderr'e uyarı verir.
- Bulunamazsa sıfırdan farklı çıkış kodu döner.

### `history`

Maddenin commit geçmişi:

```sh
uv run kktc-law history fasil-113 2
```

Alanlar: `effective_date`, `status` (`eklendi`/`güncellendi`/`silindi`), `commit_sha`,
`subject`, `author_name`.

### `diff`

İki tarih arasındaki unified diff:

```sh
uv run kktc-law diff fasil-113 2 --from 2018-01-01 --to 2020-01-01
uv run kktc-law --json diff fasil-113 2 --from 1980-01-01 --to 2000-01-01
```

İlk commit öncesi tarihler boş ağaç olarak kabul edilir.

### `search`

Git grep tabanlı tam metin araması:

```sh
uv run kktc-law search "tasfiye memuru" --as-of 2020-01-01
uv run kktc-law search "veri" --as-of 2025-01-01 --law yasa-2024-42
```

Sonuçlar `law_id`, `article_id`, `line_number`, `snippet` taşır.

### `stats`

Külliyat istatistikleri:

```sh
uv run kktc-law stats
uv run kktc-law --json stats
```

İçerik: yasa/madde sayıları, aktif/mülga, commit sayısı, tarih aralığı, `date_source`
dağılımı, en çok değişen yasalar ve maddeler.

### `log`

Commit günlüğü:

```sh
uv run kktc-law log --law fasil-113
uv run kktc-law log --since 1980-01-01 --until 2000-01-01 --limit 20
uv run kktc-law --json log --law fasil-113
```

### `changes`

İki tarih arasındaki değişiklikler:

```sh
uv run kktc-law changes --since 1980-01-01 --until 2000-01-01 --law fasil-113
uv run kktc-law --json changes --since 2018-01-01 --until 2020-01-01
```

Gerçek dosya değişikliklerine ek olarak, hibrit replay'de yalnızca `metadata.json`
üzerinden işaretlenen mantıksal madde güncellemelerini de listeler.

### `dashboard`

Statik HTML dashboard üretir:

```sh
uv run kktc-law dashboard --output dashboard
```

Çıktı: `dashboard/index.html`. Harici CDN/servis kullanmaz.

---

## 6. Külliyat inşası: `tools.build_corpus`

```sh
uv run python -m tools.build_corpus --repo-path corpus_repo --from-cache raw_cache
```

### Kaynak seçenekleri

| Seçenek | Açıklama |
| --- | --- |
| `--from-cache DIR` | SHA-256 manifestli `raw_cache` |
| `--from-dir DIR` | HTML/Word/PDF kaynak klasörü |
| `--scrape` | İnşa öncesi canlı fihristi tara |
| `--scrape-only` | Yalnızca tara/önbelleğe al; inşa etme |
| `--index-url URL` | Farklı fihrist adresi |
| `--limit N` | En fazla N yasa işle/indir |
| `--match TEXT` | Yasa kimliği/başlığında filtre; tekrarlanabilir |
| `--strict` | Atlanabilir hatalarda dur |
| `--amendment-dates FILE` | Manuel değişiklik tarihleri |
| `--law-dates-override FILE` | İlk yürürlük override dosyası |
| `--amendment-patches FILE` | Gerçek metin yamaları |
| `--corpus-json FILE` | Kök `corpus.json` |
| `--reproduce FILE` | Kök `REPRODUCE.md` |

### Filtreleme

```sh
uv run python -m tools.build_corpus \
  --repo-path corpus_repo \
  --from-cache raw_cache \
  --scrape-only \
  --limit 80

uv run python -m tools.build_corpus \
  --repo-path corpus_repo \
  --from-cache raw_cache \
  --limit 50 \
  --match fasil-113 --match fasil-154
```

`--match` büyük/küçük harf duyarsızdır; `law_id` veya başlıkta arar.

### `BuildSummary`

```json
{
  "law_count": 50,
  "article_count": 1960,
  "commit_count": 346,
  "head_sha": "…",
  "skipped_laws": [
    {"law_id": "fasil-110", "reason": "hiç madde ayrıştırılamadı"}
  ]
}
```

`skipped_laws` boş değilse inşa yine de başarılıdır; `--strict` ile hata ölümcül olur.

---

## 7. Scraper

`tools/scraper.py` gerçek `mevzuat.mahkemeler.net` portalını destekler.

- Fihrist: `https://mevzuat.mahkemeler.net/web/index?BackLink=YasaFihrist`
- Veri uç noktası: `MEVZUAT/Yasa/LoadDataForGrid` (DataTables/AJAX)
- Statik HTML fihristlerde iframe/alt kategori keşfi
- SHA-256 manifestli idempotent `raw_cache/`
- `.doc`, `.docx`, `.pdf`, `.rtf`, `.html` indirme
- `--match` / `--limit` filtreleri
- Tek belge hatasında devam; `strict` ile durma

Anayasa fihristte yoksa her zaman özel girdiyle eklenir:

```text
law_id=anayasa
url=https://www.mahkemeler.net/wp-content/uploads/2024/08/anayasa-4.doc
```

---

## 8. Belge dönüştürücü

`tools/doc_converter.py`:

| Girdi | Yöntem |
| --- | --- |
| `.html/.htm` | olduğu gibi |
| `.docx` | `python-docx`; paragraf + tablo sırası korunur |
| `.pdf` | `pypdf` metin çıkarımı |
| `.doc/.rtf` | LibreOffice headless → Word otomasyonu → saf Python binary fallback |

Dönüştürme başarısız olursa yasa `skipped_laws` içine yazılır.

---

## 9. Parser

`tools/parser.py` başlıca fonksiyonlar:

### `normalize_legal_text(value, encoding=None)`
HTML/byte/metni NFC UTF-8 düz metne çevirir; mojibake ve Windows kodlamalarını onarır.

### `parse_law_header(value, *, date_overrides=None)`
`LawHeader` üretir:

- `law_id`, `type`, `number`, `title`
- `original_date`
- `date_source`

Tarih sırası:

1. override dosyası
2. belge içi tarih (`[1954 Baskısı]`, `12/47`, tam tarih)
3. Fasıl fallback: 1959 veya daha eski değişiklik yılı
4. Yasa fallback: kimlikteki yıl

Geçerlilik aralığı: **1878-01-01 – 2026-12-31**. İki haneli yıllarda `27+` → `19YY`,
`00–26` → `20YY` (`12/47` → `1947`, `27/2047` reddedilir).

### `law_header_from_id(law_id, value, *, date_overrides=None)`
Başlığında resmi sayı olmayan portal belgeleri için önbellek kimliğinden başlık/tarih üretir.

### `parse_articles(value)`
Modern (`Madde 1.`), Fasıl (`1.`) ve bitişik metin madde işaretlerini ayrıştırır:

- Başlıkları temizler
- Kenar başlıklarını sonraki maddeye taşır
- Değişiklik referanslarını `amendments` alanına çıkarır
- `gecici-madde-NNN` destekler
- Anayasa preamble'ını `madde-000` olarak ekler

### `build_chronological_commits(parsed_law, amendment_dates=None, *, original_date=None, patches=None)`
Kronolojik `LegislativeCommitPlan` listesi üretir. `patches` verilirse ilgili tarihte
gerçek metin değişikliği uygulanır.

### `extract_amendment_refs(value)` / `extract_amendment_laws(value)`
`34/2019`, `12/47` gibi referansları normalize eder.

---

## 10. Gerçek yama motoru

`tools/amendment_patcher.py` üç kalıbı destekler:

```python
from tools.amendment_patcher import parse_amendment_instructions

patches = parse_amendment_instructions("""
Esas Yasanın 5'inci maddesi kaldırılarak yerine aşağıdaki yeni 5'inci madde konur:
Yeni madde metni.

Esas Yasanın 6'ncı maddesinin (2) fıkrasında yer alan 'eski ibare' ibaresi
çıkarılarak yerine 'yeni ibare' ibaresi konur.

Esas Yasanın 7'nci maddesi yürürlükten kaldırılır.
""")
```

Desteklenen `action` değerleri:

- `replace_article`
- `replace_text`
- `repeal_article`

`ArticlePatch` alanları: `target_article`, `new_content`, `old_text`, `new_text`,
`clause`, `effective_date`, `amending_law`, `law_id`.

`apply_article_patches(articles, patches)` yamaları uygular.
`load_article_patches(path)` JSON dosyasından yükler.

`build_chronological_commits(..., patches=[...])` sayesinde `kktc-law diff` gerçek
`- eski` / `+ yeni` satırlarını gösterir.

---

## 11. İstatistikler

`tools/stats.py`:

- `statistics_from_engine(engine)` → `CorpusStatistics`
- `statistics_from_parsed_laws(headers, laws, commit_dates)`
- `render_stats_markdown(stats)`

`CorpusStatistics` alanları:

```text
total_laws, law_type_counts, total_articles,
active_articles, repealed_articles, total_commits,
min_effective_date, max_effective_date,
date_source_counts, top_laws, top_articles
```

---

## 12. Commit günlüğü ve değişiklikler

`tools/changelog.py`:

### `collect_commit_log(engine, law_id=None, since=None, until=None, limit=50)`
`CommitLogEntry` listesi döndürür.

### `collect_changes(engine, since, until=None, law_id=None)`
`CorpusChange` listesi döndürür:

- `eklendi`
- `güncellendi`
- `silindi`

Metadata değişikliklerinden doğan mantıksal madde güncellemeleri de eklenir.

---

## 13. Dashboard

`tools/dashboard.py`:

- `render_dashboard(engine)` → HTML string
- `build_dashboard(engine, output_dir)` → `index.html`

İçerik: özet kartları, tür dağılımı, tarih kaynakları, on yıllık commit grafiği,
en çok değişen yasalar/maddeler, aranabilir yasa tablosu, son commit'ler ve
**yasa tam metni diff görünümü**.

Diff görünümü, değişen yasanın tüm maddelerini tek bir tam metin halinde birleştirir ve
önceki/yeni sürümü yan yana gösterir. Kırmızı satırlar eski, yeşil satırlar yeni metindir;
yasa veya commit konusunda arama yapılabilir.

GitHub Pages: <https://ryucel.github.io/kktc-law/>

---

## 14. Hibrit arama ve hukuk ajanı

### `SemanticIndex`
`tools/semantic_index.py` SQLite FTS5 + BM25 kullanır:

```python
from tools.semantic_index import SemanticIndex

with SemanticIndex.from_engine(engine, "2020-01-01") as index:
    hits = index.search("tasfiye memuru", limit=5)
```

Her `SemanticHit`: `law_id`, `article_id`, `title`, `snippet`, `score`, `commit_sha`,
`effective_date`.

### Ultra Vires
`tools/ultra_vires.py`:

```python
from tools.ultra_vires import audit_norm

report = audit_norm(
    "Anayasa'nın 1. maddesini değiştirir.",
    "2020-01-01",
    subject_level="secondary",
    engine=engine,
)
```

Norm seviyeleri:

1. `anayasa`
2. `fasil` / `yasa`
3. `secondary` / `tuzuk`

Bulgular: `ULTRA_VIRES`, `CONFLICT_WITH_CONSTITUTION`, `CITATION_NOT_FOUND`,
`CITATION_ARTICLE_NOT_FOUND`, `CITATION_REPEALED`, `TEMPORAL_SNAPSHOT_MISSING`,
`NO_NORM_CITATION`.

### TurkLex Sentinel
`tools/sentinel.py`:

- `HybridRetriever.search(query, event_date, law_id=None, limit=8)`
- `semantic_search(engine, query, as_of_date, law_id=None, limit=10)`
- `audit_contract_clause(clause_text, event_date, engine, subject_level="secondary")`
- `build_audit_prompt(...)`

`audit_contract_clause` çıktısı:

```json
{
  "event_date": "2020-01-01",
  "snapshot_commit": "…",
  "subject_level": "secondary",
  "sources": [],
  "findings": [],
  "audit_prompt": "TurkLex Sentinel — Ultra Vires Denetim Bağlamı …",
  "deterministic": true
}
```

LLM çağrısı yapılmaz; dış LLM yalnızca `audit_prompt` ve kaynakları tüketir.

---

## 15. MCP sunucusu

Çalıştırma:

```sh
uv run python -m tools.mcp_server --repo-path corpus_repo
```

Repo yolu `KKTC_LAW_REPO_PATH` ortam değişkeninden de alınır. Tüm araçlar salt-okunurdur.

### Araçlar

| Araç | Açıklama |
| --- | --- |
| `list_laws(as_of_date=None)` | Tarihteki yasalar |
| `get_law_article(law_id, article_id, as_of_date=None)` | Madde + commit + tarih + başlık |
| `get_article_amendment_history(law_id, article_id)` | Madde geçmişi |
| `compare_article_versions(law_id, article_id, from_date, to_date)` | Sürüm diff'i |
| `search_legislation(query, as_of_date=None, law_id=None)` | Git grep araması |
| `get_corpus_statistics()` | `CorpusStatistics` |
| `semantic_search(query, as_of_date=None, law_id=None, limit=10)` | FTS5/BM25 |
| `audit_norm(subject_text, as_of_date, subject_level="secondary")` | Ultra Vires raporu |
| `turklex_audit(clause_text, event_date, subject_level="secondary")` | Hibrit denetim paketi |
| `get_commit_log(law_id=None, since=None, until=None, limit=50)` | Commit günlüğü |
| `get_corpus_changes(since, until=None, law_id=None)` | Dosya/mantıksal değişiklikler |

### LLM için araç seçim protokolü

1. Kullanıcı bir **tarih** veriyorsa mutlaka o tarihi `as_of_date`/`event_date` olarak geçir.
2. Belirli yasa/madde varsa önce `get_law_article` kullan.
3. Kavram araması varsa `semantic_search` veya `search_legislation` kullan.
4. Geçmiş/karşılaştırma için `get_article_amendment_history` ve `compare_article_versions`.
5. Uyuşmazlık/aykırılık iddiası varsa `turklex_audit` kullan.
6. Yanıtta her zaman `commit_sha`, `effective_date`, `law_id`, `article_id` belirt.
7. Kaynakta olmayan hüküm uydurma; bulunamazsa açıkça "snapshot'ta yok" de.
8. Ultra Vires bulguları deterministik sinyaldir; nihai hukuki yorum LLM'e aittir.

Örnek:

```python
result = mcp_server.turklex_audit(
    "Anayasa'nın 1. maddesini değiştirir.",
    "2020-01-01",
)
```

---

## 16. GitHub otomasyonu

Repo: <https://github.com/RYucel/kktc-law>

### Yayın durumu

- Public repo
- Açıklama: `Deterministic Git corpus for TRNC (KKTC) legislation with temporal queries, FTS5 semantic search and Ultra Vires audit`
- Konular: `git`, `kktc`, `law`, `legaltech`, `legislation`
- MIT lisans
- `v0.1.0` etiketi ve release
- GitHub Pages yayında

### `.github/workflows/ci-and-update.yml`

Tetikleyiciler:

- `push`
- `pull_request`
- `workflow_dispatch`
- Haftalık cron: `0 3 * * 1` (Pazartesi 03:00 UTC)

İşler:

#### `test-and-lint`
- `uv sync --extra dev`
- `uv run ruff check .`
- `uv run pytest -q`

#### `weekly-corpus-sync`
- LibreOffice Writer kurulumu
- `--scrape-only --limit 80` ile canlı önbellek
- `--limit 50` ile külliyat inşası
- İki bağımsız inşanın aynı `HEAD` SHA-1'ini üretmesinin doğrulanması
- `git fsck --no-dangling`
- `git status --porcelain` temizlik kontrolü
- `STATS.md`, `manifest.json` ve `dashboard/index.html` artifact'i

### `.github/workflows/pages.yml`

- `main` push ve `workflow_dispatch`
- `dashboard/` klasörünü GitHub Pages'e yayınlar
- `actions/configure-pages@v6`, `upload-pages-artifact@v5`, `deploy-pages@v5`

### Güncel canlı durum

- Dashboard: <https://ryucel.github.io/kktc-law/>
- Son yerel 50 yasalık külliyat: **50 yasa, 1960 madde, 346 commit**
- Anayasa: **1**
- Son yürürlük tarihi: **2026-01-01**
- `skipped_laws: []`

Canlı GitHub runner temiz ortamında ilk 80 kaynaktan 50 yasa hedeflenir; erişilemeyen
kaynaklar `skipped_laws` ile raporlanır.

---

## 17. Testler

```sh
uv run pytest -q
uv run ruff check .
```

Kapsam:

- Determinizm / aynı SHA-1
- Parser ve tarih fallback
- Scraper + builder
- CLI + MCP
- Sentinel / Ultra Vires
- Commit log + changes
- Dashboard
- Anayasa / preamble / geçici maddeler
- Gerçek yama motoru

Son doğrulama: **86 test geçti**, ruff temiz.

---

## 18. Bilinen sınırlamalar

- Kaynak site çoğunlukla **konsolide** metin yayımlar; eski madde metni her zaman
  mevcut değildir. Bu nedenle hibrit replay kullanılır.
- `data/amendment_patches.json` boşsa değişiklik commit'leri çoğunlukla metadata
  güncellemesidir; gerçek metin diff'i için yama verisi gerekir.
- Bazı eski `.doc` belgelerinin OCR/metin kalitesi kaynağa bağlıdır.
- Canlı `mahkemeler.net` kaynaklarında 404 veya boş belge olabilir; `skipped_laws`
  bunu görünür kılar.
- Ultra Vires deterministik sinyaller üretir; nihai hukuki değerlendirme değildir.

---

## 19. Hızlı başlangıç

```sh
git clone https://github.com/RYucel/kktc-law.git
cd kktc-law
uv sync --extra dev

# Küçük bir gerçek pilot
uv run python -m tools.build_corpus \
  --repo-path corpus_repo \
  --from-cache raw_cache \
  --scrape \
  --limit 5 \
  --match fasil-113

uv run kktc-law laws
uv run kktc-law read fasil-113 2
uv run kktc-law stats
uv run kktc-law dashboard --output dashboard
```

Daha fazla bilgi:

- `README.md` — hızlı bakış
- `REPRODUCE.md` — deterministik yeniden üretim
- `AGENTS.md` — mimari ve kod kuralları
- `DATA_NOTICE.md` — veri/kaynak notu
- `CHANGELOG.md` — sürüm notları
