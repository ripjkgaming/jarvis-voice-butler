# Personal document librarian — functional candidate

This is a keyword/topic retrieval and source-excerpt MVP, **not semantic intelligence**. It introduces no model calls, source writes, permission changes, browser opens, schedules or startup scans. All tests use synthetic temporary files and fake Google responses. Registration is deliberately reserved for feature 2's shared integration.

## Exact host contract

Host-created service, one instance per authenticated desktop owner:

```python
from librarian import Grant, Librarian
from librarian_index import Index
from librarian_sources import LocalSource, DriveSource
from system.librarian_tools import LibrarianTools

service = Librarian(Index(), {"local": LocalSource(), "drive": DriveSource()})
# Empty grants: metadata discovery performs no file scan or network call.
# Only after an exact human-reviewed source approval, outside the model toolset:
service.approve(Grant("reviewed-id", "local", "/absolute/canonical/approved/folder",
                      content=True, model_disclosure=False), confirmed=True)
# A Drive grant uses an approved folder ID and existing personal/school account.
# It covers direct children only, never the whole account or descendants.
# Install/renew grants only from a trusted configuration flow, never from tools.
# A Sheets grant needs an explicit tab/range, default Sheet1!A1:Z19 (494 cells).
# Model-facing answers require a separately reviewed model_disclosure=True grant.
# Renewing a grant purges prior refs/indexes. Revoking immediately removes them.
service.revoke("reviewed-id")

# After integration's JARVIS_LIBRARIAN gate and reviewed registration:
# tools = LibrarianTools(service).tools
```

`Index()` is in memory. An explicitly supplied private derived-state SQLite path is supported; no default home directory or personal file is accessed during construction. Use a dedicated per-owner directory under `$JARVIS_HOME/librarian/`. New database files have mode 0600; a nonprivate existing database or symlink is refused. Grants are intentionally not persisted: restart requires host approval reconstruction and renewing a grant purges its derived data.

`content=False` permits metadata discovery only; indexing/answers are denied. `model_disclosure=False` permits approved core/local extraction but denies excerpt delivery through the model-facing answer tool. Discovery metadata can be returned as part of approved discovery. No user content goes to logs, memory, telemetry or jobs here. Once a model receives approved content, revocation cannot recall that prior context.

### Public core operations

- `approve(Grant(...), confirmed=True)` and `revoke(grant_id)`: trusted host only; not tools. Grant includes source, scope, existing account, content flag, disclosure flag, MIME set, bounded sheet range, optional expiry and cache TTL up to seven days.
- `find(query, limit=8, *, cancel=None, safety=None)`: approved metadata and already-indexed keyword/topic matches. Maximum eight output candidates. No name-based automatic read/selection. Opaque source refs are stable only for the lifetime of a grant, not arbitrary URL/path capabilities.
- `index_source(ref, *, cancel=None, safety=None)`: explicitly selected approved file, atomic index generation, no collection scan. Host-only manual refresh after `stale` status.
- `answer(ref, question, locator='', *, cancel=None, safety=None, model_output=False)`: bounded extractive evidence; selected source can be indexed on first question only with content approval. `model_output=True` requires disclosure approval. Locator `line N` or `page N` returns that exact location, with literal matching passages otherwise. No inference, unsupported arithmetic/summary claims or hidden model fallback.
- `duplicates(refs, *, cancel=None, safety=None)`: at most 20 selected sources, current verified metadata, existing approved hashes only. Never downloads new content merely for duplicate discovery. Results label `identical_bytes`, `same_extracted_text` or `possible_duplicate`.
- `close()`: closes derived-state database. No thread/worker is started by the service.

The `cancel` parameter accepts a `threading.Event`. `safety` is a no-argument authority callback invoked before reads and publication; it can raise the host's existing revocation error. `LibrarianTools` supplies local/guest/private-phone/revocation checks, copies existing approval ticket context, offloads work and sets the cancellation event on async cancellation. Running reads settle within their bound; cancellation suppresses publication, never promises an in-flight read was forcibly terminated. No detached jobs or shared diagnostic writes.

