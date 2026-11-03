# 1a — global-law Biçim Uyumluluğu Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Külliyat commit'lerini global-law biçimine (trailer'lar, 1970 öncesi saniye aralıklı damga, kesim tarihli `future` dalı) taşımak; determinizmi korumak.

**Architecture:** Değişiklik `DeterministicGitEngine` (mesaj + damga + dal sonlandırma) ve `CorpusBuilder` (kesim tarihi, üretilen `corpus.json`, kesime göre `STATS.md`) üzerinde toplanır. Plan modeli `law`/`kind` taşır; parser bunları doldurur. Sorgu tarafı (`law.py`, MCP) yalnızca geriye uyumlu trailer okumasıyla etkilenir.

**Tech Stack:** Python 3.11+, pydantic v2, typer, git CLI (subprocess), pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-global-law-format-compat-design.md`

## Global Constraints

- Trailer bloğu sırası sabit: `Law: …`, `In-force: YYYY-MM-DD`, `Kind: baseline|law`; mesaj gövdesinden bir boş satırla ayrılır, tek son `\n`.
- `Effective-Date` artık yazılmaz; okuma `In-force` veya `Effective-Date` kabul eder, ikisi varsa `In-force` kazanır.
- `Corpus` / `Corpus-commit` trailer'ı eklenmez.
- Damga: 1970 öncesi → ebeveyn yoksa `1970-01-01T00:00:00+00:00`, varsa ebeveyn + 1 sn; ≥1970 → `YYYY-MM-DDT00:00:00+00:00`, ebeveynden erkense ebeveyn damgası (eşitlik serbest). Author ve committer damgası aynı.
- Kesim tarihi girdidir: `--cutoff YYYY-MM-DD`, yoksa kök `corpus.json` `checked_on`. `date.today()` / `datetime.now()` yok.
- Sorgu araçları asla `checkout`/`reset` yapmaz; `reset --hard` yalnız inşa sonunda dal sonlandırmada.
- JSON: `json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False) + "\n"`.
- `corpus.json` `history`: `"current-only"`.
- `tools/parser.py` `MAX_LEGAL_YEAR = 2026` değişmez.
- Kod stili: `from __future__ import annotations`, tam tipli, Türkçe docstring/yorum (çevre kodla aynı yoğunlukta), `# TODO` yok.
- Her görevin sonunda `uv run pytest -q` yeşil.

## Review Focus

1. Kesim tarihi tüm commit'lerden önceyse (`--cutoff 1800-01-01`): inşa repoya hiçbir şey yazmadan anlaşılır `ValueError` vermeli, `main` boş kalmamalı. → Task 4 testi.
2. `In-force` tam olarak kesim tarihine eşit commit `main`'de kalmalı (≤, < değil). → Task 3 testi.
3. Yalnız `Effective-Date` taşıyan eski yerel külliyat yeni CLI ile `--as-of` sorgularında aynı sonucu vermeli. → Task 1 testi.
4. Aynı gün (≥1970) birden çok commit (örn. `2026-01-01` kümesi) aynı damgayı paylaşır; `--as-of` o günün **son** commit'ini döndürmeli. → Task 2 testi.
5. Gerçek tarihi `1970-01-01` olan commit 1970 öncesi zincirle damga paylaşır; `--as-of 1969-12-31` onu içermemeli. → Task 2 testi.

---

### Task 1: Trailer bloğu ve plan alanları

**Files:**
- Modify: `tools/models.py` (`LegislativeCommitPlan`, yeni `CommitKind`)
- Modify: `tools/git_engine.py:37,55-65,148-183,935-950` (sabitler, regex, `apply_legislative_commit`, `_message_with_effective_date`, `_effective_date_from_body`)
- Modify: `tools/parser.py:1200-1252` (`build_chronological_commits`)
- Modify: `tests/test_determinism.py`, `tests/test_parser.py`, `tests/test_scraper_and_builder.py` (çağrı yerleri `law=`/`kind=` geçirir; `Effective-Date` assert'leri `In-force`'a döner)
- Create: `tests/test_format_compat.py`

