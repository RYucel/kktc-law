# 1a — global-law Biçim Uyumluluğu (Tasarım)

- Tarih: 2026-11-02
- Durum: Taslak — kullanıcı onayı bekliyor
- Kapsam: Alt proje **1a**. 1b (sorgu fonksiyonları) ve 1c (statik web UI) ayrı spec'lerdir.

## 1. Amaç

`kktc-law` global-law'a eklenmeyecek (Kıbrıs sorunu nedeniyle bağımsız kalır), ancak
`lexifina/global-law` sözleşmesine biçim olarak uyumlu olmalıdır. Böylece global-law'un
araç fikirleri (1b) ve ileride olası bir bağlantı aynı commit biçimi üzerinde çalışır.

Başarı ölçütü: yeniden inşa edilen külliyatta her commit global-law trailer'larını taşır,
1970 öncesi commit'ler zaman sırasını Git damgasında da korur, yürürlüğü kesim tarihinden
sonra olan değişiklikler `future` dalında durur; iki bağımsız inşa `main` ve `future` için
aynı SHA-1'i üretir.

Kapsam dışı: yeni CLI/MCP komutları (1b), UI (1c), gerçek metin yamaları (Proje 2),
yasa sayısı limitinin (`--limit 50`) değiştirilmesi.

## 2. Commit trailer'ları

`DeterministicGitEngine.apply_legislative_commit` mesajın sonuna şu bloğu yazar
(sıra sabit):

```text
<law_id>: <YYYY-MM-DD> — <değişiklik yasası> (<etkilenen maddeler>)

Law: <değer>
In-force: <YYYY-MM-DD>
Kind: <baseline|law>
```

- `Law`: değişiklik commit'inde değiştiren yasanın numarası (`32/2026`); yasanın ilk
  göründüğü commit'te `<law_id> <başlık>` (örn. `fasil-113 Şirketler`).
- `Kind`:
  - `baseline` — yasanın külliyata ilk girişi. Madde metni orijinal değil bugünkü konsolide
    metindir; etiket bunu dürüstçe belirtir.
  - `law` — değişiklik yasası olayı.
- `In-force`: yetkili yürürlük tarihi. `Effective-Date` artık **yazılmaz**.
- Okuma geriye uyumludur: trailer regex'i `In-force` veya `Effective-Date` kabul eder;
  ikisi birden varsa `In-force` kazanır. Eski yerel külliyatlar bozulmaz.
- `Corpus` ve `Corpus-commit` eklenmez; bunlar global-law'un birleşik deposuna özgüdür.

API değişikliği: `apply_legislative_commit(..., law: str, kind: Literal["baseline", "law"])`
anahtar argümanları zorunlu olur. `LegislativeCommitPlan` bu iki alanı taşır;
`build_chronological_commits` ilk yürürlük planına `baseline`, değişiklik planlarına `law`
atar. `EFFECTIVE_DATE_TRAILER` sabiti `IN_FORCE_TRAILER = "In-force"` ile değiştirilir;
eski ad yalnızca okuma regex'inde kalır.

## 3. Git zaman damgası kuralı

Doğal damga: `YYYY-MM-DDT00:00:00+00:00`; 1970 öncesi için `1970-01-01T00:00:00+00:00`.
Nihai damga, ebeveyn commit'in (varsa) damgası `P` ile:

| Durum | Damga |
| --- | --- |
| Yürürlük < 1970-01-01, ebeveyn yok | `1970-01-01T00:00:00` |
| Yürürlük < 1970-01-01, ebeveyn var | `P + 1 sn` |
| Yürürlük ≥ 1970-01-01, doğal ≥ `P` | doğal damga (saat sıfır) |
| Yürürlük ≥ 1970-01-01, doğal < `P` | `P` (eşitlik serbest) |

- 1970 öncesi commit'ler kesin artan (saniye aralıklı), 1970 ve sonrası azalmayan damga
  alır. Gerçek tarihi `1970-01-01` olan bir commit son 1970 öncesi commit'le aynı damgayı
  paylaşabilir; doğrusal geçmişte `rev-list -n 1 --before` ucu ilk döndürdüğünden sonuç
  doğrudur.
- Durumsuzdur: motor yalnızca `git log -1 --format=%cI HEAD` okur; ek dosya veya sayaç yok.
- `resolve_commit_as_of` değişmez: 1970 öncesi sorgular `--before=1970-01-01T23:59:59`
  ile adayları alır, trailer ile daraltır.
- AGENTS.md'deki "her zaman sıfır saat" kuralı bu tabloyla güncellenir; REPRODUCE.md
  (hem proje kökü hem `corpus_repo` içine yazılan) ve GUIDE.md §2–3 aynı şekilde.

## 4. Kesim tarihi ve `future` dalı

- Kesim tarihi bir **girdidir** (ham önbellek gibi): `build_corpus --cutoff YYYY-MM-DD`.
  Verilmezse kök `corpus.json`'daki `checked_on` kullanılır. Hiçbir yerde `date.today()`
  kullanılmaz.
- CI her iki inşada aynı `--cutoff` değerini açıkça geçirir. Değer iş akışının başında bir
  kez hesaplanıp (`date -u +%F`) adım çıktısı olarak paylaşılır; böylece `main` haftalık
  ilerler ve iki inşa yine aynı girdiyle çalışır.
