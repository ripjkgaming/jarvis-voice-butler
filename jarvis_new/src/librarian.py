"""Personal librarian keyword/topic MVP: approved reads and extractive evidence.

No semantic model, background indexing, original writes, or opening tools.
Grants can only be installed by a trusted host/configuration flow, not an LLM tool.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from librarian_index import Index
from librarian_sources import ALLOWED, FILE_ID, SourceError, check_range

STOP = frozenset(
    "a an the my about what does say is in of to for how find notes document file page line".split()
)
SYNONYMS = {
    "electricity": {"electric", "current", "circuit"},
    "electrical": {"electric", "current", "circuit"},
    "budget": {"budget", "cost", "expense"},
    "expenses": {"expense", "cost", "budget"},
}


def terms(query):
    result = set()
    for word in re.findall(r"[\w]+", query.casefold())[:24]:
        if word not in STOP:
            result.add(word)
            result.update(SYNONYMS.get(word, ()))
    return result


def score(query_terms, text):
    words = set(re.findall(r"[\w]+", text.casefold()))
    return len(words & query_terms)


@dataclass(frozen=True)
class Grant:
    grant_id: str
    source: str
    scope: str
    account: str = "personal"
    content: bool = False
    model_disclosure: bool = False
    mimes: frozenset[str] = ALLOWED
    sheet_range: str = "Sheet1!A1:Z19"
    expires_at: float = 0
    retention_s: int = 7 * 24 * 3600
    root_identity: tuple[int, int] | None = None


class Librarian:
    """One instance per authenticated owner. Memory index unless explicitly supplied."""

    def __init__(self, index=None, adapters=None, *, clock=time.time):
        self.index = index if index is not None else Index()
        self.adapters = adapters or {}
        self.clock = clock
        self._grants = {}
        self._lock = threading.RLock()

    def close(self):
        self.index.close()

    def approve(self, grant, *, confirmed=False):
        """Host-only exact reviewed grant. Not exposed through LibrarianTools."""
        if confirmed is not True:
            raise ValueError("Explicit approval is required before source discovery.")
        if (
            not isinstance(grant, Grant)
            or not grant.grant_id
            or grant.source not in self.adapters
        ):
            raise ValueError("Unknown approved source.")
        if grant.account not in ("personal", "school"):
            raise ValueError(
                "Use an existing account; no new OAuth scopes are requested."
            )
        if (
            not grant.mimes
            or not grant.mimes <= ALLOWED
            or not 0 < grant.retention_s <= 7 * 24 * 3600
        ):
            raise ValueError("Invalid approved type or retention bound.")
        if grant.source == "local":
            root = Path(grant.scope)
            if (
                not root.is_absolute()
                or root.is_symlink()
                or str(root.resolve()) != str(root)
            ):
                raise ValueError(
                    "Approved root must be an absolute canonical directory without symlinks."
                )
            if not root.is_dir():
                raise ValueError("Approved local directory does not exist.")
            info = root.stat()
            grant = replace(grant, root_identity=(info.st_dev, info.st_ino))
        elif grant.source == "drive" and not FILE_ID.fullmatch(grant.scope):
            raise ValueError(
                "Approve one existing Drive folder ID; descendants are not included."
            )
        check_range(grant.sheet_range)
        if grant.expires_at and grant.expires_at <= self.clock():
            raise ValueError("Approval has expired.")
        with self._lock:
            # A renewed/replaced scope never inherits retained text or old references.
            self.index.purge(grant.grant_id)
            self._grants[grant.grant_id] = grant

    def revoke(self, grant_id):
        with self._lock:
            self._grants.pop(grant_id, None)
            self.index.purge(grant_id)

    def revoke_all(self):
        """Host lifecycle revocation; purge every approved derived source."""
        with self._lock:
            for grant_id in tuple(self._grants):
                self.revoke(grant_id)

    def _check(self, grant, cancel=None, safety=None):
        if cancel is not None and cancel.is_set():
            raise SourceError(
                "cancelled",
                "Librarian operation cancelled; no late result will be published.",
            )
        if safety is not None:
            safety()
        with self._lock:
            if self._grants.get(grant.grant_id) is not grant:
                raise SourceError("denied", "Source approval was revoked or replaced.")
            if grant.expires_at and grant.expires_at <= self.clock():
                self.revoke(grant.grant_id)
                raise SourceError("denied", "Source approval has expired.")

    def _result(self, status, say, **fields):
        return {
            "status": status,
            "mode": "keyword_topic_extract",
            "say": say,
            "evidence": [],
            "partial": False,
            **fields,
        }

    def _failure(self, exc, ref=None):
        if ref and exc.status in ("denied", "not_found", "unauthenticated", "stale"):
            self.index.invalidate(ref)
        return self._result(exc.status, str(exc))

    def _source(self, ref, cancel=None, safety=None):
        row = self.index.get(ref)
        if row is None:
            raise SourceError(
                "not_found",
                "Select an opaque reference from the current approved results.",
            )
        with self._lock:
            grant = self._grants.get(row["grant_id"])
        if grant is None:
            self.index.purge(row["grant_id"])
            raise SourceError("denied", "Source no longer has an active approval.")
        self._check(grant, cancel, safety)
        return row, grant, self.adapters[grant.source]

    def _fresh(self, ref, *, cancel=None, safety=None):
        row, grant, adapter = self._source(ref, cancel, safety)
        self._check(grant, cancel, safety)
        meta = adapter.metadata(grant, row["key"])
        self._check(grant, cancel, safety)
        with self._lock:
            self._check(grant, cancel, safety)
            self.index.upsert(ref, grant.grant_id, row["key"], meta)
        if row["indexed_version"] and (
            row["indexed_version"] != meta["version"]
            or self.clock() - row["indexed_at"] > grant.retention_s
        ):
            self.index.invalidate(ref)
            raise SourceError(
                "stale",
                "Source changed or its cache expired; explicitly refresh this source.",
            )
        return row, grant, adapter, meta

    def find(self, query, limit=8, *, cancel=None, safety=None):
        query = " ".join(str(query or "").split())[:200]
        if not query:
            return self._result(
                "no_match",
                "Provide filename or content keywords; meaning search is limited to explicit topics.",
            )
        limit = max(1, min(8, int(limit)))
        with self._lock:
            grants = list(self._grants.values())
        if not grants:
            return self._result(
                "needs_approval", "Approve a specific source before document discovery."
            )
        query_terms = terms(query)
        hits = {}
        errors = []
        partial = False
        for grant in grants:
            check = lambda g=grant: self._check(g, cancel, safety)
            try:
                check()
                found, capped = self.adapters[grant.source].discover(
                    grant, query, 100, check
                )
                partial |= capped
                for meta in found:
                    with self._lock:
                        check()
                        ref = self.index.upsert(
                            uuid.uuid4().hex, grant.grant_id, meta["key"], meta
                        )
                    if ref is None:
                        partial = True
                        continue
                    hits[ref] = (
                        10 + score(query_terms, meta["title"]),
                        meta,
                        "filename_or_drive_keyword",
                    )
                # Indexed content candidates are validated before excerpts/names are returned.
                for row in self.index.sources(grant.grant_id):
                    check()
                    if not row["indexed_version"]:
                        continue
                    title_score = score(query_terms, row["meta"]["title"])
                    body_score = max(
                        (
                            score(query_terms, c["text"])
                            for c in self.index.chunks(row["ref"])
                        ),
                        default=0,
                    )
                    if title_score or body_score:
                        try:
                            fresh = self._fresh(
                                row["ref"], cancel=cancel, safety=safety
                            )[3]
                        except SourceError as exc:
                            self._failure(exc, row["ref"])
                            hits.pop(row["ref"], None)
                            errors.append(
                                {"grant_id": grant.grant_id, "status": exc.status}
                            )
                            continue
                        hits[row["ref"]] = (
                            10 * title_score + body_score,
                            fresh,
                            "indexed_keyword_topic",
                        )
            except SourceError as exc:
                if exc.status in ("denied", "unauthenticated", "not_found"):
                    self.index.purge(grant.grant_id)
                    hits = {r: v for r, v in hits.items() if self.index.get(r)}
                errors.append({"grant_id": grant.grant_id, "status": exc.status})
                if exc.status == "cancelled":
                    return self._failure(exc)
        if cancel is not None and cancel.is_set():
            return self._result("cancelled", "Search cancelled; no results published.")
        if safety is not None:
            safety()
        ordered = sorted(hits.items(), key=lambda x: (-x[1][0], x[1][1]["title"], x[0]))
        candidates = []
        for ref, (_, meta, reason) in ordered[:limit]:
            try:
                row, grant, _ = self._source(ref, cancel, safety)
            except SourceError:
                continue
            candidates.append(
                {
                    "ref": ref,
                    "title": meta["title"],
                    "mime": meta["mime"],
                    "source": grant.source,
                    "account": grant.account if grant.source == "drive" else "",
                    "path_or_url": meta["path_or_url"],
                    "modified_at": meta["modified_at"],
                    "version": meta["version"],
                    "match_reason": reason,
                }
            )
        status = "ok" if candidates else (errors[0]["status"] if errors else "no_match")
        return self._result(
            status,
            f"Found {len(candidates)} approved keyword/topic matches."
            if candidates
            else "No verified matches in the approved sources.",
            candidates=candidates,
            partial=partial or len(ordered) > limit or bool(errors),
            coverage="approved sources only; bounded, direct-child Drive folders",
            errors=errors,
        )

    def index_source(self, ref, *, cancel=None, safety=None):
        """Explicit selected-file indexing; never index a search-result collection."""
        try:
            row, grant, adapter = self._source(ref, cancel, safety)
            if not grant.content:
                return self._result(
                    "needs_content_approval",
                    "Persistent content indexing was not approved for this source.",
                )
            self._check(grant, cancel, safety)
            before = adapter.metadata(grant, row["key"])
            self._check(grant, cancel, safety)
            chunks, digest, kind = adapter.read(grant, before)
            self._check(grant, cancel, safety)
            after = adapter.metadata(grant, row["key"])
            if before["version"] != after["version"]:
                raise SourceError(
                    "stale",
                    "Source changed during the read; no mixed-version index published.",
                )
            if not chunks:
                raise SourceError("unsupported", "No readable evidence was extracted.")
            with self._lock:
                self._check(grant, cancel, safety)
                self.index.upsert(ref, grant.grant_id, row["key"], after)
                self.index.replace(
                    ref, after["version"], chunks, digest, kind, indexed_at=self.clock()
                )
            return self._result(
                "ok",
                "Selected approved source indexed.",
                ref=ref,
                chunk_count=len(chunks),
                version=after["version"],
            )
        except SourceError as exc:
            return self._failure(exc, ref)

    def answer(
        self, ref, question, locator="", *, cancel=None, safety=None, model_output=False
    ):
        """Extract matching source text; no cloud/backend/model calls or hidden inference."""
        try:
            row, grant, adapter = self._source(ref, cancel, safety)
            if model_output and not grant.model_disclosure:
                return self._result(
                    "needs_disclosure_approval",
                    "Source content disclosure to the configured model was not approved.",
                )
            row, grant, adapter, meta = self._fresh(ref, cancel=cancel, safety=safety)
            if not grant.content:
                return self._result(
                    "needs_content_approval",
                    "Approve content indexing before source-backed answers.",
                )
            if not row["indexed_version"]:
                outcome = self.index_source(ref, cancel=cancel, safety=safety)
                if outcome["status"] != "ok":
                    return outcome
                row, grant, adapter, meta = self._fresh(
                    ref, cancel=cancel, safety=safety
                )
            query_terms = terms(str(question or "")[:500])
            if not query_terms and not locator:
                return self._result(
                    "no_evidence", "Ask using specific content keywords."
                )
            selector = None
            selector_kind = "line_start"
            if locator:
                m = re.fullmatch(r"(line|page) ([1-9][0-9]*)", locator)
                if not m:
                    return self._result(
                        "unsupported",
                        "Use line N or page N; PDF pages require an approved injected parser.",
                    )
                selector_kind = "page" if m[1] == "page" else "line_start"
                selector = int(m[2])
            ranked = []
            for c in self.index.chunks(ref):
                if selector is not None and c["locator"].get(selector_kind) != selector:
                    continue
                n = score(query_terms, c["text"])
                if selector is not None:
                    n = max(1, n)
                if n:
                    ranked.append((n, c))
            ranked.sort(key=lambda pair: -pair[0])
            evidence = []
            for _, c in ranked[:6]:
                evidence.append(
                    {
                        "ref": ref,
                        "title": meta["title"],
                        "source": grant.source,
                        "account": grant.account if grant.source == "drive" else "",
                        "path_or_url": meta["path_or_url"],
                        "version": meta["version"],
                        "locator": c["locator"],
                        "quote": c["text"][:1000],
                        "offset": c["offset"],
                        "indexed_at": row["indexed_at"],
                        "verified_at": self.clock(),
                        "truncated": len(c["text"]) > 1000,
                    }
                )
            # Recheck access/version after evidence assembly, as well as authority at publication.
            final = adapter.metadata(grant, row["key"])
            if final["version"] != meta["version"]:
                raise SourceError(
                    "stale", "Source changed before the answer was published."
                )
            self._check(grant, cancel, safety)
            if not evidence:
                return self._result(
                    "no_evidence",
                    "No indexed passage supports those keywords.",
                    ref=ref,
                )
            return self._result(
                "ok",
                "Source excerpts: " + " / ".join(e["quote"] for e in evidence)[:600],
                answer="\n".join(e["quote"] for e in evidence),
                evidence=evidence,
                partial=len(ranked) > 6,
                ref=ref,
                untrusted_source=True,
            )
        except SourceError as exc:
            return self._failure(exc, ref)

    def duplicates(self, refs, *, cancel=None, safety=None):
        if len(refs) > 20:
            return self._result(
                "too_large", "Compare at most 20 selected approved sources."
            )
        rows = []
        errors = []
        for ref in dict.fromkeys(refs):
            try:
                row, grant, _, meta = self._fresh(ref, cancel=cancel, safety=safety)
                rows.append((ref, row, meta))
            except SourceError as exc:
                self._failure(exc, ref)
                errors.append({"ref": ref, "status": exc.status})
        groups = {}
        for ref, row, meta in rows:
            if row["digest"]:
                key = (row["digest_kind"], row["digest"])
            else:
                key = ("possible_duplicate", meta["title"].casefold(), meta["size"])
            groups.setdefault(key, []).append(ref)
        for ref, _, _ in rows:
            try:
                self._source(ref, cancel, safety)
            except SourceError as exc:
                return self._failure(exc, ref)
        if safety is not None:
            safety()
        if cancel is not None and cancel.is_set():
            return self._result("cancelled", "Duplicate comparison cancelled.")
        return self._result(
            "ok",
            "Duplicate candidates reported; originals untouched.",
            groups=[
                {"kind": key[0], "refs": items}
                for key, items in groups.items()
                if len(items) > 1
            ],
            errors=errors,
            partial=bool(errors),
        )
