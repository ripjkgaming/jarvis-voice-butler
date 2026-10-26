"""Allowlisted, bounded, public read-only F1 adapters; evidence never instructions.

Contracts: https://api.jolpi.ca/docs/ and https://openf1.org/docs/ (2026-10-10).
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request
from urllib.parse import urlencode, urlsplit

from .core import Event, digest, validate

MAX_BYTES = 512 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Feed redirect refused")


def public_json(url, *, opener=None):
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in {
        "api.jolpi.ca",
        "api.openf1.org",
    }:
        raise ValueError("Public adapter allowlist")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Jarvis-public-monitor/1"},
        method="GET",
    )
    open_fn = opener or urllib.request.build_opener(NoRedirect()).open
    with open_fn(request, timeout=8) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Feed size exceeded")
    return json.loads(raw)


def timestamp(value):
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Feed UTC timezone required")
    return result.timestamp()


class F1Adapters:
    def __init__(self, fetch=public_json):
        self.fetch = fetch

    def jolpica(self, monitor, cursor, now):
        f, event = monitor["filters"], monitor["event"]
        validate("jolpica", event, f)
        suffix = "/results" if event == "results" else ""
        data = self.fetch(
            f"https://api.jolpi.ca/ergast/f1/{f['season']}/{f['round']}{suffix}/?limit=100"
        )
        races = data["MRData"]["RaceTable"]["Races"]
        if not isinstance(races, list) or len(races) > 1:
            raise ValueError("Expected exact race")
        out = []
        for race in races:
            if str(race["season"]) != str(f["season"]) or str(race["round"]) != str(
                f["round"]
            ):
                raise ValueError("Feed filter mismatch")
            # Only public structured facts; no arbitrary descriptions/URLs.
            facts = {
                "season": f["season"],
                "round": f["round"],
                "race": str(race["raceName"])[:120],
                "date": str(race["date"])[:10],
                "time": str(race.get("time", ""))[:20],
            }
            if event == "results":
                results = race.get("Results", [])
                if not results:
                    continue
                if not isinstance(results, list) or len(results) > 100:
                    raise ValueError("Results exceeded bounds")
                facts["results"] = [
                    {
                        "position": str(r["position"])[:3],
                        "driver": str(r["Driver"]["driverId"])[:60],
                        "status": str(r["status"])[:80],
                    }
                    for r in results
                ]
            # Schedule/results are version updates, not timestamped live incidents.
            # Deterministic version ID prevents repeated alerts when fetch time changes.
            out.append(Event("jolpica-" + digest([event, facts]), now, facts))
        return out

    def openf1(self, monitor, cursor, now):
        f = monitor["filters"]
        validate("openf1", monitor["event"], f)
        if f["end"] > now - 1800:
            raise ValueError("Historical only: window must end >30 minutes ago")
        sessions = self.fetch(
            "https://api.openf1.org/v1/sessions?"
            + urlencode({"session_key": f["session_key"]})
        )
        if (
            not isinstance(sessions, list)
            or len(sessions) != 1
            or sessions[0]["session_key"] != f["session_key"]
        ):
            raise ValueError("Exact historical session unavailable")
        if timestamp(sessions[0]["date_end"]) > now - 1800:
            raise ValueError("Paid live window refused")

        # Always replay the whole bounded window: late/out-of-order records must not
        # be lost behind a high-water mark. Persistent event IDs handle duplicates.
        def iso(t):
            return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()

        query = urlencode(
            {
                "session_key": f["session_key"],
                "date>=": iso(f["start"]),
                "date<=": iso(f["end"]),
            }
        )
        records = self.fetch("https://api.openf1.org/v1/race_control?" + query)
        if not isinstance(records, list) or len(records) > 200:
            raise ValueError("Replay too large; choose a smaller time window")
        out = []
        for r in records:
            if r["session_key"] != f["session_key"]:
                raise ValueError("Feed filter mismatch")
            facts = {
                k: str(r[k])[:300]
                for k in ("category", "flag", "message", "driver_number", "lap_number")
                if r.get(k) is not None
            }
            facts["mode"] = "historical-replay"
            out.append(
                Event(
                    "openf1-" + digest([f["session_key"], r["date"], facts]),
                    timestamp(r["date"]),
                    facts,
                )
            )
        return out

    def mapping(self):
        return {"jolpica": self.jolpica, "openf1": self.openf1}
