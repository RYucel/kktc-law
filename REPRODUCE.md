# REPRODUCE.md — Deterministik Yeniden Üretim

Bu dosya, `kktc-law` külliyatının **aynı ham girdilerden**, **her makinede** ve **her tarihte**
birebir aynı Git ağacını ve commit SHA-1 değerlerini üreteceğini garanti eden kuralları ve
komutları tanımlar. `lexifina/global-law` standardındaki `REPRODUCE.md` sözleşmesinin KKTC
mevzuatına uyarlanmış halidir.

## 1. Garantiler

- `GIT_AUTHOR_*` / `GIT_COMMITTER_*` kimlik ve tarih değişkenleri her commit için açıkça atanır.
- Tüm tarihler yalnızca `YYYY-MM-DD` biçiminde saklanır; commit zamanı daima
  `YYYY-MM-DDT00:00:00+00:00` (UTC, gece yarısı) olarak sabitlenir. 1970 öncesi yürürlük
  tarihleri için Git zemini kuralı geçerlidir (bkz. §2.1).
- Her commit mesajı, gerçek yürürlük gününü makine-okunur biçimde taşıyan bir
  `Effective-Date: YYYY-MM-DD` trailer'ı ile biter (bkz. §2.1).
- Tüm metin dosyaları UTF-8 (BOM yok) ve `\n` satır sonuyla yazılır; her dosya tek bir
  son satır sonu ile biter.
- Dosya yolları ve JSON anahtarları yazılmadan önce alfabetik olarak sıralanır.
- Dosya izinleri `0644`, yürütülebilir bit yok; `core.fileMode=false`.
- Git geçmişi `git checkout` ile geri sarılmaz; tarihsel sorgular `git rev-list`, `git show`,
  `git ls-tree`, `git log`, `git diff` ve `git grep` ile çalışma ağacına dokunmadan yapılır.
- Üretilen dosyalarda çalışma zamanına ait zaman damgası (`datetime.now()`) **bulunmaz**.
- Gerçek portal `.doc/.docx/.rtf/.pdf` yayımladığı için `.docx` `python-docx`, `.pdf` `pypdf`,
  eski `.doc` ise LibreOffice/Word veya saf Python binary fallback'i ile UTF-8 HTML'e çevrilir;
  dönüştürme veya ayrıştırma hatası `BuildSummary.skipped_laws` içinde raporlanır.
- İlk yürürlük tarihi sırası: `data/law_dates_override.json` → belge içi tarih → Fasıllar için
  1959 Revize Baskısı/daha eski değişiklik yılı → Yasalar için kimlikteki yıl. Kaynak
  `LawMetadata.date_source` alanında (`explicit`, `override`, `fallback_1959`,
  `fallback_law_year`) tutulur.
- Külliyattaki yasaların `explicit`/`override` tarihleri, `NN/YYYY` değişiklik referanslarıyla
  otomatik çapraz eşleştirilir; manuel `data/amendment_dates.json` her zaman önceliklidir.
- Kök commit'e deterministik `STATS.md` eklenir; `kktc-law stats` aynı raporu depodan üretir.
- Tarih güvenliği: iki haneli `27+` yıllar `19YY`; geçerli aralık `1878-01-01`–`2026-12-31`.
- KKTC Anayasası `law_id="anayasa"` ve `1985-05-05` override tarihiyle her zaman fihristin başına
  eklenir; preamble `madde-000`, geçici maddeler `gecici-madde-NNN` olarak saklanır.
- `tools/amendment_patcher.py` ve `data/amendment_patches.json` ile değişiklik yasalarındaki
  gerçek madde/ibare yamaları ilgili tarihli commit'lere uygulanabilir.
- TurkLex Sentinel katmanı `tools/semantic_index.py` (SQLite FTS5/BM25), `tools/ultra_vires.py`
  ve `tools/sentinel.py` ile exact Git snapshot + semantik retrieval + Ultra Vires denetimini
  LLM'siz, deterministik bir prompt paketinde birleştirir.

## 2. Kontrollü Ortam