### Three optional voice tools

`librarian_find(query: str, limit: int=8)`
`librarian_answer(source_ref: str, question: str, locator: str='')`
`librarian_duplicates(source_refs: list[str])`

No approval creation, bulk indexing, visible opening, external writes or permission tool is exported. Private calls and guests are rejected until a separately reviewed content-access design exists. Nonlocal execution is rejected by existing `require_local`. There is no patch to private-phone/WhatsApp logic.

### JSON results

Common: `{status, mode:'keyword_topic_extract', say, evidence:[], partial}`.

Find adds `candidates:[{ref,title,mime,source,account,path_or_url,modified_at,version,match_reason}]`, `coverage`, `errors`. Discovery has explicit caps and incomplete Drive pagination surfaces `partial=true`; it does not claim completeness. Duplicate titles require selecting an opaque ref. There is no ordinal selection tool, so no “result 2” state can silently move between searches.

Answer adds `answer` (literal excerpts), `ref`, `untrusted_source:true`, and evidence entries `{ref,title,source,account,path_or_url,version,locator,quote,offset,indexed_at,verified_at,truncated}`. Locators are line numbers, physical PDF pages, Docs tab + structural path/index, or exact A1 cell. Quotes are source text; embedded instructions have no authority. At most six passages, 1,000 characters each. Missing evidence yields `no_evidence`, not a fabricated answer.

Statuses include `ok`, `needs_approval`, `needs_content_approval`, `needs_disclosure_approval`, `no_match`, `no_evidence`, `not_found`, `unsupported`, `too_large`, `denied`, `unauthenticated`, `unavailable`, `stale`, `cancelled`. These are retrieval lifecycle statuses, not feature 2's ActionOutcome schema; no new verified action effect is introduced.

## Bounds and freshness

100 source records/service, 1,000 local directory entries/discovery, depth four, direct-child Drive folder query, 10 MiB read response/file, 200,000 extracted characters, 512 chunks/file of 1,024 characters, 200 physical PDF pages, 500 explicitly approved sheet cells within A1:ZZ10000. Optional FTS was removed from the MVP: bounded Python token ranking over SQLite chunks avoids a new dependency and has identical literal/synonym limits. Synonyms are a tiny visible static map for electricity/electrical and budget/expenses; unsupported paraphrases have no intelligence claim.

Local reads open approved root and each component with descriptors and `O_NOFOLLOW`, verify root device/inode captured at approval, reject hidden paths/special files and check version before/after reads. Replaced roots need fresh approval. Drive metadata validates selected ID, approved direct parent, MIME and trash state; all metadata/content calls carry the grant account. The GET transport reads bounded responses, refuses redirects, sanitizes 401/403/404/429 and never requests new scopes. Native Docs/Sheets use their structured read APIs; stored text/PDF uses `alt=media`, not indiscriminate export.

Every cached answer checks live readability/version before and after evidence assembly. Stale or retention-expired content is purged and requires explicit `index_source(ref)` refresh. Lost permissions/deletion/source approval removes content/hash eligibility. No offline answer fallback is implemented. Cache TTL is enforced on use; there is no idle retention sweeper in this MVP. Persistent storage should remain disabled if hard wall-clock deletion while idle is required. Revocation purges immediately; SQLite uses secure deletion and rollback journaling. No cryptographic erasure/backups guarantee is claimed.

## Real PDF parser and runtime limitations

Installed Poppler `pdfinfo` and `pdftotext` 26.01.0 are now the default PDF parser for approved local and stored Drive PDFs. No package was installed or dependency/lockfile modified. `src/librarian_pdf.py` copies already-approved bounded bytes into a private temporary directory, verifies physical page count and unencrypted status with pdfinfo, then extracts UTF-8 text with pdftotext. Default form-feed boundaries preserve blank pages. Page count and extracted boundaries must agree before any evidence is published. No passwords, OCR, JavaScript execution, exports or model calls are involved.