**Interfaces:**
- Produces:
  - `tools.models.CommitKind = Literal["baseline", "law"]`
  - `LegislativeCommitPlan.law: str = Field(min_length=1)`, `LegislativeCommitPlan.kind: CommitKind` (zorunlu); `apply_to` ikisini motora geçirir.
  - `DeterministicGitEngine.apply_legislative_commit(effective_date, author_name, commit_message, file_changes, tag_name=None, *, law: str, kind: CommitKind, deleted_paths=())`
  - `tools.git_engine.IN_FORCE_TRAILER: Final[str] = "In-force"`, `LAW_TRAILER = "Law"`, `KIND_TRAILER = "Kind"`; `EFFECTIVE_DATE_TRAILER` sabiti `LEGACY_EFFECTIVE_DATE_TRAILER` olarak yeniden adlandırılır, `__all__` güncellenir.
  - `effective_date(sha)` davranışı değişmez (trailer'dan okur).

- [ ] **Step 1: Failing testleri yaz** (`tests/test_format_compat.py`)

```python
def test_baseline_and_law_commits_carry_trailers(tmp_path):
    engine = DeterministicGitEngine(tmp_path / "r")
    engine.apply_legislative_commit("1951-06-01", COLONIAL_AUTHOR_NAME, "fasil-113: ilk",
        {"laws/fasil-113/madde-001.md": "# Madde 1\n\nA.\n"},
        law="fasil-113 Şirketler", kind="baseline")
    engine.apply_legislative_commit("2018-09-05", DEFAULT_AUTHOR_NAME, "fasil-113: 41/2018",
        {"laws/fasil-113/madde-001.md": "# Madde 1\n\nB.\n"}, law="41/2018", kind="law")
    root = engine.commit_message(engine.root_commit())
    head = engine.commit_message("HEAD")
    assert root.endswith("\n\nLaw: fasil-113 Şirketler\nIn-force: 1951-06-01\nKind: baseline\n")
    assert head.endswith("\n\nLaw: 41/2018\nIn-force: 2018-09-05\nKind: law\n")
    assert "Effective-Date" not in root + head

def test_legacy_effective_date_trailer_still_resolves(tmp_path):
    # Eski biçimde commit'i doğrudan git ile yaz (motoru atla): mesaj
    # "x\n\nEffective-Date: 1951-06-01\n", damga 1970-01-01T00:00:00+00:00
    ...
    assert engine.effective_date("HEAD") == "1951-06-01"
    assert engine.read_file_as_of(path, "1955-01-01") is not None
    assert engine.read_file_as_of(path, "1949-01-01") is None

def test_in_force_wins_over_legacy_when_both_present(): ...
    # gövde "x\n\nEffective-Date: 1950-01-01\nIn-force: 1951-06-01\n" → "1951-06-01"

def test_parser_assigns_kind_and_law(): 
    # tests/fixtures/fasil_113_sample.html ile build_chronological_commits
    assert plans[0].kind == "baseline" and plans[0].law == f"{law_id} {title}"
    assert all(p.kind == "law" and p.law == p.amending_law for p in plans[1:])
```

Eski biçim commit'i için yardımcı: `git -C repo commit -F -` çağrısını `engine._legislative_env("1951-06-01", COLONIAL_AUTHOR_NAME)` ortamıyla `subprocess.run` ile yap (Task 2'den önce bu ortam hâlâ `1970-01-01T00:00:00` üretir; Task 2 sonrası da testin ihtiyacı aynıdır, çünkü testte ebeveyn yoktur).

- [ ] **Step 2: Çalıştır, başarısız olduğunu gör**

Run: `uv run pytest tests/test_format_compat.py -v`
Expected: FAIL — `apply_legislative_commit() got an unexpected keyword argument 'law'`