| Değişken | Değer |
| --- | --- |
| `GIT_AUTHOR_NAME` | `KKTC Cumhuriyet Meclisi` (1960 öncesi Fasıllar için `Kıbrıs Koloni İdaresi`) |
| `GIT_AUTHOR_EMAIL` | `mevzuat@kktc-law.local` |
| `GIT_COMMITTER_NAME` | `kktc-law-bot` |
| `GIT_COMMITTER_EMAIL` | `bot@kktc-law.local` |
| `GIT_AUTHOR_DATE` | `<yürürlük tarihi>T00:00:00+00:00` (`< 1970-01-01` ise `1970-01-01T00:00:00+00:00`) |
| `GIT_COMMITTER_DATE` | `GIT_AUTHOR_DATE` ile birebir aynı |

Ek olarak her Git çağrısında kullanıcı/system Git yapılandırması devre dışı bırakılır
(`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=<os.devnull>`), böylece `commit.gpgsign`,
`commit.template`, `core.excludesFile` gibi yerel ayarlar hashleri etkileyemez.

Repo başlangıç ayarları:

```sh
git init -b main
git config core.autocrlf false
git config core.fileMode false
git config core.symlinks false
git config core.precomposeunicode false
```

### 2.1. 1970 Öncesi Yürürlük Tarihleri (Fasıllar)

Git nesne biçimi negatif zaman damgalarını (1970-01-01 öncesi) kabul etmez:
`GIT_AUTHOR_DATE=1951-06-01T00:00:00+00:00` ile commit atılamaz. Bu nedenle:

- `1951-06-01` gibi tarihler için commit zaman damgası `1970-01-01T00:00:00+00:00`
  ("Git zemini") olarak yazılır.
- Gerçek tarih **kaybolmaz**: commit mesajının sonunda
  `Effective-Date: 1951-06-01` trailer'ı ve `metadata.json` içindeki `original_date`
  alanı ile korunur.
- `read_file_as_of` 1970 öncesi sorgularda önce Git zemini adaylarını `git rev-list` ile
  toplar, ardından `Effective-Date` trailer'ına göre gerçek tarihe göre daraltır; böylece
  `1951-06-02` sorgusu 1951 metnini, `1951-05-31` sorgusu `None` döndürür.
- 1970 ve sonrası sorgular doğrudan `git rev-list --before` ile (hızlı yol) yanıtlanır.

## 3. Yeniden Üretim Adımları

```sh
# 1) Temiz bir külliyat dizini oluştur
rm -rf corpus_repo && mkdir corpus_repo

# 2) Bağımlılıkları kur
uv sync --extra dev

# 3) Fihristi tara, ham önbelleği doldur ve külliyatı tek komutla sıfırdan inşa et
uv run python -m tools.build_corpus \
  --repo-path corpus_repo \
  --from-cache raw_cache \
  --scrape

# 4) Determinizmi doğrula (iki bağımsız inşa aynı SHA-1 üretmeli)
uv run pytest -q
```

Ağa çıkmadan yalnızca mevcut `raw_cache/` içeriğinden inşa etmek için `--scrape` seçeneğini
kaldırın. Yerel HTML klasöründen inşa etmek için `--from-cache` yerine `--from-dir <dizin>`
kullanın; her iki mod da aynı ham baytlar için birebir aynı `HEAD` SHA-1'ini üretir.

### 3.1. Canlı Pilot ve Filtreleme

Gerçek fihrist `https://mevzuat.mahkemeler.net/web/index?BackLink=YasaFihrist` adresindedir;
portal verisini `MEVZUAT/Yasa/LoadDataForGrid` üzerinden DataTables/AJAX ile yükler. Scraper bu
yapıyı, iframe/alt kategori sayfalarını ve `.doc/.docx/.pdf` belgelerini otomatik keşfeder.

```sh
# Yalnızca seçilen beş Fasılı indir/işle
uv run python -m tools.build_corpus \
  --repo-path corpus_repo \
  --from-cache raw_cache \
  --scrape \
  --limit 5 \
  --match fasil-113 --match fasil-154 --match fasil-3 --match fasil-4 --match fasil-5
```

