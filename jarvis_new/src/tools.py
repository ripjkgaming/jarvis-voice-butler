from urllib.parse import urlencode

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from browser import BrowserError, BrowserManager


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
    "spotify": "https://open.spotify.com",
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
            self.open_url,
            self.search_the_web,
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

    @function_tool()
    async def search_the_web(
        self,
        context: RunContext,
        query: str,
    ) -> dict[str, str]:
        """Open fallback DuckDuckGo results in the agent-controlled browser.

        Use this only when the user needs a general internet search and did not name a
        website, service, or domain. If the user names a destination, open its official
        URL directly with open_url instead. Read or inspect the resulting page before
        answering the user.

        When DuckDuckGo serves a bot wall, this automatically retries the
        same query in the helper Google tab and returns those results
        (marked with fallback "helper-google") so the turn keeps flowing.

        Args:
            query: A concise DuckDuckGo search query containing all relevant context.
        """
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

    @function_tool()
    async def open_url(
        self, context: RunContext, url: str, tab: str | None = None
    ) -> dict[str, str]:
        """Open a public webpage directly in the agent-controlled browser.

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
        try:
            return await self.browser.open_url(resolve_url(url), tab=tab)
        except BrowserError as exc:
            raise ToolError(str(exc)) from exc

    @function_tool()
    async def dismiss_popups(self, context: RunContext) -> dict[str, str | None]:
        """Dismiss cookie/consent/overlay dialogs blocking the page.

        Clicks the first safe dismiss button found (Reject all, Accept
        all, Got it, Dismiss, ...). Only allowlisted labels are ever
        tried: consequential controls are never touched. Call when
        open_url reports wall "consent" or inspect shows a blocking
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
    async def click(
        self, context: RunContext, target: str, tab: str | None = None
    ) -> dict[str, str]:
        """Click a visible control by its accessible name.

        Args:
            target: The visible or accessible name of the control to click.
            tab: Optional tab id. Defaults to the active tab.
        """
        if self._requires_confirmation(target):
            if self._confirmed_target != target.casefold():
                raise ToolError(
                    f"This action may be consequential. Ask the user to confirm clicking {target!r} before retrying."
                )
            self._confirmed_target = None

        try:
            return await self.browser.click(target, tab=tab)
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
            return await self.browser.type_text(target, text, tab=tab)
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
