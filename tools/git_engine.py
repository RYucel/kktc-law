"""Deterministik Git motoru.

Aynı ham mevzuat girdisinden, her makinede ve her tarihte **birebir aynı** Git ağacını ve
commit SHA-1 değerlerini üreten motor. Determinizm şu sabitlemelerle sağlanır:

* `git init -b main`, `core.autocrlf=false`, `core.fileMode=false` ve ilgili yerel ayarlar,
* her commit için açık `GIT_AUTHOR_*` / `GIT_COMMITTER_*` kimliği ve UTC gece yarısı tarihi,
* kullanıcı/system Git yapılandırmasının tamamen devre dışı bırakılması,
* UTF-8 (BOM'suz), NFC, `\\n` satır sonlu ve tek son satır sonu ile biten dosyalar,
* alfabetik sıralı dosya yolları, `json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False)`,
* `git checkout` kullanmayan, `git rev-list` + `git show` tabanlı tarihsel okuma.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final

from tools.models import (
    FileChange,
    LawMetadata,
    SearchHit,
    normalize_article_id,
    validate_iso_date,
)

__all__ = [
    "COLONIAL_AUTHOR_NAME",
    "DEFAULT_AUTHOR_NAME",
    "DEFAULT_BRANCH",
    "EFFECTIVE_DATE_TRAILER",
    "GIT_EPOCH_DATE",
    "DeterministicGitEngine",
    "GitEngineError",
    "is_repealed_text",
]

DEFAULT_BRANCH: Final[str] = "main"
DEFAULT_AUTHOR_NAME: Final[str] = "KKTC Cumhuriyet Meclisi"
COLONIAL_AUTHOR_NAME: Final[str] = "Kıbrıs Koloni İdaresi"
DEFAULT_AUTHOR_EMAIL: Final[str] = "mevzuat@kktc-law.local"
COMMITTER_NAME: Final[str] = "kktc-law-bot"
COMMITTER_EMAIL: Final[str] = "bot@kktc-law.local"

#: Git, 1970-01-01 öncesi zaman damgalarını (negatif `time_t`) nesne biçiminde kabul etmez.
#: Bu nedenle 1970 öncesi yürürlük tarihleri bu sabit "zemin"e taşınır; gerçek tarih commit
#: mesajındaki `Effective-Date` trailer'ında ve `metadata.json` içinde korunur.
GIT_EPOCH_DATE: Final[str] = "1970-01-01"
EFFECTIVE_DATE_TRAILER: Final[str] = "Effective-Date"

_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^v\d{4}-\d{2}-\d{2}$")
_EMPTY_TREE_SHA: Final[str] = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_LAW_ID_QUERY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:fasil-\d+|yasa-\d{4}-\d+|anayasa)$"
)
_STAGE_CHUNK_SIZE: Final[int] = 500
_EFFECTIVE_DATE_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?m)^{re.escape(EFFECTIVE_DATE_TRAILER)}: (\d{{4}}-\d{{2}}-\d{{2}})$"
)
_REPEAL_MARKERS: Final[tuple[str, ...]] = (
    "yürürlükten kaldırılmıştır",
    "yürürlükten kaldırılmış",
    "mülga",
    "ilga edilmiştir",
)
_INHERITED_GIT_VARS: Final[tuple[str, ...]] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
)


class GitEngineError(RuntimeError):
    """Git motorunda oluşan determinizm veya çağrı hataları."""


def is_repealed_text(content: str) -> bool:
    """Bir madde metninin yürürlükten kaldırılmış (mülga) olduğunu sezgisel olarak belirler."""
    folded = content.replace("İ", "i").replace("I", "ı").casefold()
    return any(marker in folded for marker in _REPEAL_MARKERS)


class DeterministicGitEngine:
    """Sabitlenmiş parametrelerle Git geçmişi inşa eden motor."""

    def __init__(
        self,
        repo_path: str | os.PathLike[str],
        *,
        author_name: str | None = None,
        initialize: bool = True,
    ) -> None:
        self.repo_path = Path(repo_path).expanduser().resolve()
        self.default_author_name = author_name or DEFAULT_AUTHOR_NAME
        if initialize:
            self.initialize()

    # ------------------------------------------------------------------ kurulum

    def initialize(self) -> None:
        """Hedef dizinde (yoksa) `main` dallı repoyu oluşturur ve ayarları sabitler."""
        self.repo_path.mkdir(parents=True, exist_ok=True)
        if not self.is_repository():
            self._run_git(["init", "-b", DEFAULT_BRANCH])
        self._configure_repository()

    def is_repository(self) -> bool:
        """Hedef dizinin bir Git çalışma ağacı olup olmadığını döndürür."""
        return (self.repo_path / ".git").exists()

    def _configure_repository(self) -> None:
        settings: dict[str, str] = {
            "core.autocrlf": "false",
            "core.fileMode": "false",
            "core.symlinks": "false",
            "core.precomposeunicode": "false",
            "core.quotepath": "false",
            "commit.gpgsign": "false",
            "tag.gpgSign": "false",
            "i18n.commitEncoding": "utf-8",
            "i18n.logOutputEncoding": "utf-8",
            "user.name": COMMITTER_NAME,
            "user.email": COMMITTER_EMAIL,
            "init.defaultBranch": DEFAULT_BRANCH,
        }
        for key, value in settings.items():
            self._run_git(["config", "--local", key, value])

    # ---------------------------------------------------------------- commit atma

    def apply_legislative_commit(
        self,
        effective_date: str,
        author_name: str,
        commit_message: str,
        file_changes: dict[str, str],
        tag_name: str | None = None,
        *,
        deleted_paths: Sequence[str] = (),
    ) -> str:
        """Bir yürürlük/değişiklik olayını tek bir deterministik commit olarak yazar.

        Args:
            effective_date: Yürürlük günü, `YYYY-MM-DD`.
            author_name: Yürürlüğe koyan otorite (örn. `Kıbrıs Koloni İdaresi`).
            commit_message: Commit mesajı; LF'e normalize edilip tek son satır sonu eklenir.
            file_changes: Göreli yol -> dosya içeriği. `.json` içerikleri sıralı biçimde yazılır.
            tag_name: Verilirse `vYYYY-MM-DD` biçiminde açıklamalı (annotated) tag oluşturulur.
            deleted_paths: Yürürlükten kaldırılan (dosyası silinecek) maddeler.

        Returns:
            Oluşan commit'in tam SHA-1 değeri.

        Raises:
            ValueError: Tarih, yol, mesaj veya JSON içeriği geçersizse.
            GitEngineError: Git komutu başarısızsa ya da commit edilecek değişiklik yoksa.
        """
        iso_date = validate_iso_date(effective_date)
        if not author_name or not author_name.strip():
            raise ValueError("author_name boş olamaz")
        if not file_changes and not deleted_paths:
            raise ValueError("file_changes ve deleted_paths birlikte boş olamaz")
        normalized_tag = self._normalize_tag_name(tag_name) if tag_name is not None else None

        message = self._message_with_effective_date(
            self._normalize_commit_message(commit_message), iso_date
        )
        payloads: dict[str, bytes] = {}
        for raw_path, content in file_changes.items():
            relative_path = self._normalize_relative_path(raw_path)
            payloads[relative_path] = self._encode_payload(relative_path, content)

        removals = sorted({self._normalize_relative_path(path) for path in deleted_paths})
        overlap = set(payloads).intersection(removals)
        if overlap:
            raise ValueError(
                "Aynı dosya hem yazılıp hem silinemez: " + ", ".join(sorted(overlap))
            )

        written: list[str] = []
        for relative_path in sorted(payloads):
            target = self.repo_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payloads[relative_path])
            try:
                os.chmod(target, 0o644)
            except OSError:
                # Windows'ta izin bitleri anlamsızdır; Git `100644` kaydeder.
                pass
            written.append(relative_path)

        for relative_path in removals:
            target = self.repo_path / relative_path
            if not target.exists():
                raise ValueError(f"Silinecek dosya bulunamadı: {relative_path}")
            target.unlink()
            written.append(relative_path)

        changed_paths = sorted(set(written))
        for offset in range(0, len(changed_paths), _STAGE_CHUNK_SIZE):
            self._run_git(
                ["add", "--", *changed_paths[offset : offset + _STAGE_CHUNK_SIZE]]
            )

        diff = self._run_git(
            ["diff", "--cached", "--quiet", "--no-renames", "--no-ext-diff", "--no-textconv"],
            allow_failure=True,
        )
        if diff.returncode == 0:
            raise GitEngineError(
                "Commit edilecek değişiklik yok: dosya içerikleri HEAD ile aynı. "
                f"Yürürlük tarihi: {iso_date}"
            )
        if diff.returncode not in (0, 1):  # pragma: no cover - savunma amaçlı
            raise GitEngineError(self._decode(diff.stderr).strip())

        commit_env = self._legislative_env(iso_date, author_name.strip())
        self._run_git(
            [
                "-c",
                "commit.cleanup=verbatim",
                "commit",
                "--no-verify",
                "--no-gpg-sign",
                "--quiet",
                "-F",
                "-",
            ],
            env=commit_env,
            stdin=message,
        )

        sha = self.head()
        if normalized_tag is not None:
            self.create_tag(normalized_tag, message=message, commit_sha=sha, env=commit_env)
        return sha

    def create_tag(
        self,
        tag_name: str,
        *,
        message: str | None = None,
        commit_sha: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Açıklamalı (annotated) ve deterministik bir tag oluşturup SHA-1'ini döndürür."""
        tag = self._normalize_tag_name(tag_name)
        target = commit_sha or self.head()
        tag_env = env or self._legislative_env(
            self.effective_date(target), self.default_author_name
        )
        body = message or f"{tag}\n"
        self._run_git(
            [
                "-c",
                "tag.gpgSign=false",
                "tag",
                "--annotate",
                "--force",
                "--cleanup=verbatim",
                "-F",
                "-",
                tag,
                target,
            ],
            env=tag_env,
            stdin=self._normalize_commit_message(body),
        )
        return self.rev_parse(f"refs/tags/{tag}")

    # -------------------------------------------------------------- tarihsel okuma

    def read_file_as_of(
        self, relative_path: str, as_of_date: str | None = None
    ) -> str | None:
        """Belirli bir tarihte yürürlükte olan dosya içeriğini çalışma ağacını bozmadan okur.

        `resolve_commit_as_of` ile tarihteki son commit bulunur, ardından
        `git show <sha>:<path>` ile içerik belleğe alınır. `git checkout` **kullanılmaz**.
        1970 öncesi tarihlerde adaylar `Effective-Date` trailer'ına göre daraltılır.

        Args:
            relative_path: Repo köküne göre dosya yolu.
            as_of_date: `YYYY-MM-DD`; `None` ise `HEAD` kullanılır.

        Returns:
            UTF-8 çözülmüş dosya içeriği; dosya o tarihte mevcut değilse `None`.
        """
        path = self._normalize_relative_path(relative_path)
        sha = self.resolve_commit_as_of(as_of_date)
        if sha is None:
            return None
        return self.read_file_at_commit(path, sha)

    def read_file_at_commit(self, relative_path: str, commit_sha: str) -> str | None:
        """Belirli bir commit'teki (veya `HEAD`/tag gibi revizyondaki) dosya içeriğini döndürür."""
        path = self._normalize_relative_path(relative_path)
        revision = commit_sha.strip() if isinstance(commit_sha, str) else ""
        if not revision:
            raise ValueError("Commit revizyonu boş olamaz")
        if not re.fullmatch(r"[0-9a-fA-F]{4,64}", revision):
            revision = self.rev_parse(revision)
        result = self._run_git(
            ["show", f"{revision}:{path}"],
            allow_failure=True,
        )
        if result.returncode != 0:
            return None
        try:
            return self._decode(result.stdout)
        except UnicodeDecodeError as exc:  # pragma: no cover - yazdığımız dosyalar UTF-8
            raise GitEngineError(f"Dosya UTF-8 olarak çözülemedi: {path}") from exc

    def resolve_commit_as_of(self, as_of_date: str | None = None) -> str | None:
        """Verilen günde yürürlükte olan son commit'in SHA-1'ini döndürür.

        `as_of_date=None` ise doğrudan `HEAD` çözümlenir. 1970 ve sonrası için
        `git rev-list --before` kullanılır. 1970 öncesi tarihlerde tüm yasama commit'leri
        Git zemin tarihine (1970-01-01) taşındığı için adaylar commit mesajlarındaki
        `Effective-Date` trailer'ı ile daraltılır; böylece Fasıl dönemi (1951 gibi)
        sorguları da doğru sonucu verir.
        """
        if as_of_date is None:
            return self.head() if self.has_commits() else None

        iso_date = validate_iso_date(as_of_date)
        if not self.has_commits():
            return None

        probe_date = iso_date if iso_date >= GIT_EPOCH_DATE else GIT_EPOCH_DATE
        result = self._run_git(
            [
                "rev-list",
                "-n",
                "1",
                f"--before={probe_date}T23:59:59+00:00",
                DEFAULT_BRANCH,
            ],
            allow_failure=True,
        )
        if result.returncode != 0:
            raise GitEngineError(self._decode(result.stderr).strip())
        sha = self._decode(result.stdout).strip()
        if not sha:
            return None
        if iso_date >= GIT_EPOCH_DATE:
            return sha
        return self._refine_pre_epoch(iso_date)

    def _refine_pre_epoch(self, as_of_date: str) -> str | None:
        """1970 öncesi sorgularda gerçek yürürlük tarihine göre son commit'i seçer."""
        result = self._run_git(
            [
                "log",
                "-z",
                "--format=%H%x1f%B",
                f"--before={GIT_EPOCH_DATE}T23:59:59+00:00",
                DEFAULT_BRANCH,
            ],
            allow_failure=True,
        )
        if result.returncode != 0:  # pragma: no cover - savunma amaçlı
            raise GitEngineError(self._decode(result.stderr).strip())

        best: str | None = None
        best_date = ""
        for record in self._decode(result.stdout).split("\x00"):
            if not record.strip():
                continue
            sha, separator, body = record.partition("\x1f")
            if not separator:
                continue
            real_date = self._effective_date_from_body(body)
            if real_date is None or real_date > as_of_date:
                continue
            if best is None or real_date > best_date:
                best = sha.strip()
                best_date = real_date
        return best

    # ---------------------------------------------------------------- sorgu API'si

    @staticmethod
    def _normalize_law_id(law_id: str) -> str:
        if not isinstance(law_id, str):
            raise ValueError(f"law_id metin olmalıdır, alınan: {type(law_id).__name__}")
        candidate = law_id.strip()
        if not _LAW_ID_QUERY_RE.match(candidate):
            raise ValueError(
                "law_id 'fasil-<sayı>', 'yasa-<YYYY>-<sayı>' veya 'anayasa' olmalıdır: "
                f"{law_id!r}"
            )
        return candidate

    def _article_path(self, law_id: str, article_id: str) -> str:
        target_law = self._normalize_law_id(law_id)
        target_article = normalize_article_id(article_id)
        return self._normalize_relative_path(f"laws/{target_law}/{target_article}.md")

    def _commits_touching(self, relative_path: str) -> set[str]:
        """Bir dosyayı değiştiren commit SHA-1'lerini (tüm geçmiş) döndürür."""
        path = self._normalize_relative_path(relative_path)
        result = self._run_git(
            ["log", "--format=%H", "--reverse", "HEAD", "--", path],
            allow_failure=True,
        )
        if result.returncode != 0:
            return set()
        return {
            line.strip()
            for line in self._decode(result.stdout).splitlines()
            if line.strip()
        }

    def _parent_sha(self, commit_sha: str) -> str | None:
        result = self._run_git(
            ["show", "-s", "--format=%P", commit_sha],
            allow_failure=True,
        )
        if result.returncode != 0:
            return None
        parents = self._decode(result.stdout).split()
        return parents[0] if parents else None

    def _commit_history_details(self, commit_sha: str) -> tuple[str, str, str]:
        """`(author_name, subject, effective_date)` üçlüsünü tek Git çağrısında okur."""
        result = self._run_git(
            ["show", "-s", "--format=%an%x1f%s%x1f%B", commit_sha]
        )
        author, separator, remainder = self._decode(result.stdout).partition("\x1f")
        subject, separator_two, body = remainder.partition("\x1f")
        if not separator or not separator_two:  # pragma: no cover - savunma amaçlı
            raise GitEngineError(f"Commit bilgileri çözümlenemedi: {commit_sha}")
        effective = self._effective_date_from_body(body) or self.commit_date(commit_sha)
        return author.strip(), subject.strip(), effective

    def _metadata_mentions_article(
        self,
        law_id: str,
        commit_sha: str,
        effective_date: str,
        article_id: str,
    ) -> bool:
        """Commit'in metadata geçmişi, maddeyi o gün için değiştirilmiş sayıyor mu?"""
        text = self.read_file_at_commit(f"laws/{law_id}/metadata.json", commit_sha)
        if text is None:
            return False
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return False
        history = payload.get("consolidation_history") if isinstance(payload, dict) else None
        if not isinstance(history, list):
            return False
        for record in history:
            if not isinstance(record, dict):
                continue
            if record.get("effective_date") != effective_date:
                continue
            affected = record.get("affected_articles")
            if isinstance(affected, list) and article_id in affected:
                return True
        return False

    def list_laws_as_of(self, as_of_date: str | None = None) -> list[LawMetadata]:
        """Verilen tarihte yürürlükte olan yasaların `metadata.json` kayıtlarını döndürür.

        Çözümlenen commit üzerinde `git ls-tree` ile `laws/*/metadata.json` yolları
        bulunur ve her biri `git show <sha>:<path>` ile belleğe alınır. Çalışma ağacı
        değiştirilmez. Sonuç `law_id`'ye göre sıralıdır.
        """
        sha = self.resolve_commit_as_of(as_of_date)
        if sha is None:
            return []
        result = self._run_git(
            ["ls-tree", "-r", "--name-only", sha, "--", "laws"],
            allow_failure=True,
        )
        if result.returncode != 0:
            raise GitEngineError(self._decode(result.stderr).strip())

        laws: list[LawMetadata] = []
        for path in sorted(self._decode(result.stdout).splitlines()):
            match = re.fullmatch(r"laws/([^/]+)/metadata\.json", path)
            if match is None:
                continue
            text = self.read_file_at_commit(path, sha)
            if text is None:  # pragma: no cover - ls-tree ile tutarlı olmalı
                continue
            try:
                payload = json.loads(text)
                metadata = LawMetadata.model_validate(payload)
            except ValueError as exc:
                raise GitEngineError(f"Geçersiz metadata.json ({path}): {exc}") from exc
            if metadata.law_id != match.group(1):
                raise GitEngineError(
                    f"metadata.json law_id '{metadata.law_id}' dizin adı "
                    f"'{match.group(1)}' ile uyuşmuyor"
                )
            laws.append(metadata)
        laws.sort(key=lambda item: item.law_id)
        return laws

    def get_law_metadata(
        self, law_id: str, as_of_date: str | None = None
    ) -> LawMetadata | None:
        """Tek bir yasanın belirli tarihteki metadata kaydını döndürür."""
        target = self._normalize_law_id(law_id)
        for metadata in self.list_laws_as_of(as_of_date):
            if metadata.law_id == target:
                return metadata
        return None

    def read_article_as_of(
        self,
        law_id: str,
        article_id: str,
        as_of_date: str | None = None,
    ) -> str | None:
        """Bir maddenin belirli tarihteki metnini working tree'yi bozmadan döndürür."""
        return self.read_file_as_of(self._article_path(law_id, article_id), as_of_date)

    def get_article_history(
        self, law_id: str, article_id: str
    ) -> list[dict[str, str]]:
        """Bir maddenin tüm geçmiş commit'lerini kronolojik olarak döndürür.

        Dosya içeriği değişmese bile `metadata.json` konsolidasyon geçmişinde o gün
        için etkilenen madde olarak işaretlenen commit'ler `güncellendi` sayılır.
        Durum değerleri: `eklendi`, `güncellendi`, `silindi`.

        Returns:
            `commit_sha`, `effective_date`, `author_name`, `subject` ve `status`
            anahtarlarını taşıyan sözlükler; en eski commit önce gelir.
        """
        target_law = self._normalize_law_id(law_id)
        target_article = normalize_article_id(article_id)
        path = self._article_path(target_law, target_article)
        if not self.has_commits():
            return []

        file_commits = self._commits_touching(path)
        metadata_commits = self._commits_touching(f"laws/{target_law}/metadata.json")
        relevant = file_commits | metadata_commits
        if not relevant:
            return []

        history: list[dict[str, str]] = []
        for sha in self.list_commits(reverse=True):
            if sha not in relevant:
                continue

            content = self.read_file_at_commit(path, sha)
            parent_sha = self._parent_sha(sha)
            parent_content = (
                self.read_file_at_commit(path, parent_sha) if parent_sha else None
            )
            author, subject, effective = self._commit_history_details(sha)

            status: str | None = None
            if content is not None and parent_content is None:
                status = "eklendi"
            elif content is None and parent_content is not None:
                status = "silindi"
            elif content is not None and parent_content is not None:
                if content != parent_content:
                    status = "güncellendi"
                elif sha in metadata_commits and self._metadata_mentions_article(
                    target_law, sha, effective, target_article
                ):
                    status = "güncellendi"
            if status is None:
                continue

            history.append(
                {
                    "commit_sha": sha,
                    "effective_date": effective,
                    "author_name": author,
                    "subject": subject,
                    "status": status,
                }
            )
        return history

    def diff_article(
        self,
        law_id: str,
        article_id: str,
        from_date: str,
        to_date: str,
    ) -> str:
        """İki tarih arasındaki madde dosyası için unified diff döndürür."""
        path = self._article_path(law_id, article_id)
        from_iso = validate_iso_date(from_date)
        to_iso = validate_iso_date(to_date)
        from_sha = self.resolve_commit_as_of(from_iso)
        to_sha = self.resolve_commit_as_of(to_iso)
        if from_sha is None and to_sha is None:
            return ""
        # İlk commit'ten önceki bir tarih "boş ağaç" olarak ele alınır; böylece
        # 1951-01-01 → 2020 gibi sorgular dosyanın tamamının eklenmesini gösterir.
        if from_sha is None:
            from_sha = _EMPTY_TREE_SHA
        if to_sha is None:
            to_sha = _EMPTY_TREE_SHA
        if from_sha == to_sha:
            return ""

        result = self._run_git(
            [
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--unified=3",
                from_sha,
                to_sha,
                "--",
                path,
            ],
            allow_failure=True,
        )
        if result.returncode != 0:
            raise GitEngineError(self._decode(result.stderr).strip())
        return self._decode(result.stdout)

    def search_legislation(
        self,
        query: str,
        as_of_date: str | None = None,
        law_id: str | None = None,
    ) -> list[SearchHit]:
        """Çözümlenen commit üzerinde `git grep` ile tam metin araması yapar.

        Args:
            query: Aranacak sabit metin (büyük/küçük harf duyarsız).
            as_of_date: `None` ise `HEAD`; aksi halde o tarihteki son commit.
            law_id: Verilirse yalnızca `laws/<law_id>/` altında arar.

        Returns:
            Madde dosyalarındaki eşleşmeler; `law_id`, `article_id`, `line_number`,
            `snippet` alanlarıyla deterministik sıralı.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Arama sorgusu boş olamaz")
        if "\x00" in query:
            raise ValueError("Arama sorgusu NUL karakteri içeremez")

        sha = self.resolve_commit_as_of(as_of_date)
        if sha is None:
            return []

        if law_id is not None:
            pathspec = f"laws/{self._normalize_law_id(law_id)}/"
        else:
            pathspec = "laws/"

        result = self._run_git(
            ["grep", "-n", "-i", "-F", "-e", query, sha, "--", pathspec],
            allow_failure=True,
        )
        if result.returncode == 1:
            return []
        if result.returncode != 0:
            raise GitEngineError(self._decode(result.stderr).strip())

        prefix = f"{sha}:"
        hits: list[SearchHit] = []
        for raw_line in self._decode(result.stdout).splitlines():
            line = raw_line[len(prefix) :] if raw_line.startswith(prefix) else raw_line
            fields = line.split(":", 2)
            if len(fields) != 3:
                continue
            path, line_number_raw, snippet = fields
            parts = PurePosixPath(path).parts
            if len(parts) != 3 or parts[0] != "laws" or not parts[2].endswith(".md"):
                continue
            try:
                line_number = int(line_number_raw)
            except ValueError:  # pragma: no cover - git grep biçimi
                continue
            snippet_text = snippet.strip()
            if not snippet_text:
                continue
            hits.append(
                SearchHit(
                    law_id=parts[1],
                    article_id=parts[2][:-3],
                    line_number=line_number,
                    snippet=snippet_text,
                )
            )
        hits.sort(key=lambda hit: (hit.law_id, hit.article_id, hit.line_number))
        return hits

    # -------------------------------------------------------------------- sorgular

    def has_commits(self) -> bool:
        """Repo en az bir commit içeriyor mu?"""
        if not self.is_repository():
            return False
        result = self._run_git(["rev-parse", "--verify", "--quiet", "HEAD"], allow_failure=True)
        return result.returncode == 0

    def head(self) -> str:
        """`HEAD` commit'inin tam SHA-1 değerini döndürür."""
        if not self.has_commits():
            raise GitEngineError("Repo henüz commit içermiyor")
        return self.rev_parse("HEAD")

    def rev_parse(self, revision: str) -> str:
        """Bir revizyonu (dal, tag, `HEAD`, SHA) tam nesne kimliğine çözer."""
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("Revizyon boş olamaz")
        result = self._run_git(["rev-parse", "--verify", revision.strip()])
        sha = self._decode(result.stdout).strip()
        if not sha:  # pragma: no cover - savunma amaçlı
            raise GitEngineError(f"Revizyon çözümlenemedi: {revision!r}")
        return sha

    def root_commit(self) -> str:
        """İlk (kök) commit'in SHA-1 değerini döndürür."""
        if not self.has_commits():
            raise GitEngineError("Repo henüz commit içermiyor")
        result = self._run_git(["rev-list", "--max-parents=0", "HEAD"])
        roots = self._decode(result.stdout).split()
        if not roots:  # pragma: no cover - savunma amaçlı
            raise GitEngineError("Kök commit bulunamadı")
        return roots[0].strip()

    def list_commits(self, revision: str = "HEAD", *, reverse: bool = False) -> list[str]:
        """Bir revizyondan erişilebilen commit SHA-1'lerini listeler (varsayılan: yeniden eskiye).

        Args:
            revision: Başlangıç revizyonu (varsayılan `HEAD`).
            reverse: `True` ise en eski commit önce gelir.

        Returns:
            Tam SHA-1 değerlerinden oluşan liste.
        """
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("Revizyon boş olamaz")
        args = ["rev-list"]
        if reverse:
            args.append("--reverse")
        args.append(revision.strip())
        result = self._run_git(args)
        return [line.strip() for line in self._decode(result.stdout).splitlines() if line.strip()]

    def commit_date(self, commit_sha: str = "HEAD") -> str:
        """Git zaman damgasını `YYYY-MM-DD` olarak döndürür (1970 öncesi zemine taşınır)."""
        return self.commit_timestamp(commit_sha)[:10]

    def commit_timestamp(self, commit_sha: str = "HEAD") -> str:
        """Commit'in Git yazar tarihini tam ISO 8601 biçiminde (offset dahil) döndürür."""
        result = self._run_git(["show", "-s", "--format=%aI", commit_sha])
        raw = self._decode(result.stdout).strip()
        if not raw:  # pragma: no cover - savunma amaçlı
            raise GitEngineError(f"Commit tarihi okunamadı: {commit_sha}")
        return raw

    def commit_message(self, commit_sha: str = "HEAD") -> str:
        """Commit mesajının tam gövdesini (trailer dahil) döndürür."""
        result = self._run_git(["show", "-s", "--format=%B", commit_sha])
        return self._decode(result.stdout)

    def commit_subject(self, commit_sha: str = "HEAD") -> str:
        """Commit mesajının ilk satırını (subject) döndürür."""
        result = self._run_git(["show", "-s", "--format=%s", commit_sha])
        return self._decode(result.stdout).strip()

    def commit_author(self, commit_sha: str = "HEAD") -> str:
        """Commit yazarının adını döndürür."""
        result = self._run_git(["show", "-s", "--format=%an", commit_sha])
        return self._decode(result.stdout).strip()

    def changed_files_between(
        self,
        from_sha: str,
        to_sha: str,
        *,
        pathspec: str = "laws",
    ) -> list[FileChange]:
        """İki commit arasında değişen dosyaları `git diff --name-status` ile döndürür."""
        if not from_sha or not to_sha:
            raise ValueError("from_sha ve to_sha boş olamaz")
        result = self._run_git(
            [
                "diff",
                "--name-status",
                "--no-renames",
                "--no-ext-diff",
                from_sha,
                to_sha,
                "--",
                pathspec,
            ],
            allow_failure=True,
        )
        if result.returncode != 0:
            raise GitEngineError(self._decode(result.stderr).strip())
        changes: list[FileChange] = []
        for raw_line in self._decode(result.stdout).splitlines():
            fields = raw_line.split("\t", 1)
            if len(fields) != 2:
                continue
            status_raw, path = fields[0].strip().upper(), fields[1].strip()
            status = {"A": "added", "M": "modified", "D": "deleted"}.get(status_raw)
            if status is None:
                continue
            changes.append(FileChange(path=path, status=status))
        return sorted(changes, key=lambda item: (item.path, item.status))

    def effective_date(self, commit_sha: str = "HEAD") -> str:
        """Commit'in gerçek yürürlük tarihini (`Effective-Date` trailer'ı) döndürür.

        Trailer bulunamazsa Git zaman damgasına geri düşer.
        """
        parsed = self._effective_date_from_body(self.commit_message(commit_sha))
        if parsed is not None:
            return parsed
        return self.commit_date(commit_sha)

    def last_effective_date(self) -> str:
        """`HEAD` commit'inin gerçek yürürlük gününü döndürür."""
        return self.effective_date("HEAD")

    def list_files_at_commit(self, commit_sha: str) -> list[str]:
        """Bir commit'teki tüm dosya yollarını alfabetik olarak döndürür."""
        result = self._run_git(["ls-tree", "-r", "--name-only", commit_sha])
        return sorted(self._decode(result.stdout).splitlines())

    def is_clean(self) -> bool:
        """Çalışma ağacında izlenmeyen/değişmiş dosya yok mu?"""
        result = self._run_git(["status", "--porcelain"], allow_failure=True)
        return result.returncode == 0 and not self._decode(result.stdout).strip()

    # -------------------------------------------------------------------- yardımcı

    @staticmethod
    def format_git_date(effective_date: str) -> str:
        """`YYYY-MM-DD` tarihini Git'in sabit UTC damgasına dönüştürür."""
        return f"{validate_iso_date(effective_date)}T00:00:00+00:00"

    def _legislative_env(self, effective_date: str, author_name: str) -> dict[str, str]:
        env = self._base_env()
        iso_date = validate_iso_date(effective_date)
        stamp = self.format_git_date(iso_date if iso_date >= GIT_EPOCH_DATE else GIT_EPOCH_DATE)
        env.update(
            {
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": DEFAULT_AUTHOR_EMAIL,
                "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_NAME": COMMITTER_NAME,
                "GIT_COMMITTER_EMAIL": COMMITTER_EMAIL,
                "GIT_COMMITTER_DATE": stamp,
                "EMAIL": DEFAULT_AUTHOR_EMAIL,
            }
        )
        return env

    @staticmethod
    def _base_env() -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in _INHERITED_GIT_VARS}
        env.update(
            {
                # Determinizm: kullanıcı/system Git ayarları ve template'leri devre dışı.
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_ADVICE": "0",
                "TZ": "UTC",
                "LC_ALL": "C",
                "LANG": "C",
                "LANGUAGE": "C",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    def _run_git(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        stdin: str | bytes | None = None,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["git", "-c", "safe.directory=*", *args]
        input_bytes: bytes | None
        if stdin is None:
            input_bytes = None
        elif isinstance(stdin, str):
            input_bytes = stdin.encode("utf-8")
        else:
            input_bytes = stdin
        completed = subprocess.run(  # noqa: S603 - argümanlar liste halinde, shell yok
            command,
            cwd=str(self.repo_path),
            env=env or self._base_env(),
            input=input_bytes,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0 and not allow_failure:
            stderr = self._decode(completed.stderr).strip()
            raise GitEngineError(
                f"Git komutu başarısız (kod {completed.returncode}): {' '.join(command)}: {stderr}"
            )
        return completed

    @staticmethod
    def _decode(raw: bytes) -> str:
        return raw.decode("utf-8", errors="strict")

    @staticmethod
    def _message_with_effective_date(normalized_message: str, effective_date: str) -> str:
        """Commit mesajına makine-okunur `Effective-Date` trailer'ını ekler."""
        base = normalized_message.rstrip("\n")
        return f"{base}\n\n{EFFECTIVE_DATE_TRAILER}: {effective_date}\n"

    @staticmethod
    def _effective_date_from_body(body: str) -> str | None:
        """Commit gövdesindeki son `Effective-Date` trailer'ını `YYYY-MM-DD` olarak döndürür."""
        matches = _EFFECTIVE_DATE_RE.findall(body.replace("\r\n", "\n"))
        if not matches:
            return None
        try:
            return validate_iso_date(matches[-1])
        except ValueError:
            return None

    @staticmethod
    def _normalize_commit_message(message: str) -> str:
        if not isinstance(message, str):
            raise ValueError(f"Commit mesajı metin olmalıdır, alınan: {type(message).__name__}")
        if "\x00" in message:
            raise ValueError("Commit mesajı NUL karakteri içeremez")
        normalized = message.replace("\r\n", "\n").replace("\r", "\n")
        normalized = unicodedata.normalize("NFC", normalized)
        normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
        normalized = normalized.strip("\n")
        if not normalized:
            raise ValueError("Commit mesajı boş olamaz")
        return f"{normalized}\n"

    @staticmethod
    def _normalize_tag_name(tag_name: str) -> str:
        if not isinstance(tag_name, str) or not _TAG_RE.match(tag_name.strip()):
            raise ValueError(f"Tag adı 'vYYYY-MM-DD' biçiminde olmalıdır, alınan: {tag_name!r}")
        candidate = tag_name.strip()
        validate_iso_date(candidate[1:])
        return candidate

    @staticmethod
    def _normalize_relative_path(raw_path: str) -> str:
        if not isinstance(raw_path, str):
            raise ValueError(f"Dosya yolu metin olmalıdır, alınan: {type(raw_path).__name__}")
        if "\x00" in raw_path:
            raise ValueError("Dosya yolu NUL karakteri içeremez")
        candidate = raw_path.strip().replace("\\", "/")
        if not candidate:
            raise ValueError("Dosya yolu boş olamaz")
        if candidate.startswith("./"):
            candidate = candidate[2:]
        pure = PurePosixPath(candidate)
        if pure.is_absolute() or ":" in pure.parts[0]:
            raise ValueError(f"Repo dışına işaret eden yol reddedildi: {raw_path!r}")
        if any(part in ("", ".", "..") for part in pure.parts):
            raise ValueError(f"Yol bileşenleri geçersiz: {raw_path!r}")
        if pure.parts[0] == ".git":
            raise ValueError(f".git dizini altına yazılamaz: {raw_path!r}")
        return pure.as_posix()

    @staticmethod
    def _encode_payload(relative_path: str, content: str) -> bytes:
        if not isinstance(content, str):
            raise ValueError(
                f"Dosya içeriği metin olmalıdır ({relative_path}), alınan: {type(content).__name__}"
            )
        if relative_path.lower().endswith(".json"):
            try:
                parsed: Any = json.loads(content)
                serialized = json.dumps(parsed, sort_keys=True, indent=2, ensure_ascii=False)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Geçersiz JSON içeriği ({relative_path}): {exc}") from exc
            text = f"{unicodedata.normalize('NFC', serialized)}\n"
        else:
            normalized = content.replace("\r\n", "\n").replace("\r", "\n")
            normalized = unicodedata.normalize("NFC", normalized)
            text = normalized.rstrip("\n") + "\n"
        return text.encode("utf-8")