`--match` yasa kimliği veya başlığında büyük/küçük harf duyarsız eşleşir; birden fazla kez
verilebilir. `--limit` filtre sonrası işlenecek en fazla yasa sayısıdır. Boş madde listesi,
eksik yürürlük tarihi veya dönüştürme hatası olan yasalar varsayılan modda `skipped_laws`
listesine nedenleriyle yazılır ve diğer yasalar inşa edilmeye devam eder. Tüm hataları ölümcül
yapmak için `--strict` kullanın. Word belgeleri için Microsoft Word/pywin32 gerekir.
Tarih override dosyası varsayılan olarak `data/law_dates_override.json`'dur; farklı bir dosya
için `--law-dates-override <dosya>` kullanın. `.docx`/`.pdf` dönüştürme için Word gerekmez.


## 4. Doğrulama

```sh
git -C corpus_repo rev-parse HEAD
git -C corpus_repo rev-list --max-parents=0 HEAD
git -C corpus_repo fsck --no-dangling
git -C corpus_repo status --porcelain   # boş olmalı
```

Beklenen sonuç: aynı `corpus.json`, aynı `raw_cache/` içeriği ve aynı araç sürümleriyle iki kez
inşa edilen repo için `HEAD` ve kök commit SHA-1 değerleri **birebir aynıdır**. Her inşa
`STATS.md` üretir; güncel istatistikler için:

```sh
uv run kktc-law --repo-path corpus_repo stats
uv run kktc-law --repo-path corpus_repo --json stats
```

## 5. Tarihsel Sorgulama (Salt Okunur)

Hiçbir sorgu komutu `git checkout` çalıştırmaz; `HEAD`, indeks ve çalışma ağacı değişmez.
Tarih çözümlemesi tek bir motorda (`resolve_commit_as_of`) yapılır; 1970 öncesi sorgularda
`Effective-Date` trailer'ı listeleme, okuma, geçmiş, diff ve arama için aynı biçimde uygulanır.

### 5.1. CLI

```sh
# 1960'ta yürürlükte olan yasalar
uv run kktc-law --repo-path corpus_repo laws --as-of 1960-01-01

# Madde okuma ("2" -> madde-002, "gecici-1" -> gecici-madde-001)
uv run kktc-law --repo-path corpus_repo read fasil-113 2 --as-of 2020-01-01

# Değişiklik geçmişi, iki tarih arası diff ve tarihsel arama
uv run kktc-law --repo-path corpus_repo history fasil-113 4
uv run kktc-law --repo-path corpus_repo diff yasa-2024-42 1 \
  --from 2020-01-01 --to 2025-01-01
uv run kktc-law --repo-path corpus_repo search "kaldırılmıştır" --as-of 2020-01-01

# Makine-okunur çıktı
uv run kktc-law --repo-path corpus_repo --json laws
```

`--repo-path` verilmezse `KKTC_LAW_REPO_PATH` ortam değişkeni, o da yoksa `./corpus_repo`
kullanılır. `read`, mülga görünen maddelerde stderr'e uyarı yazar.

### 5.2. MCP Sunucusu

```sh
KKTC_LAW_REPO_PATH=corpus_repo uv run python -m tools.mcp_server
# veya
uv run python -m tools.mcp_server --repo-path corpus_repo
```

Sunucu `list_laws`, `get_law_article`, `get_article_amendment_history`,
`compare_article_versions` ve `search_legislation` araçlarını sunar. Tüm araçlar aynı
salt-okunur motoru kullanır; çağrılardan sonra `git status --porcelain` boş kalmalıdır.

### 5.3. Elle Git Doğrulaması

```sh
SHA=$(git -C corpus_repo rev-list -n 1 --before="2000-01-01T23:59:59+00:00" main)
git -C corpus_repo show "$SHA:laws/fasil-113/madde-004.md"
git -C corpus_repo status --porcelain   # boş olmalı
```

## 6. Sürüm Notları

- Araç zinciri: Python 3.11+, Git 2.40+.
- Şema değişiklikleri `corpus.json` içindeki `history` alanı ile birlikte duyurulur.
- Commit mesajı biçimi: `<yasa kimliği>: <yürürlük tarihi> — <değişiklik yasası> (<özet>)`,
  ardından boş satır ve `Effective-Date: YYYY-MM-DD` trailer'ı gelir.
- 1970 öncesi tarihlerin Git zemini kuralı için §2.1'e bakın.