- İnşa tüm planları tek doğrusal kronolojik geçmiş olarak yazar. Sonunda:
  - `In-force > cutoff` olan commit yoksa yalnızca `main` vardır.
  - Varsa `future` ref'i tam geçmişin ucuna konur, `main` son `In-force ≤ cutoff` commit'ine
    taşınır ve çalışma ağacı `main`'e eşitlenir (`git reset --hard`; yalnız inşa modunda,
    sorgu araçları hiçbir zaman checkout/reset yapmaz).
- Kök commit'e yazılan `corpus.json` inşa tarafından üretilir: kök dosyadaki alanlar korunur,
  `checked_on` = cutoff, `has_future_branch` = bool eklenir; anahtarlar sıralı.
- Kök commit'teki `STATS.md` yalnızca `In-force ≤ cutoff` planlarından ve ilk yürürlüğü
  cutoff'tan önce olan yasalardan hesaplanır (kök commit iki dalda ortaktır).
- Sorgu tarafı (`law.py`, MCP) bu alt projede yalnızca `main` okur.

### Bilinen kısıt: `future` şimdilik boş kalacak

`tools/parser.py` `MAX_LEGAL_YEAR = 2026` ve iki haneli yıl pivotu (`00–26 → 20YY`)
2027+ tarihlerini reddeder. Bu spec bu sınırı **değiştirmez** (OCR çöp yıllarını ayıklayan
bir güvenlik önlemidir). Sonuç: gerçek veride `future` ancak `cutoff < tarih ≤ 2026-12-31`
aralığındaki dört haneli tarihlerle dolabilir; pratikte şimdilik boştur. Dal mantığı
sentetik verilerle test edilir. Sınırın kesim tarihine bağlanması ayrı bir karardır.

## 5. `corpus.json` `history` alanı

`history: "replayed"` değeri **`"current-only"` olarak değiştirilir.** Gerekçe: global-law'da
`replayed` "değişiklik yasalarından yeniden kurulmuş metin" demektir; bu külliyatta 345
commit'ten yalnızca ilk yürürlük (baseline) commit'leri madde metni ekler, değişiklik
commit'leri yalnızca `metadata.json` günceller, `data/amendment_patches.json` boştur.
Proje 2 gerçek yamalar ürettiğinde `replayed`'e dönülür.

## 6. Etkilenen dosyalar

| Dosya | Değişiklik |
| --- | --- |
| `tools/git_engine.py` | trailer bloğu, `IN_FORCE_TRAILER`, geriye uyumlu regex, damga kuralı, `finalize_branches(cutoff)` |
| `tools/models.py` | `LegislativeCommitPlan.law`, `.kind` alanları |
| `tools/parser.py` | `build_chronological_commits` `law`/`kind` atar |
| `tools/build_corpus.py` | `--cutoff`, `corpus.json` üretimi, cutoff'a göre `STATS.md`, dal sonlandırma |
| `corpus.json` | `history: "current-only"` |
| `.github/workflows/ci-and-update.yml` | tek cutoff değeri, iki inşaya geçirme, `future` ref karşılaştırması |
| `AGENTS.md`, `REPRODUCE.md`, `GUIDE.md`, `README.md` | trailer ve damga kuralı metni |
| `tests/test_determinism.py`, `tests/test_parser.py`, `tests/test_scraper_and_builder.py` | Git zamanı varsayımları güncellenir |
| `tests/test_format_compat.py` (yeni) | aşağıdaki testler |

Okuyucu taraması: `changelog.py`, `stats.py`, `dashboard.py`, `mcp_server.py`, `law.py`,
`semantic_index.py`, `sentinel.py` tarihleri `effective_date()` üzerinden alır; Git
zamanını doğrudan göstermez, değişiklik gerekmez. Testlerde sabit SHA yoktur.

## 7. Testler

- Trailer: baseline ve law commit'lerinde üç trailer doğru sırada ve değerde.
- Geriye uyum: yalnız `Effective-Date` taşıyan commit'te `effective_date()` doğru tarih.
- Damga: 3 adet 1970 öncesi commit → `00:00:00`, `00:00:01`, `00:00:02`; ardından
  `1970-01-01` commit'i son damgayla eşit; ardından `1985-05-05` → `1985-05-05T00:00:00`.
- `--as-of 1951-06-01` ve `1960-01-01` sorguları damga değişikliğinden sonra aynı sonucu verir.
- Kesim: sentetik planlarda `cutoff` sonrası iki commit → `future` iki commit önde, `main`
  ucu `In-force ≤ cutoff`, çalışma ağacı temiz ve `main` ile aynı; `has_future_branch: true`;
  `STATS.md` gelecekteki commit'leri saymaz.
- Kesim sonrası commit yoksa `future` ref'i yok, `has_future_branch: false`.
- Determinizm: aynı girdi + aynı cutoff ile iki inşa → `main` ve `future` SHA'ları eşit.
- Mevcut tüm testler geçer (`uv run pytest -q`).

## 8. Riskler

- Tüm commit SHA'ları değişir (beklenen; külliyat her inşada sıfırdan kurulur).
- GitHub Pages dashboard'u yeni inşa ile yeniden üretilmelidir; biçim değişmez.
