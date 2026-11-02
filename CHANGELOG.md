# Changelog

Bu dosya `kktc-law` projesindeki kullanıcıya dokunan değişiklikleri özetler.
Sürümleme [Semantic Versioning](https://semver.org/lang/tr/) kurallarına uyar.

## [Unreleased]

### Eklenenler

- `kktc-law log` — yürürlük tarihi ve yasa filtresiyle commit günlüğü.
- `kktc-law changes` — iki tarih arasındaki eklenen/değişen/silinen madde ve metadata
  değişiklikleri; hibrit replay'de yalnızca metadata'da işaretlenen maddeler de gösterilir.
- MCP `get_commit_log` ve `get_corpus_changes` araçları.

## [0.1.0] - 2026-11-02

### Eklenenler

- Deterministik Git motoru: `git init -b main`, sabit kimlik/tarih, `git checkout`
  kullanmadan `git show`/`git rev-list` ile tarihsel okuma.
- Parser ve normalizasyon: Fasıl/Yasa/Anayasa başlıkları, maddeler, kenar notu
  referansları, mojibake ve kodlama onarımı.
- Scraper: `mahkemeler.net` DataTables portalı, iframe/alt kategori keşfi, SHA-256
  manifestli idempotent `raw_cache/`, `--limit`/`--match` filtreleri.
- Belge dönüştürücü: `.docx` (python-docx), `.pdf` (pypdf), eski `.doc` (LibreOffice/
  Word/saf Python fallback).
- Sorgulama CLI (`kktc-law`): `laws`, `read`, `history`, `diff`, `search`, `stats`.
- FastMCP sunucusu: listeleme, okuma, geçmiş, karşılaştırma, arama ve istatistik araçları.
- Tarih fallback cascade'i: `data/law_dates_override.json`, 1959 Revize Baskısı ve yasa
  yılı fallback'leri; `LawMetadata.date_source` alanı.
- Kenar başlığı hizalaması ve iki haneli yıl pivotu (1878–2026 geçerlilik koruması).
- Gerçek yama motoru (`tools/amendment_patcher.py`): madde değiştirme, ibare değiştirme
  ve yürürlükten kaldırma kalıpları.
- KKTC Anayasası entegrasyonu ve preamble/geçici madde ayrıştırma.
- Çapraz yasa tarih eşleştirme, `STATS.md` raporu ve GitHub Actions CI/haftalık senkron.
- TurkLex Sentinel: SQLite FTS5/BM25 semantik arama, Ultra Vires denetimi ve prompt-only
  orchestrator.

### Bilinen Sınırlamalar

- `mahkemeler.net` çoğunlukla konsolide metin yayımladığı için tarihsel değişiklik
  farkları tam metin olarak her zaman mevcut değildir; `data/amendment_patches.json` ile
  gerçek metin yamaları sağlanabilir.
- Bazı eski `.doc` belgelerinin OCR/metin kalitesi kaynağa bağlıdır.