- [ ] **Step 3: Uygula**
  - `models.py`: `CommitKind`, plan alanları, `apply_to` geçişi.
  - `git_engine.py`: trailer regex `(?m)^(In-force|Effective-Date): (\d{4}-\d{2}-\d{2})$`; `_effective_date_from_body` önce son `In-force`, yoksa son `Effective-Date`. `_message_with_trailers(message, *, law, in_force, kind) -> str` eski `_message_with_effective_date`'in yerini alır. `law` boş/yalnız boşluksa ya da `\n` içeriyorsa `ValueError`; `kind` `("baseline","law")` dışındaysa `ValueError`.
  - `parser.py`: kök plan `law=f"{header.law_id} {header.title}".strip()` (başlık boşsa yalnız `law_id`), `kind="baseline"`; değişiklik planları `law=reference`, `kind="law"`.
  - Testlerdeki tüm `apply_legislative_commit` çağrılarına `law=`/`kind=` ekle (kök commit'ler `baseline`, diğerleri `law`); `tests/test_scraper_and_builder.py::_plan` `law=law_id, kind="baseline"`. `test_determinism.py:199,205` assert'leri `In-force: …` olur.

- [ ] **Step 4: Çalıştır**

Run: `uv run pytest -q`
Expected: tümü PASS

- [ ] **Step 5: Commit**

```bash
git add tools/models.py tools/git_engine.py tools/parser.py tests/
git commit -m "feat: write global-law Law/In-force/Kind trailers"
```

---

### Task 2: Git zaman damgası kuralı

**Files:**
- Modify: `tools/git_engine.py:862-878` (`_legislative_env`), yeni `_commit_stamp`
- Modify: `tests/test_determinism.py:191-196,344-345`, `tests/test_parser.py:311` (Git zamanı varsayımları)
- Test: `tests/test_format_compat.py`

**Interfaces:**
- Consumes: Task 1 imzası.
- Produces: `DeterministicGitEngine._commit_stamp(effective_date: str) -> str` — `YYYY-MM-DDTHH:MM:SS+00:00`; `apply_legislative_commit` ve `create_tag` aynı damgayı kullanır (`commit_env` zaten tag'e geçiyor).

- [ ] **Step 1: Failing testleri yaz**

```python
def test_pre_epoch_commits_are_one_second_apart_then_midnight(tmp_path):
    # sırayla: 1899-06-02, 1951-06-01, 1960-08-16, 1970-01-01, 1985-05-05
    stamps = [engine.commit_timestamp(c) for c in engine.list_commits(reverse=True)]
    assert stamps == ["1970-01-01T00:00:00+00:00", "1970-01-01T00:00:01+00:00",
                      "1970-01-01T00:00:02+00:00", "1970-01-01T00:00:02+00:00",
                      "1985-05-05T00:00:00+00:00"]

def test_as_of_before_1970_excludes_real_1970_commit(tmp_path):   # Review Focus 5
    # aynı repo; madde dosyası her commit'te farklı içerik
    assert engine.read_file_as_of(path, "1969-12-31") == <1960 içeriği>
    assert engine.read_file_as_of(path, "1970-01-01") == <1970 içeriği>
    assert engine.read_file_as_of(path, "1951-06-01") == <1951 içeriği>

def test_same_day_post_epoch_commits_resolve_to_last(tmp_path):    # Review Focus 4
    # 2026-01-01 tarihli iki commit, farklı içerik
    assert engine.commit_timestamp("HEAD") == engine.commit_timestamp("HEAD~1")
    assert engine.read_file_as_of(path, "2026-01-01") == <ikinci içerik>
```

`commit_timestamp` biçimi `%cI` çıktısıysa assert'leri o biçime uydur (önce `git_engine.py:771-777`'yi oku; `Z` değil `+00:00` döndüğünü doğrula).

- [ ] **Step 2: Çalıştır**

Run: `uv run pytest tests/test_format_compat.py -v`
Expected: FAIL — ikinci damga `1970-01-01T00:00:00+00:00`

- [ ] **Step 3: Uygula `_commit_stamp`**

Ebeveyn damgası: `has_commits()` ise `git log -1 --format=%cI HEAD`, `datetime.fromisoformat` ile UTC'ye. Kural Global Constraints'teki tablo. `_legislative_env(effective_date, author_name)` damgayı `_commit_stamp`'ten alır; `format_git_date` yalnız doğal damga için kalır. Eski testlerdeki `commit_date(...) == GIT_EPOCH_DATE` assert'leri doğru kalır (tarih kısmı hâlâ 1970-01-01); `test_pre_epoch_queries_respect_real_effective_dates` içindeki "aynı Git zemini" yorumu "aynı Git günü, saniye aralıklı" olarak düzeltilir ve `commit_timestamp` eşitsizliği eklenir.

- [ ] **Step 4: Çalıştır**

Run: `uv run pytest -q`
Expected: tümü PASS (`test_scraper_and_builder.py:275` `timestamps == sorted(timestamps)` dahil)

- [ ] **Step 5: Commit**

```bash
git add tools/git_engine.py tests/
git commit -m "feat: space pre-1970 commits one second apart, keep timestamps monotonic"
```

---

### Task 3: Dal sonlandırma (`future`)

**Files:**
- Modify: `tools/git_engine.py` (yeni metot, `FUTURE_BRANCH` sabiti)
- Test: `tests/test_format_compat.py`

**Interfaces:**
- Produces:
  - `tools.git_engine.FUTURE_BRANCH: Final[str] = "future"`
  - `DeterministicGitEngine.finalize_branches(cutoff: str) -> str | None` — `future` oluşturulduysa onun SHA'sını, yoksa `None` döndürür. `main` ucu son `effective_date(sha) <= cutoff` commit'idir. Hiçbir commit ≤ cutoff değilse `ValueError`. Çalışma ağacı sonunda temiz ve `main` ile aynı.

- [ ] **Step 1: Failing testleri yaz**

```python
def test_finalize_moves_future_commits_off_main(tmp_path):
    # commit'ler: 2020-01-01, 2026-11-02, 2026-12-01, 2026-12-15 ; cutoff 2026-11-02
    future_sha = engine.finalize_branches("2026-11-02")
    assert future_sha == old_head
    assert engine.rev_parse("refs/heads/future") == old_head
    assert engine.effective_date(engine.rev_parse("refs/heads/main")) == "2026-11-02"  # Review Focus 2
    assert engine.head() == engine.rev_parse("refs/heads/main")
    assert engine.is_clean()
    assert (engine.repo_path / path).read_text(encoding="utf-8") == <2026-11-02 içeriği>
    assert engine.read_file_as_of(path, "2026-12-31") == <2026-11-02 içeriği>  # sorgular main okur

def test_finalize_without_future_commits_creates_no_branch(tmp_path):
    assert engine.finalize_branches("2026-11-02") is None
    # git rev-parse --verify -q refs/heads/future → çıkış kodu 1

def test_finalize_rejects_cutoff_before_all_commits(tmp_path):
    with pytest.raises(ValueError):
        engine.finalize_branches("1800-01-01")
    assert engine.head() == old_head
```

- [ ] **Step 2: Çalıştır** — Run: `uv run pytest tests/test_format_compat.py -k finalize -v` — Expected: FAIL `AttributeError`

- [ ] **Step 3: Uygula**

`list_commits(reverse=False)` üzerinde ilk `effective_date <= cutoff` commit'i bul. Uç ise `None`. Değilse `update-ref refs/heads/future <HEAD>` ve `reset --hard --quiet <sha>` (`_base_env` ortamıyla). `cutoff` `validate_iso_date` ile doğrulanır.

- [ ] **Step 4: Çalıştır** — Run: `uv run pytest -q` — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/git_engine.py tests/test_format_compat.py
git commit -m "feat: split post-cutoff commits onto a future branch"
```

---

### Task 4: İnşada kesim tarihi, üretilen `corpus.json`, `STATS.md`

**Files:**
- Modify: `tools/build_corpus.py:175-215` (`CorpusBuilder.__init__`), `:395-460` (plan uygulama), `:500-620` (CLI)
- Modify: `tools/models.py:862-880` (`BuildSummary`)
- Modify: `corpus.json` (`"history": "current-only"`)
- Test: `tests/test_format_compat.py`, mevcut `tests/test_scraper_and_builder.py` yeşil kalmalı

**Interfaces:**
- Consumes: `finalize_branches(cutoff) -> str | None` (Task 3), `LegislativeCommitPlan.effective_date`.
- Produces:
  - `render_corpus_json(corpus_text: str, *, cutoff: str, has_future_branch: bool) -> str` (`tools/build_corpus.py`) — kök alanları korur, `checked_on=cutoff`, `has_future_branch` ekler, Global Constraints JSON biçimi.
  - `CorpusBuilder(..., cutoff: str | None = None)` — `None` ise `json.loads(corpus_text)["checked_on"]`; eksik/geçersizse `ValueError`.
  - `BuildSummary.cutoff: str`, `BuildSummary.future_sha: str | None = None` (`head_sha` = `main` ucu).
  - CLI: `build --cutoff YYYY-MM-DD` (geçersizse `typer.BadParameter`).

- [ ] **Step 1: Failing testleri yaz**

```python
def test_render_corpus_json_sets_cutoff_and_future_flag():
    out = render_corpus_json('{"history": "current-only", "checked_on": "2026-11-02"}\n',
                             cutoff="2026-11-03", has_future_branch=True)
    assert out == '{\n  "checked_on": "2026-11-03",\n  "has_future_branch": true,\n  "history": "current-only"\n}\n'

def test_build_with_cutoff_splits_future_and_filters_stats(tmp_path):
    # mevcut _plan yardımcısı yerine --from-dir fixture'ları + amendment_dates ile
    # bir değişikliği cutoff sonrasına koy (örn. {"41/2018": "2026-12-01"}, cutoff "2026-11-02")
    summary = CorpusBuilder(repo, amendment_dates=..., cutoff="2026-11-02").build_from_dir(src)
    assert summary.future_sha is not None and summary.cutoff == "2026-11-02"
    corpus = json.loads(engine.read_file_at_commit("corpus.json", engine.root_commit()))
    assert corpus["checked_on"] == "2026-11-02" and corpus["has_future_branch"] is True
    stats_md = engine.read_file_at_commit("STATS.md", engine.root_commit())
    # STATS.md'deki "Tarihsel commit" sayısı summary.commit_count - (future'daki commit sayısı)
    # ve tarih aralığı üst sınırı ≤ 2026-11-02

def test_build_twice_with_same_cutoff_is_identical(tmp_path):
    assert (a.head_sha, a.future_sha) == (b.head_sha, b.future_sha)

def test_build_rejects_cutoff_before_all_laws(tmp_path):            # Review Focus 1
    with pytest.raises(ValueError):
        CorpusBuilder(repo, cutoff="1800-01-01").build_from_dir(src)
    assert not DeterministicGitEngine(repo).has_commits()

def test_build_cli_rejects_malformed_cutoff(): # CliRunner, "--cutoff 2026/11/02" → exit_code != 0
```

Fixture seçimi: `tests/test_scraper_and_builder.py`'deki mevcut `--from-dir` testinin kullandığı kaynakları ve `EXPECTED_DATES`'i oku; cutoff'u bu tarihlerin ikisi arasına yerleştir.

- [ ] **Step 2: Çalıştır** — Run: `uv run pytest tests/test_format_compat.py -k "corpus_json or build" -v` — Expected: FAIL `ImportError: render_corpus_json`

- [ ] **Step 3: Uygula**

`merge_commit_plans` sonrası: `in_force = [p for p in merged if p.effective_date <= cutoff]`; boşsa repoya dokunmadan `ValueError`. `has_future = len(in_force) < len(merged)`. `statistics_from_parsed_laws` yalnız `original_date <= cutoff` olan yasa başlıkları/yasaları ve `in_force` tarihleriyle çağrılır. Kök `corpus.json` `render_corpus_json(...)`. Tüm planlar uygulandıktan sonra `future_sha = engine.finalize_branches(cutoff)`. `commit_count` tüm uygulanan commit sayısı olarak kalır. Kök `corpus.json`'da `history` → `"current-only"`.

- [ ] **Step 4: Çalıştır** — Run: `uv run pytest -q` — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/build_corpus.py tools/models.py corpus.json tests/
git commit -m "feat: build with explicit cutoff, generated corpus.json and in-force STATS"
```

---

### Task 5: CI, dokümantasyon ve gerçek önbellekle doğrulama

**Files:**
- Modify: `.github/workflows/ci-and-update.yml:57-80`
- Modify: `AGENTS.md:23-24`, `REPRODUCE.md:10-16,50,67-80,151-152,199-202`, `GUIDE.md:133-160`, `README.md:84-87` (+ `--cutoff` ve `future` dalı açıklaması)

- [ ] **Step 1: CI'yi güncelle**

`weekly-corpus-sync` başında `id: cutoff` adımı: `echo "value=$(date -u +%F)" >> "$GITHUB_OUTPUT"`. Her iki `build_corpus` çağrısına `--cutoff "${{ steps.cutoff.outputs.value }}"`. Determinizm adımına:

```sh
test "$(git -C corpus_repo rev-parse -q --verify refs/heads/future || true)" = \
     "$(git -C corpus_repo_check rev-parse -q --verify refs/heads/future || true)"
```

- [ ] **Step 2: Dokümanları güncelle** — trailer bloğu, damga tablosu (spec §3), kesim tarihi/`future` (spec §4), `history: current-only` gerekçesi (spec §5), "atıfta commit SHA değil `law_id` + madde + yürürlük tarihi kullanın; SHA her haftalık inşada değişir" notu (GUIDE §2 ve README). AGENTS.md'de "strictly UTC, zeroed time" satırı damga tablosunun özetine dönüşür.

- [ ] **Step 3: Gerçek önbellekle iki bağımsız inşa** (ağ yok)

```sh
uv run python -m tools.build_corpus --repo-path "$SP/c1" --from-cache raw_cache --limit 50 --cutoff 2026-11-03
uv run python -m tools.build_corpus --repo-path "$SP/c2" --from-cache raw_cache --limit 50 --cutoff 2026-11-03
test "$(git -C "$SP/c1" rev-parse HEAD)" = "$(git -C "$SP/c2" rev-parse HEAD)"
git -C "$SP/c1" log -1 --format=%B "$(git -C "$SP/c1" rev-list --max-parents=0 HEAD)"
git -C "$SP/c1" log --format=%cI | sort | uniq -d | head
uv run kktc-law --repo-path "$SP/c1" read fasil-113 2 --as-of 1960-01-01
```

`$SP` = oturum scratchpad dizini. Expected: SHA'lar eşit; kök mesajı `Law:`/`In-force:`/`Kind: baseline` ile biter; tekrarlanan damgalar yalnız ≥1970 aynı-gün kümeleri; `read` metni döner. Mevcut `corpus_repo/`'ya dokunma.

- [ ] **Step 4: Tam test + lint** — Run: `uv run pytest -q` ve CI'deki lint komutu (`ci-and-update.yml` `test-and-lint` işindeki ile aynı) — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci-and-update.yml AGENTS.md REPRODUCE.md GUIDE.md README.md
git commit -m "docs, ci: document global-law trailers and pass explicit build cutoff"
```
