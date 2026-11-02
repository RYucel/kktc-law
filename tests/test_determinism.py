"""Faz 1 determinizm testleri.

Aynı ham mevzuat verisinden iki bağımsız repoda birebir aynı Git geçmişinin (aynı commit
SHA-1'leri) üretildiğini ve tarihsel sorguların çalışma ağacını bozmadan doğru sürümü
döndürdüğünü doğrular.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from tools.git_engine import (
    COLONIAL_AUTHOR_NAME,
    DEFAULT_AUTHOR_NAME,
    GIT_EPOCH_DATE,
    DeterministicGitEngine,
    GitEngineError,
)

DATE_1951 = "1951-06-01"
DATE_2018 = "2018-09-05"
MADDE_004_PATH = "laws/fasil-113/madde-004.md"
METADATA_PATH = "laws/fasil-113/metadata.json"

CORPUS_JSON = """{
  "name": "Turkish Republic of Northern Cyprus",
  "jurisdiction": "trnc",
  "label": "KKTC Mevzuatı (Birleştirilmiş Yasalar ve Fasıllar)",
  "language": ["tr"],
  "sources": [
    {"id": "mahkemeler", "name": "KKTC Mahkemeler Mevzuat Bilgi Sistemi", "url": "https://www.mahkemeler.net"},
    {"id": "meclis", "name": "KKTC Cumhuriyet Meclisi", "url": "https://www.cm.gov.ct.tr"}
  ],
  "checked_on": "2026-11-02",
  "history": "replayed"
}
"""

MADDE_001_V1951 = """# Madde 1: Kısa İsim

Bu Yasa, Şirketler Yasası olarak isimlendirilir.
"""

MADDE_004_V1951 = """# Madde 4: Şirket Tüzel Kişiliği

Bir şirket, kuruluş belgesinin tescili tarihinden itibaren tüzel kişilik kazanır.
Şirketin tüzel kişiliği, tasfiye işlemlerinin tamamlanmasına kadar devam eder.
"""

MADDE_004_V2018 = """# Madde 4: Şirketin Tüzel Kişiliği ve Temsili

Bir şirket, kuruluş belgesinin tescili tarihinden itibaren tüzel kişilik kazanır.
Şirket, esas sözleşmesinde aksine bir hüküm bulunmadıkça müdürleri tarafından temsil edilir.
Tüzel kişilik, tasfiye işlemlerinin tamamlanmasına kadar devam eder.

(41/2018 Sayılı Değişiklik Yasası ile değişik)
"""

MESSAGE_1951 = (
    "fasil-113: Şirketler Yasası (Fasıl 113) 1951-06-01 tarihinde yürürlüğe girdi."
)
MESSAGE_2018 = (
    "41/2018 Sayılı Değişiklik Yasası (2018-09-05): fasil-113 madde-004 yeniden düzenlendi."
)


def _metadata_1951() -> dict[str, object]:
    return {
        "law_id": "fasil-113",
        "type": "fasil",
        "number": "113",
        "title": "Şirketler Yasası",
        "original_date": DATE_1951,
        "consolidation_history": [],
    }


def _metadata_2018() -> dict[str, object]:
    metadata = _metadata_1951()
    metadata["consolidation_history"] = [
        {
            "amending_law": "41/2018",
            "effective_date": DATE_2018,
            "affected_articles": ["madde-004"],
            "commit_message": MESSAGE_2018,
        }
    ]
    return metadata


def _metadata_text(metadata: dict[str, object], *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(metadata, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    return json.dumps(metadata, ensure_ascii=False)


def _initial_files(*, crlf: bool, reverse: bool) -> dict[str, str]:
    files: dict[str, str] = {
        "corpus.json": CORPUS_JSON,
        f"{METADATA_PATH}": _metadata_text(_metadata_1951()),
        "laws/fasil-113/madde-001.md": MADDE_001_V1951,
        MADDE_004_PATH: MADDE_004_V1951,
    }
    if reverse:
        files = dict(reversed(list(files.items())))
    if crlf:
        files = {path: content.replace("\n", "\r\n") for path, content in files.items()}
    return files


def _amendment_files(*, crlf: bool, reverse: bool) -> dict[str, str]:
    files: dict[str, str] = {
        MADDE_004_PATH: MADDE_004_V2018,
        METADATA_PATH: _metadata_text(_metadata_2018(), pretty=True),
    }
    if reverse:
        files = dict(reversed(list(files.items())))
    if crlf:
        files = {path: content.replace("\n", "\r\n") for path, content in files.items()}
    return files


def _build_repo(
    repo_path: Path, *, crlf: bool = False, reverse: bool = False
) -> DeterministicGitEngine:
    """Fasıl 113'ün 1951 ilk yürürlük ve 2018 değişiklik commit'lerini uygular."""
    engine = DeterministicGitEngine(repo_path)
    engine.apply_legislative_commit(
        effective_date=DATE_1951,
        author_name=COLONIAL_AUTHOR_NAME,
        commit_message="  " + MESSAGE_1951 + "  \r\n",
        file_changes=_initial_files(crlf=crlf, reverse=reverse),
        tag_name="v1951-06-01",
    )
    engine.apply_legislative_commit(
        effective_date=DATE_2018,
        author_name=DEFAULT_AUTHOR_NAME,
        commit_message=MESSAGE_2018,
        file_changes=_amendment_files(crlf=crlf, reverse=reverse),
        tag_name="v2018-09-05",
    )
    return engine


def _force_rmtree(path: Path) -> None:
    """Git nesneleri Windows'ta salt-okunur olduğu için silmeden önce izinleri açar."""
    for item in [path, *path.rglob("*")]:
        try:
            os.chmod(item, stat.S_IWRITE)
        except OSError:
            pass
    shutil.rmtree(path)


