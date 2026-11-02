# AGENTS.md — kktc-law (Git for TRNC Law)

## Project Overview
`kktc-law` encodes the legislation of the Turkish Republic of Northern Cyprus (TRNC / KKTC) as a deterministic Git repository following the `lexifina/global-law` standard. Every law/chapter (Fasıl or Yasa) is a directory, every article (`Madde`) is an individual Markdown file, and every legislative enactment or amendment is a chronological Git commit stamped with its official effective date.

## Tech Stack & Tooling
- **Language:** Python 3.11+
- **Package & Venv Manager:** `uv` (preferred) or standard `pip` with `pyproject.toml`
- **Data Validation:** `pydantic` (v2)
- **HTTP & Scraping:** `httpx` (async), `beautifulsoup4`, `lxml`, `tenacity`
- **CLI & MCP:** `typer`, `mcp` (FastMCP)
- **Testing:** `pytest`, `pytest-asyncio`

## Critical Architecture Rules (MUST FOLLOW)

### 1. Deterministic Git History (`tools/git_engine.py`)
Rebuilding the legislation repository from the same raw inputs MUST produce **bit-for-bit identical Git tree and commit SHA-1 hashes** on any machine at any time.
- **Environment Overrides:** Every `git commit` subprocess call MUST explicitly override the environment with:
  - `GIT_AUTHOR_NAME`: Derived from the enacting authority (default: `"KKTC Cumhuriyet Meclisi"` or `"Kıbrıs Koloni İdaresi"` for pre-1960 Fasıllar).
  - `GIT_AUTHOR_EMAIL`: `"mevzuat@kktc-law.local"`
  - `GIT_COMMITTER_NAME`: `"kktc-law-bot"`
  - `GIT_COMMITTER_EMAIL`: `"bot@kktc-law.local"`
  - `GIT_AUTHOR_DATE`: `"YYYY-MM-DDT00:00:00+00:00"` (strictly UTC, zeroed time).
  - `GIT_COMMITTER_DATE`: Identical to `GIT_AUTHOR_DATE`.
- ** Repo Config:** Initialize target corpus repositories with:
  - `git config core.autocrlf false`
  - `git config core.fileMode false`
  - Default branch explicitly set to `main` (`git init -b main`).
- **File Ordering & Formatting:**
  - Always sort file paths and JSON keys (`json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False)`) before writing.
  - Enforce Unix line endings (`\n`) and a single trailing newline on every generated `.md` and `.json` file.
  - Never embed dynamic runtime timestamps (`datetime.now()`) inside tracked files.

### 2. Non-Destructive Git Queries (`tools/law.py` & `tools/mcp_server.py`)
- NEVER run `git checkout` on the corpus repository to read historical law states, as this mutates the working tree and breaks concurrent MCP/CLI queries.
- Always resolve historical states in-memory using `git rev-list -n 1 --before="YYYY-MM-DDT23:59:59+00:00" main` followed by `git show <commit_sha>:<relative_path>` or `git grep <pattern> <commit_sha>`.

### 3. TRNC Legislation Naming & Normalization Conventions
- **Pre-1960 Chapters (Fasıllar):** Slug format `fasil-<number>` (e.g., `laws/fasil-113/`).
- **Modern Laws (Yasalar):** Slug format `yasa-<YYYY>-<number>` (e.g., `42/2024 Sayılı Yasa` -> `laws/yasa-2024-42/`).
- **Articles (Maddeler):** Zero-padded 3-digit filenames: `madde-001.md`, `madde-002.md`. Temporary articles (`Geçici Madde`) use `gecici-madde-001.md`.
- **Encoding:** Normalize all scraped Turkish text from `windows-1254` / `iso-8859-9` to `utf-8` (NFC form) and strip redundant whitespace.

## Coding Standards for DeepSeek
- Write modular, strictly typed Python 3.11+ code (`from __future__ import annotations`).
- Avoid placeholder comments (`# TODO`, `pass`); write complete implementations.
- Keep `tools/` scripts independent from the target `corpus_repo/` git history if operating in builder mode, or support operating directly inside the corpus root via `--repo-path`.
- Run `pytest` after every module implementation and fix any failures before concluding a task.