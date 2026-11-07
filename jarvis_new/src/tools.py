import asyncio
import html as _html
import os
import re
from urllib.parse import urlencode

import httpx
from jarvis_rt import RunContext, function_tool
from jarvis_rt import ToolError

import phone_session
from browser import BrowserError, BrowserManager

BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DDG_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
YOU_SEARCH_ENDPOINT = "https://ydc-index.io/v1/search"


def _you_api_key(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    return (
        os.environ.get("YDC_API_KEY", "").strip()
        or os.environ.get("YOU_API_KEY", "").strip()
    )


def _clean_text(raw: str) -> str:
    """Strip tags/entities/cruft from a search snippet. Pure."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _format_results(query: str, items: list[tuple[str, str, str]]) -> dict[str, str]:
    """Shape (title, snippet, url) hits into a tool result. Pure."""
    lines = [
        f"{i}. {title} — {snippet} ({url})"
        for i, (title, snippet, url) in enumerate(items[:5], 1)
    ]
    text = f"Top web results for {query}:\n" + "\n".join(lines)
    return {
        "say": text,
        "text": text,
        "url": items[0][2] if items else "",
    }


async def brave_api_search(
    query: str, *, api_key: str | None = None
) -> dict[str, str] | None:
    """Brave Search API tier: JSON, no browser, no bot walls.

    Needs BRAVE_API_KEY (free 2k queries/mo at search.brave.com).
    Returns None on any failure so callers fall through.
    """
    key = (api_key or os.environ.get("BRAVE_API_KEY", "")).strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                BRAVE_SEARCH_ENDPOINT,
                params={"q": query.strip(), "count": 5},
                headers={
                    "X-Subscription-Token": key,
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            results = (resp.json().get("web") or {}).get("results") or []
        items = [
            (
                _clean_text(str(r.get("title", ""))),
                _clean_text(str(r.get("description", "") or r.get("snippet", ""))),
                str(r.get("url", "")),
            )
            for r in results[:5]
            if r.get("url")
        ]
        if not items:
            return None
        out = _format_results(query.strip(), items)
        out["fallback"] = "brave-api"
        return out
    except Exception:
        return None


async def ddg_lite_search(query: str) -> dict[str, str] | None:
    """Keyless tier: DuckDuckGo's lite endpoint via plain HTTP POST.

    No JS, no automation fingerprints — the bot walls that plague the
    full DDG/Google pages don't apply here. Returns None on failure.
    """
    query = query.strip()
    if not query:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                DDG_LITE_ENDPOINT,
                data={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            resp.raise_for_status()
            page = resp.text
        links = re.findall(
            r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>', page, re.S
        )
        snippets = re.findall(r"result-snippet\">(.*?)</td>", page, re.S)
        items = [
            (
                _clean_text(title),
                _clean_text(snippets[i] if i < len(snippets) else ""),
                url,
            )
            for i, (url, title) in enumerate(links[:5])
            if url.startswith("http")
        ]
        if not items:
            return None
        out = _format_results(query, items)
        out["fallback"] = "ddg-lite"
        return out
    except Exception:
        return None


async def you_search(
    query: str, *, api_key: str | None = None
) -> dict[str, str] | None:
    """You.com Search API tier: LLM-ready results, no browser, no walls.

    Needs YDC_API_KEY (free $5/mo credits at you.com/platform/api-keys).
    Returns None on any failure so callers fall through.
    """
    key = _you_api_key(api_key)
    query = query.strip()
    if not key or not query:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                YOU_SEARCH_ENDPOINT,
                json={"query": query, "count": 5},
                headers={
                    "X-API-Key": key,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        container = data.get("results", data)
        results = container.get("web", []) if isinstance(container, dict) else []
        if isinstance(results, dict):
            results = [results]
        items = []
        for r in results[:5]:
            if not isinstance(r, dict) or not r.get("url"):
                continue
            snippets = (
                r.get("snippets") or r.get("snippet") or r.get("description") or ""
            )
            if isinstance(snippets, list):
                snippets = " ".join(str(s) for s in snippets)
            items.append(
                (
                    _clean_text(str(r.get("title", ""))),
                    _clean_text(str(snippets)),
                    str(r.get("url", "")),
                )
            )
        if not items:
            return None
        out = _format_results(query, items)
        out["fallback"] = "you-search"
        return out
    except Exception:
        return None


async def api_search_parallel(query: str) -> dict[str, str] | None:
    """Three independent HTTP reads; first valid result wins.

    Prefer You, Brave, then DDG among results already available together.
    An unfinished preferred provider cannot hold a valid fallback hostage.
    Cancel and drain unused requests, including on caller cancellation.
    No browser or action runs here; all misses retain the browser fallback.
    """
    tasks = [
        asyncio.create_task(provider(query))
        for provider in (you_search, brave_api_search, ddg_lite_search)
    ]
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in tasks:  # stable preference among this ready batch
                if task not in done or task.cancelled():
                    continue
                try:
                    hit = task.result()
                except Exception:
                    continue
                if isinstance(hit, dict) and hit.get("say") and hit.get("url"):
                    return hit
        return None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


_API_PROVIDERS = ("you_search", "brave_api_search", "ddg_lite_search")


def duckduckgo_search_url(query: str) -> str:
    query = query.strip()
    if not query:
        raise ValueError("The search query cannot be empty.")
    return f"https://duckduckgo.com/?{urlencode({'q': query})}"


def google_search_url(query: str) -> str:
    """Google search URL for the in-context helper tab."""
    query = query.strip()
    if not query:
        raise ValueError("The search query cannot be empty.")
    return f"https://www.google.com/search?{urlencode({'q': query})}"


# Sites Jarvis knows by heart so it never has to ask the user "which URL?".
# Keys are normalized: lowercase, spaces/hyphens stripped.
KNOWN_SITES: dict[str, str] = {
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "youtubeshorts": "https://www.youtube.com/shorts",
    "amazon": "https://www.amazon.com",
    "reddit": "https://www.reddit.com",
    "wikipedia": "https://www.wikipedia.org",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "tiktok": "https://www.tiktok.com",
    "linkedin": "https://www.linkedin.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "chatgpt": "https://chat.openai.com",
    "netflix": "https://www.netflix.com",
    "ebay": "https://www.ebay.com",
    "walmart": "https://www.walmart.com",
    "target": "https://www.target.com",
    "bestbuy": "https://www.bestbuy.com",
    "etsy": "https://www.etsy.com",
    "weather": "https://weather.com",
    "bbc": "https://www.bbc.com",
    "bbcnews": "https://www.bbc.com/news",
    "cnn": "https://www.cnn.com",
    "nytimes": "https://www.nytimes.com",
    "imdb": "https://www.imdb.com",
    "twitch": "https://www.twitch.tv",
    "discord": "https://discord.com",
    "outlook": "https://outlook.live.com",
    "hotmail": "https://outlook.live.com",
    "office": "https://www.office.com",
    "googlemaps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "googledrive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    # Homepages, never a guessed file: "open google docs" used to land on a
    # spreadsheet the model half-remembered.
    "docs": "https://docs.google.com/document/",
    "googledocs": "https://docs.google.com/document/",
    "sheets": "https://docs.google.com/spreadsheets/",
    "googlesheets": "https://docs.google.com/spreadsheets/",
    "slides": "https://docs.google.com/presentation/",
    "googleslides": "https://docs.google.com/presentation/",
    "googledrive": "https://drive.google.com/drive/my-drive",
}


def resolve_url(destination: str) -> str:
    """Resolve a site name, bare domain, or full URL to a complete URL.

    Accepts things like "youtube", "YouTube", "youtube.com", "open gmail",
    or a full "https://..." URL. Never raises for a known site; falls back
    to treating bare domains as https URLs.
    """
    text = destination.strip().lower()
    # Strip common voice-command filler.
    for prefix in ("open ", "go to ", "goto ", "navigate to ", "visit "):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    text = text.rstrip("/")
    normalized = text.replace(" ", "").replace("-", "")
    if normalized in KNOWN_SITES:
        return KNOWN_SITES[normalized]
    # Bare domain like "youtube.com" or "bbc.co.uk" -> assume https.
    if "://" not in text and "." in text and " " not in text:
        return f"https://{text}"
    return destination.strip()


class BrowserTools:
    def __init__(
        self,
        browser: BrowserManager,
        *,
        research_browser: BrowserManager | None = None,
    ) -> None:
        self.browser = browser
        # Lazily-created isolated browser for deep research. Shared here so
        # the research agent and the router see the same instance.
        self._research_browser = research_browser
        self._research_active = False
        self._confirmed_target: str | None = None

    @property
    def research_browser(self) -> BrowserManager | None:
        return self._research_browser

    def _get_research_browser(self) -> BrowserManager:
        if self._research_browser is None:
            self._research_browser = BrowserManager.create_research_manager()
        return self._research_browser

    @property
    def tools(self) -> list:
        return [
            self.shopping_search,
            self.open_link,
            self.open_visible_url,
            self.open_url,
            self.search_the_web,
            self.open_google_app,
            self.dismiss_popups,
            self.read_page,
            self.inspect_page,
            self.go_back,
            self.take_screenshot,
            self.click,
            self.confirm_browser_action,
            self.type_text,
            self.scroll,
            self.auto_scroll,
            self.press_key,
            self.open_helper_google,
            self.read_helper,
            self.close_helper,
            self.list_tabs,
            self.switch_tab,
            self.start_deep_research,
            self.read_research_page,
            self.close_deep_research,
        ]

    @function_tool(name="open_url")
    @phone_session.approval_action
    async def open_link(self, context: RunContext, url: str) -> dict:
        """Request a visible new tab in the owner's existing desktop Brave.

        Use for an explicit open/show/play URL or YouTube video request.
        For a media title/search without a URL, use play_media instead.
        Never navigate Playwright here. For deliberate backend reading or
        research use browse_url. launch_requested is only a launch request;
        visually_confirmed=False never proves a page loaded or video played.
        """
        return await self.open_visible_url(context, url)

    @function_tool()
    @phone_session.approval_action
    async def open_visible_url(self, context: RunContext, url: str) -> dict:
        """Request a visible new tab in the owner's existing desktop Brave.

        Compatibility alias of open_url for explicit open/show/play URL requests,
        including a YouTube video or shopping source on the laptop.
        No Playwright, alternate profile, purchase, or submission. Report the
        returned launch status exactly; visually_confirmed=False means never
        say the page is visible or loaded. Requires an explicit open request.
        """
        import action_guard
        import visible_url

        return await visible_url.launch(
            url,
            ticket=phone_session.approval_ticket(),
            identity=action_guard.request_id(context),
        )

    @function_tool()
    async def shopping_search(self, context: RunContext, query: str) -> dict:
        """Shop via Playwright: read public product prices, currency, availability,
        source URL and check time. Report blocked/unverified states honestly.
        Never buy, bypass login/bot blocks, or automatically open a source in
        the system browser. Ask for explicit user approval before opening it.
        """
        from shopping import search_products_browser

        return await search_products_browser(query, self.browser)

    @function_tool()
    async def search_the_web(
        self,
        context: RunContext,
        query: str,
    ) -> dict[str, str]:
        """Search the web without touching a browser first.

        Use this only when the user needs a general internet search and did not name a
        website, service, or domain. If the user names a destination, open its official
        URL directly with browse_url instead. Read or inspect the resulting page before
        answering the user.

        Tiered so automated bot checks can't stop a turn: API providers
        raced in parallel (You.com when YDC_API_KEY is set, Brave Search
        API when BRAVE_API_KEY is set, plus keyless DuckDuckGo-lite),
        else the agent browser (DDG, then helper Google on a wall).

        Args:
            query: A concise search query containing all relevant context.
        """
        api_result = await api_search_parallel(query)
        if api_result is not None:
            return api_result
        try:
            result = await self.browser.open_url(duckduckgo_search_url(query))
        except (BrowserError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        if result.get("wall") in ("bot-check", "empty"):
            try:
                helper = await self.browser.open_helper(google_search_url(query))
                helper_text = await self.browser.read_page(
                    tab=BrowserManager.HELPER_TAB_ID
                )
            except (BrowserError, ValueError) as exc:
                raise ToolError(str(exc)) from exc
            return {
                "say": (
                    "DuckDuckGo showed a bot check, so I retried on Google: "
                    f"{helper_text.get('text', '')}"
                ),
                "fallback": "helper-google",
                "text": str(helper_text.get("text", "")),
                "url": str(helper.get("url", "")),
            }
        return result

    @function_tool(name="browse_url")
    async def open_url(
        self, context: RunContext, url: str, tab: str | None = None
    ) -> dict[str, str]:
        """Navigate the separate backend automation browser for reading or inspection.

        This does not open a link in the user's visible desktop Brave. Use
        open_url (or open_visible_url) for an explicit open/show/play link or video
        request on the laptop.

        Accepts a full http/https URL, a bare domain ("youtube.com"), or a
        well-known site name ("youtube", "gmail", "amazon"). Known names
        resolve automatically via KNOWN_SITES, so prefer calling this
        immediately with your best guess instead of asking the user for a URL.

        The result includes a "wall" flag: None when the page loaded with
        content, otherwise "consent" (call dismiss_popups), "empty" or
        "bot-check" (degraded automation shell: try ONE fallback, then
        report honestly instead of clicking a blank page), or "login".

        Args:
            url: A complete http/https URL, bare domain, or known site name.
            tab: Optional tab id ("main", "helper", "tab-N"). Defaults to
                the active tab.
        """
        import action_guard
        from system.launcher import is_blocked, blocked_say

        if blocked := is_blocked(url):
            raise ToolError(blocked_say(blocked))
        if prior := action_guard.launch_for_request(action_guard.request_id(context)):
            return action_guard.duplicate_reply(prior, "Done, Sir.")
        try:
            from urllib.parse import urlsplit

            from action_outcome import ActionOutcome

            expected = resolve_url(url)
            result = await self.browser.open_url(expected, tab=tab)
            # This observes only the exact backend page returned by navigation.
            observed = result.get("url", "")

            def key(value):
                parsed = urlsplit(value)
                return (
                    parsed.scheme.lower(),
                    (parsed.hostname or "").lower(),
                    parsed.port,
                    parsed.path or "/",
                    parsed.query,
                    parsed.fragment,
                )

            matched = bool(observed) and key(expected) == key(observed)
            receipt = ActionOutcome(
                "url.navigate",
                "verified" if matched else "dispatched_unverified",
                "started",
                "matched" if matched else "mismatched",
                "backend_browser",
                reason="" if matched else "redirected_or_mismatched",
                evidence={"url_matched": matched},
                request_id=action_guard.request_id(context),
            )
            result.update(receipt.result())
            result["browser"] = "backend_automation"
            result["visibility"] = "not_verified_on_desktop"
            return result
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    @phone_session.approval_action
    async def open_google_app(
        self,
        context: RunContext,
        app: str,
        which: str = "",
        account: str = "personal",
    ) -> dict:
        """Request a visible Google Docs, Sheets, Slides or Drive tab.

        Always use this for file names; never guess a file URL for browse_url.

        "open Google Docs" -> which="" (the homepage, the default).
        "open the second document" / "my latest sheet" -> which="2nd" /
        "latest" (his Nth most recently opened file of that type).
        "open Physics notes in Docs" -> which="Physics notes" (by name).
        "... on my school account" / "secondary school account" ->
        account="school"; otherwise "personal".
        If needs_choice is returned, ask the owner to choose and do not open
        anything yet. After the owner selects a candidate, use its returned
        URL with open_url; do not reinterpret a choice number as recent order.
        Report launch_requested as a request only, never visible/loaded without
        visual evidence. Backend reading remains on browse_url.

        Args:
            app: "docs", "sheets", "slides" or "drive".
            which: "" for the homepage, an ordinal like "2nd"/"latest", or a file name.
            account: "personal" (default) or "school".
        """
        import action_guard
        import google_apps
        import visible_url
        from system import require_local

        require_local()
        if phone_session.guest_required():
            raise ToolError("Guest mode refuses owner actions.")
        ticket = phone_session.approval_ticket()
        identity = action_guard.request_id(context)
        target = await asyncio.to_thread(google_apps.resolve, app, which, account)
        # Preserve the invocation epoch across lookup; never re-ticket a
        # request queued before a WhatsApp review/cancel or owner revocation.
        phone_session.check_approval_effect(ticket, current=True)
        if target.get("status") == "needs_choice":
            return {**target, "visually_confirmed": False}
        opened = await visible_url.launch(
            target["url"], ticket=ticket, identity=identity
        )
        result = {
            **opened,
            "title": target["title"],
            "resolution": target.get("status", "resolved"),
        }
        if result["resolution"].startswith("fallback_"):
            result["say"] = f"{target['say']} {opened['say']}"
        return result

    @function_tool()
    @phone_session.approval_action
    async def dismiss_popups(self, context: RunContext) -> dict[str, str | None]:
        """Dismiss cookie/consent/overlay dialogs blocking the page.

        Clicks the first safe dismiss button found (Reject all, Accept
        all, Got it, Dismiss, ...). Only allowlisted labels are ever
        tried: consequential controls are never touched. Call when
        browse_url reports wall "consent" or inspect shows a blocking
        dialog; afterwards continue the original task.
        """
        try:
            return await self.browser.dismiss_popups()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def read_page(
        self, context: RunContext, tab: str | None = None
    ) -> dict[str, str | bool]:
        """Read the visible text from a browser page.

        Args:
            tab: Optional tab id. Defaults to the active tab.
        """
        try:
            return await self.browser.read_page(tab=tab)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def inspect_page(
        self, context: RunContext, tab: str | None = None
    ) -> dict[str, object]:
        """Inspect a page, including readable text and interactive element names.

        Use this before clicking or typing so you can choose a visible control by its
        returned name or role.

        Args:
            tab: Optional tab id. Defaults to the active tab.
        """
        try:
            return await self.browser.inspect_page(tab=tab)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def go_back(
        self, context: RunContext, tab: str | None = None
    ) -> dict[str, str]:
        """Go back to the previous page in the agent-controlled browser.

        Args:
            tab: Optional tab id. Defaults to the active tab.
        """
        try:
            return await self.browser.go_back(tab=tab)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def take_screenshot(
        self, context: RunContext, tab: str | None = None
    ) -> dict[str, str | int | bool]:
        """Capture a browser page for diagnostics.

        Args:
            tab: Optional tab id. Defaults to the active tab.
        """
        try:
            return await self.browser.take_screenshot(tab=tab)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    @phone_session.approval_action
    async def click(
        self, context: RunContext, target: str, tab: str | None = None
    ) -> dict[str, str]:
        """Click a visible control by its accessible name.

        Args:
            target: The visible or accessible name of the control to click.
            tab: Optional tab id. Defaults to the active tab.
        """
        if os.environ.get("JARVIS_AUTO_APPROVE", "1") != "0":
            pass  # owner pre-approved consequential clicks
        elif self._requires_confirmation(target):
            if self._confirmed_target != target.casefold():
                raise ToolError(
                    f"This action may be consequential. Ask the user to confirm clicking {target!r} before retrying."
                )
            self._confirmed_target = None

        try:
            from action_outcome import ActionOutcome

            result = await self.browser.click(target, tab=tab)
            result.update(
                ActionOutcome(
                    "input.click",
                    "dispatched_unverified",
                    "started",
                    surface="backend_browser",
                ).result()
            )
            return result
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def confirm_browser_action(self, context: RunContext, target: str) -> str:
        """Authorize one previously discussed consequential browser click.

        Call this only after the user explicitly confirms the exact action.

        Args:
            target: The exact accessible name of the control the user approved.
        """
        self._confirmed_target = target.casefold()
        return f"The user confirmed clicking {target!r}."

    @function_tool()
    @phone_session.approval_action
    async def type_text(
        self,
        context: RunContext,
        target: str,
        text: str,
        tab: str | None = None,
    ) -> dict[str, str]:
        """Fill a visible text field by its label, placeholder, or accessible name.

        Args:
            target: The label, placeholder, or accessible name of the text field.
            text: The text to enter.
            tab: Optional tab id. Defaults to the active tab.
        """
        try:
            from action_outcome import ActionOutcome

            result = await self.browser.type_text(target, text, tab=tab)
            result.update(
                ActionOutcome(
                    "input.type",
                    "dispatched_unverified",
                    "started",
                    surface="backend_browser",
                ).result()
            )
            return result
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def scroll(
        self,
        context: RunContext,
        direction: str,
        tab: str | None = None,
        times: int = 1,
    ) -> dict[str, object]:
        """Scroll a browser page up or down, optionally several notches.

        One wheel notch barely moves feed-style pages (YouTube, Shorts,
        search results), so pass times 3-5 when the user says "scroll"
        or "keep scrolling". The result includes moved (bool) and scrollY:
        if moved is false, the page did not budge — inspect or try a
        fallback instead of scrolling blindly again.

        Args:
            direction: Either 'up' or 'down'.
            tab: Optional tab id. Defaults to the active tab.
            times: Wheel notches, 1-5 (clamped). Default 1.
        """
        try:
            return await self.browser.scroll(direction, tab=tab, times=times)  # type: ignore[arg-type]
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def auto_scroll(
        self,
        context: RunContext,
        direction: str = "down",
        tab: str | None = None,
        rounds: int = 3,
    ) -> dict[str, object]:
        """Keep scrolling a page for several rounds in ONE call (any website).

        This is THE tool for "keep scrolling", "scroll more", "continue",
        "next", and feed-style pages (Shorts, feeds, articles, results).
        It scrolls repeatedly inside a single call and verifies movement,
        falling back to keyboard scrolling when the wheel has no effect.
        Call it again immediately when the user says continue; only stop
        when the user says stop.

        Args:
            direction: Either 'down' or 'up'. Defaults to 'down'.
            tab: Optional tab id. Defaults to the active tab.
            rounds: Scroll rounds, 1-5 (clamped). Default 3.
        """
        try:
            return await self.browser.auto_scroll(direction, tab=tab, rounds=rounds)  # type: ignore[arg-type]
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    @phone_session.approval_action
    async def press_key(
        self, context: RunContext, key: str, tab: str | None = None
    ) -> dict[str, str]:
        """Press a safe navigation key in a browser page.

        Args:
            key: One of Enter, Escape, Tab, an arrow key, or Backspace.
            tab: Optional tab id. Defaults to the active tab.
        """
        try:
            return await self.browser.press_key(key, tab=tab)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def open_helper_google(
        self, context: RunContext, query: str
    ) -> dict[str, str]:
        """Quick Google assist: open results in a cheap second tab.

        Uses the in-context helper tab (same browser, shared login) for
        fast fact-checks while the main page stays open. Read it with
        read_helper, close it with close_helper.

        Args:
            query: A concise Google search query.
        """
        try:
            return await self.browser.open_helper(google_search_url(query))
        except (BrowserError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def read_helper(self, context: RunContext) -> dict[str, str | bool]:
        """Read the visible text from the helper (Google assist) tab."""
        try:
            return await self.browser.read_page(tab=BrowserManager.HELPER_TAB_ID)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def close_helper(self, context: RunContext) -> dict[str, object]:
        """Close the helper tab and return to the main tab."""
        try:
            result = await self.browser.close_tab(BrowserManager.HELPER_TAB_ID)
            await self.browser.switch_tab("main")
            return result
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def list_tabs(self, context: RunContext) -> dict[str, object]:
        """List open browser tabs (ids + urls) and which is active."""
        try:
            return await self.browser.list_tabs()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def switch_tab(self, context: RunContext, tab: str) -> dict[str, str]:
        """Switch to another open tab (e.g. "main", "helper", "tab-2").

        Args:
            tab: The tab id from list_tabs.
        """
        try:
            return await self.browser.switch_tab(tab)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def start_deep_research(
        self, context: RunContext, url: str
    ) -> dict[str, str]:
        """Spin up an ISOLATED second browser for deep research.

        Use only when the task genuinely needs a separate browser: comparing
        several pages, gathering citations, or long multi-page reads. Shares
        nothing with the main browser (no cookies, no tabs). Close it with
        close_deep_research when done.

        Args:
            url: The first research URL (full URL, domain, or site name).
        """
        try:
            research = self._get_research_browser()
            self._research_active = True
            return await research.open_url(resolve_url(url))
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def read_research_page(self, context: RunContext) -> dict[str, str | bool]:
        """Read visible text from the isolated deep-research browser."""
        try:
            return await self._get_research_browser().read_page()
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def close_deep_research(self, context: RunContext) -> dict[str, bool]:
        """Shut down the isolated deep-research browser."""
        try:
            if self._research_browser is not None:
                await self._research_browser.close()
            self._research_active = False
            return {"closed": True}
        except Exception as exc:
            raise ToolError(f"I could not close the research browser: {exc}") from exc

    @staticmethod
    def _requires_confirmation(target: str) -> bool:
        risky_words = {
            "buy",
            "confirm",
            "delete",
            "purchase",
            "remove",
            "send",
            "submit",
        }
        return bool(risky_words.intersection(target.casefold().split()))