@pytest.fixture()
def repos(tmp_path: Path) -> tuple[DeterministicGitEngine, DeterministicGitEngine]:
    repo_a = _build_repo(tmp_path / "repo_a")
    # İkinci repo, farklı sözlük sırası ve CRLF içerikle beslenir: normalize edilmelidir.
    repo_b = _build_repo(tmp_path / "repo_b", crlf=True, reverse=True)
    return repo_a, repo_b


def test_two_repos_produce_identical_hashes(
    repos: tuple[DeterministicGitEngine, DeterministicGitEngine],
) -> None:
    engine_a, engine_b = repos

    head_a = engine_a.head()
    head_b = engine_b.head()
    assert head_a == head_b
    assert len(head_a) == 40

    first_a = engine_a.root_commit()
    first_b = engine_b.root_commit()
    assert first_a == first_b

    # Aynı içerik => aynı ağaç; iki repo da aynı dosya kümesini taşır.
    assert engine_a.list_files_at_commit("HEAD") == engine_b.list_files_at_commit("HEAD")

    # Etiket nesneleri de deterministik olmalı.
    tag_a = engine_a.rev_parse("refs/tags/v2018-09-05")
    tag_b = engine_b.rev_parse("refs/tags/v2018-09-05")
    assert tag_a == tag_b

    # Zaman damgaları sabit UTC gece yarısı olmalı.
    assert engine_a.commit_date("HEAD") == DATE_2018
    # 1970 öncesi yürürlük tarihleri Git zeminine taşınır; gerçek tarih trailer'da korunur.
    assert engine_b.commit_date(first_b) == GIT_EPOCH_DATE
    assert engine_b.effective_date(first_b) == DATE_1951
    assert engine_a.effective_date("HEAD") == DATE_2018
    assert engine_a.commit_timestamp("HEAD").startswith(f"{DATE_2018}T00:00:00")

    body = engine_a.commit_message("HEAD")
    assert f"Effective-Date: {DATE_2018}" in body
    assert "Sayılı Değişiklik Yasası" in body

    root_body = engine_a.commit_message(first_a)
    assert "Şirketler Yasası" in root_body
    assert "yürürlüğe girdi" in root_body
    assert f"Effective-Date: {DATE_1951}" in root_body