Each parser invocation runs through an isolated Python child that sets hard limits **before** exec: 512 MiB virtual memory, two CPU seconds, four wall seconds, 32 file descriptors, no core dump, 64 KiB info output or 800,000 text-output bytes. Both stages together can take at most eight wall seconds. Input is capped at 10 MiB, page count at 200, and extracted text at 200,000 characters plus the existing chunk bound. No `preexec_fn` is used in threads. A timeout settles only the parser's own child. Parser stderr is discarded; user source data is not printed or logged. Temporary source/output files are removed on success and failure. Resource limits are not a full filesystem/network sandbox; stronger parser containment can be added separately before handling adversarial collections.

Real synthetic PDF tests cover four-page extraction, a blank second page without shifting page-four provenance, core and stored-Drive page answers, image-only/scanned and blank files, corrupt/empty/oversize inputs, page-count preflight, actual text-output cap, missing-tool fallback, sanitized timeout handling, page-boundary mismatch and temporary cleanup. They use a tiny test-only PDF fixture writer rather than installing reportlab. The injected parser seam remains supported for fixture/alternative-host use, but PDF readiness is now backed by actual Poppler parsing.

If Poppler is absent on another host, return `unsupported`; the minimal optional system dependency is the reputable **poppler-utils** package providing both `pdfinfo` and `pdftotext`, not a model or OCR stack. Installation must be coordinated. POSIX resource-limit support is required; fail closed before parser exec if limits cannot be installed. Encrypted PDFs remain outside scope. No promise is made for mangled fonts or inaccessible text; extraction failure abstains instead of claiming an answer.

No LiveKit SDK, ruff or async pytest plugin is installed in this execution environment. Voice decorator construction is tested with a fake SDK; installed LiveKit 1.8.2 registration/JSON schema remains an integration acceptance gate. No agent routing edit was made, so URL tool names, original orb, active Assigner v4 and the applied Google visible-open fix remain untouched. Core tests run with standard-library unittest under both system pytest and Python 3.11. Managed-environment thread selector wakeup failed in unmodified `asyncio.run(asyncio.to_thread(...))` too; wrapper tests use a test-only event-loop timer during executor teardown, no production workaround.

## Offline verification and later live gate

Run from the candidate workspace without installing dependencies:

```bash
PYTHONPATH=src UV_CACHE_DIR=/tmp/jarvis-feature10-uv uv run --offline --no-project python -m pytest tests -q
```

Tests cover explicit grants and disclosure, metadata-only boundaries, line/page/structural/cell evidence, topic limits, unknown refs, scoped account propagation, moved/denied/deleted sources, expiry, stale/race reads, atomic rollback, root/symlink replacement, duplicate labels, parser/read caps, sanitized fake HTTP failures, GET-only endpoints, redirect refusal, cancellation and private/guest/local checks. All source bytes are synthetic fixtures; no real Drive call, token read, capture or model call occurred.

After shared integration, separately authorize synthetic live folders/account checks, synthetic Poppler checks on the target host and installed-SDK schema construction. Authenticate only existing accounts, no broader scopes. Keep opening acceptance with its owner. User cap remains 6.1 Sol medium; no new model has been added.

## Rollback

Do not register new tools or remove only their feature-gated registration. Close the service and remove only the ten new files in this candidate if rollback is needed. A clear-cache request may delete the dedicated derived SQLite state; never originals, tokens, Second Brain/memory state or other jobs. No shared-source modifications or migrations are part of the patch.

API shapes checked against current official references: [Drive list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list), [Drive get](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get), [Docs get](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get), [Sheets values get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get), [LiveKit function tools](https://docs.livekit.io/agents/logic/tools/definition/). Actual installed-SDK construction and live Google behavior were not tested.

Parser references: [pdftotext installed-tool contract](https://manpages.debian.org/bookworm/poppler-utils/pdftotext.1.en.html), [pdfinfo page/encryption metadata](https://manpages.debian.org/bookworm/poppler-utils/pdfinfo.1.en.html), [Python resource limits](https://docs.python.org/3/library/resource.html). Installed 26.01.0 help/version output was also checked.