def test_historical_reads_return_correct_version(
    repos: tuple[DeterministicGitEngine, DeterministicGitEngine],
) -> None:
    engine_a, engine_b = repos

    for engine in (engine_a, engine_b):
        version_1951 = engine.read_file_as_of(MADDE_004_PATH, "2010-01-01")
        version_2018 = engine.read_file_as_of(MADDE_004_PATH, "2020-01-01")

        assert version_1951 is not None
        assert version_2018 is not None

        assert version_1951 == MADDE_004_V1951
        assert version_2018 == MADDE_004_V2018
        assert "Şirketin tüzel kişiliği" in version_1951
        assert "müdürleri tarafından temsil edilir" not in version_1951
        assert "müdürleri tarafından temsil edilir" in version_2018

        # Tarihsel okuma çalışma ağacını değiştirmemeli.
        on_disk = (engine.repo_path / MADDE_004_PATH).read_text(encoding="utf-8")
        assert on_disk == MADDE_004_V2018
        assert engine.is_clean()

    # İlk commit öncesi ve hiç var olmamış dosya None döner.
    assert engine_a.read_file_as_of(MADDE_004_PATH, "1951-05-31") is None
    assert engine_a.read_file_as_of(MADDE_004_PATH, "1951-06-02") == MADDE_004_V1951
    assert engine_a.read_file_as_of(MADDE_004_PATH, "1969-12-31") == MADDE_004_V1951
    assert engine_a.read_file_as_of("laws/fasil-113/madde-999.md", "2020-01-01") is None

    # Kök commit'te 1951 metni, HEAD'de 2018 metni bulunur.
    assert engine_a.read_file_at_commit(MADDE_004_PATH, engine_a.root_commit()) == MADDE_004_V1951
    assert engine_a.read_file_at_commit(MADDE_004_PATH, "HEAD") == MADDE_004_V2018


def test_metadata_history_matches_effective_dates(
    repos: tuple[DeterministicGitEngine, DeterministicGitEngine],
) -> None:
    engine_a, _ = repos

    before = engine_a.read_file_as_of(METADATA_PATH, "2010-01-01")
    after = engine_a.read_file_as_of(METADATA_PATH, "2020-01-01")
    assert before is not None and after is not None

    metadata_before = json.loads(before)
    metadata_after = json.loads(after)
    assert metadata_before["consolidation_history"] == []
    assert [record["amending_law"] for record in metadata_after["consolidation_history"]] == [
        "41/2018"
    ]
    assert metadata_after["consolidation_history"][0]["effective_date"] == DATE_2018
    assert metadata_after["consolidation_history"][0]["affected_articles"] == ["madde-004"]


def test_files_are_normalized_on_disk(
    repos: tuple[DeterministicGitEngine, DeterministicGitEngine],
) -> None:
    engine_a, _ = repos

    for relative_path in (
        "corpus.json",
        METADATA_PATH,
        "laws/fasil-113/madde-001.md",
        MADDE_004_PATH,
    ):
        raw = (engine_a.repo_path / relative_path).read_bytes()
        assert b"\r\n" not in raw, f"CRLF bulundu: {relative_path}"
        assert raw.endswith(b"\n")
        assert not raw.endswith(b"\n\n")
        decoded = raw.decode("utf-8")
        assert decoded == decoded.strip() + "\n" or decoded == "\n"

    # JSON anahtarları alfabetik ve 2 boşluk girintili yazılmalı.
    corpus_text = (engine_a.repo_path / "corpus.json").read_text(encoding="utf-8")
    expected = (
        json.dumps(json.loads(CORPUS_JSON), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    assert corpus_text == expected
    assert corpus_text.startswith('{\n  "checked_on"')


def test_rebuild_after_deleting_repo_produces_same_sha(tmp_path: Path) -> None:
    first_engine = _build_repo(tmp_path / "repo_rebuild")
    first_head = first_engine.head()
    first_root = first_engine.root_commit()

    _force_rmtree(tmp_path / "repo_rebuild")
    assert not (tmp_path / "repo_rebuild").exists()

    rebuilt_engine = _build_repo(tmp_path / "repo_rebuild")
    assert rebuilt_engine.head() == first_head
    assert rebuilt_engine.root_commit() == first_root


def test_repealed_article_is_deleted_from_working_tree_and_head(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "repo_repeal")
    engine.apply_legislative_commit(
        DATE_1951,
        COLONIAL_AUTHOR_NAME,
        MESSAGE_1951,
        {
            "laws/fasil-113/madde-001.md": MADDE_001_V1951,
            "laws/fasil-113/madde-002.md": "# Madde 2: Kapsam\n\nKapsam metni.\n",
        },
    )
    engine.apply_legislative_commit(
        DATE_2018,
        DEFAULT_AUTHOR_NAME,
        "2/2018 Sayılı Değişiklik Yasası: madde-002 yürürlükten kaldırıldı.",
        {},
        deleted_paths=["laws/fasil-113/madde-002.md"],
    )

    assert engine.read_file_as_of("laws/fasil-113/madde-002.md", "2010-01-01") is not None
    assert engine.read_file_as_of("laws/fasil-113/madde-002.md", "2020-01-01") is None
    assert "laws/fasil-113/madde-002.md" not in engine.list_files_at_commit("HEAD")
    assert not (engine.repo_path / "laws/fasil-113/madde-002.md").exists()
    assert engine.is_clean()


def test_pre_epoch_queries_respect_real_effective_dates(tmp_path: Path) -> None:
    """1970 öncesi iki ayrı yürürlük tarihi, Git zemini paylaşsa da doğru ayrılmalı."""
    engine = DeterministicGitEngine(tmp_path / "repo_pre_epoch")
    path = "laws/fasil-148/madde-001.md"
    engine.apply_legislative_commit(
        "1951-06-01",
        COLONIAL_AUTHOR_NAME,
        "fasil-148: ilk metin.",
        {path: "# Madde 1: Kısa İsim\n\n1951 metni.\n"},
    )
    engine.apply_legislative_commit(
        "1960-08-16",
        COLONIAL_AUTHOR_NAME,
        "fasil-148: değişiklik.",
        {path: "# Madde 1: Kısa İsim\n\n1960 metni.\n"},
    )

    # İki commit de aynı Git zeminini taşır.
    assert engine.commit_date("HEAD") == engine.commit_date(engine.root_commit()) == GIT_EPOCH_DATE
    assert engine.effective_date(engine.root_commit()) == "1951-06-01"
    assert engine.effective_date("HEAD") == "1960-08-16"

    assert engine.read_file_as_of(path, "1949-01-01") is None
    assert engine.read_file_as_of(path, "1955-01-01") == "# Madde 1: Kısa İsim\n\n1951 metni.\n"
    assert engine.read_file_as_of(path, "1965-01-01") == "# Madde 1: Kısa İsim\n\n1960 metni.\n"


def test_engine_rejects_invalid_inputs(tmp_path: Path) -> None:
    engine = DeterministicGitEngine(tmp_path / "repo_invalid")
    valid_files = {"laws/fasil-113/madde-001.md": MADDE_001_V1951}

    with pytest.raises(ValueError):
        engine.apply_legislative_commit("1951/06/01", COLONIAL_AUTHOR_NAME, "x", valid_files)

    with pytest.raises(ValueError):
        engine.apply_legislative_commit(DATE_1951, COLONIAL_AUTHOR_NAME, "x", {})

    with pytest.raises(ValueError):
        engine.apply_legislative_commit(
            DATE_1951, COLONIAL_AUTHOR_NAME, "x", {"../kacis.md": "içerik"}
        )

    with pytest.raises(ValueError):
        engine.apply_legislative_commit(DATE_1951, COLONIAL_AUTHOR_NAME, "x", {"a.json": "{"})

    with pytest.raises(ValueError):
        engine.apply_legislative_commit(
            DATE_1951, COLONIAL_AUTHOR_NAME, "x", valid_files, tag_name="1951-06-01"
        )

    with pytest.raises(ValueError):
        engine.apply_legislative_commit(
            DATE_1951,
            COLONIAL_AUTHOR_NAME,
            "x",
            valid_files,
            deleted_paths=["laws/fasil-113/madde-001.md"],
        )

    with pytest.raises(ValueError):
        engine.apply_legislative_commit(
            DATE_1951,
            COLONIAL_AUTHOR_NAME,
            "x",
            {},
            deleted_paths=["laws/fasil-113/yok.md"],
        )

    assert engine.apply_legislative_commit(
        DATE_1951, COLONIAL_AUTHOR_NAME, "İlk yürürlük", valid_files
    )

    # Aynı içerikle ikinci kez commit atılamaz.
    with pytest.raises(GitEngineError):
        engine.apply_legislative_commit(
            DATE_1951, COLONIAL_AUTHOR_NAME, "İlk yürürlük", valid_files
        )
