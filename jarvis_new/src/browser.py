from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from playwright.async_api import (
    Locator,
    Page,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)


class BrowserError(Exception):
    """A user-facing browser operation failure."""


JARVIS_PROFILE_DIRNAME = "brave-profile"
JARVIS_RESEARCH_PROFILE_DIRNAME = "brave-research-profile"


def _default_profile_dir() -> Path:
    """Dedicated Jarvis Brave profile (never the live user profile).

    Overridable via JARVIS_BROWSER_PROFILE. The live
    ~/.config/BraveSoftware profile is deliberately avoided: Brave locks
    it while running, so automation would fight the user's own window.
    """
    override = os.environ.get("JARVIS_BROWSER_PROFILE", "").strip()
    if override:
        return Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home).expanduser() if data_home else Path.home() / ".jarvis"
    return base / JARVIS_PROFILE_DIRNAME


def _discover_browser_binary(explicit: str | None = None) -> str | None:
    """Brave executable path, else Chrome/Chromium, else None (bundled)."""
    if explicit and explicit.strip():
        return explicit.strip()
    override = os.environ.get("JARVIS_BROWSER_BIN", "").strip()
    if override:
        return override
    for candidate in (
        "brave-browser",
        "brave",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def detect_page_wall(title: str, text: str) -> str | None:
    """Classify what is blocking a page, if anything. Pure, testable.

    Returns "bot-check" (degraded bot shell / verification demand),
    "login" (needs an account), "consent" (cookie/privacy dialog),
    "empty" (blank body: nothing to click, read, or scroll), or None.
    """
    combined = f"{title or ''} {text or ''}".casefold()
    body = (text or "").strip()
    if any(
        phrase in combined
        for phrase in (
            "confirm you're not a bot",
            "confirm you are not a bot",
            "unusual traffic",
            "verify you are human",
            "verify you're human",
            "verify that you are human",
            "captcha",
            "bot check",
            "automated requests",
            "blocked by network security",
            "been blocked",
        )
    ):
        return "bot-check"
    if "sign in to continue" in combined and len(body) < 300:
        return "login"
    if any(
        phrase in combined
        for phrase in (
            "before you continue",
            "accept all",
            "reject all",
            "we value your privacy",
            "we use cookies",
            "cookie preferences",
            "manage consent",
            "choose your privacy",
        )
    ):
        return "consent"
    if not body:
        return "empty"
    return None


def _focus_window_with_title(title: str, *, timeout_seconds: float = 2.0) -> bool:
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_windows_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    user32.EnumWindows.argtypes = [enum_windows_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def find_window() -> int | None:
        matches: list[int] = []

        @enum_windows_proc
        def collect_window(hwnd: int, _: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title in buffer.value:
                matches.append(hwnd)
                return False
            return True

        user32.EnumWindows(collect_window, 0)
        return matches[0] if matches else None

    deadline = time.monotonic() + timeout_seconds
    hwnd = find_window()
    while hwnd is None and time.monotonic() < deadline:
        time.sleep(0.05)
        hwnd = find_window()
    if hwnd is None:
        return False

    foreground = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    attached = bool(
        foreground_thread
        and foreground_thread != current_thread
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0040,  # NOSIZE | NOMOVE | SHOWWINDOW
        )
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, False)


async def _bring_page_window_to_front(page: Page) -> None:
    marker = f"Jarvis Browser {uuid.uuid4().hex}"
    original_title = ""
    title_changed = False
    try:
        original_title = await page.title()
        await page.evaluate("title => { document.title = title; }", marker)
        title_changed = True
        await page.bring_to_front()
        await asyncio.to_thread(_focus_window_with_title, marker)
    except Exception:
        # Foreground activation is best-effort and must not prevent browser use.
        return
    finally:
        if title_changed:
            with contextlib.suppress(Exception):
                await page.evaluate(
                    "title => { document.title = title; }",
                    original_title,
                )


async def _page_scroll_y(page: Page) -> float:
    """Current vertical scroll offset; 0.0 when unreadable. Never raises."""
    with contextlib.suppress(Exception):
        return float(await page.evaluate("window.scrollY || 0"))
    return 0.0


class BrowserManager:
    """Own one isolated browser for a LiveKit room, with multiple tabs.

    Runs the user's own Brave (persistent profile, stays signed in) so
    sites with a login just work. First run opens Brave for a one-time
    sign-in; every later run reuses the session.

    Tab model:
    - ``main`` is the primary user-visible tab (backwards compatible: all
      existing methods default to the active tab).
    - ``helper`` (or ``tab-N``) is a cheap second tab in the SAME browser
      context — shared cookies/login — for quick fact-checks / Google assist.
    - Deep research uses a SEPARATE BrowserManager instance (isolated
      context/process AND its own profile, no cookie leak); see
      ``create_research_manager``.
    """

    HELPER_TAB_ID = "helper"

    def __init__(
        self,
        *,
        headless: bool = False,
        timeout_ms: int = 15_000,
        profile_dir: str | Path | None = None,
        browser_bin: str | None = None,
    ) -> None:
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._profile_dir = (
            Path(profile_dir).expanduser()
            if profile_dir is not None
            else _default_profile_dir()
        )
        self._browser_bin = _discover_browser_binary(browser_bin)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page: Page | None = None
        self._tabs: dict[str, Page] = {}
        self._active_tab: str = "main"
        self._next_tab_id: int = 1
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._page is not None and self._is_alive():
            return

        # Drop any stale handles (e.g. after a crash or the window being
        # closed) before launching a fresh browser.
        await self._shutdown_unlocked()

        self._playwright = await async_playwright().start()
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict[str, object] = {
            "user_data_dir": str(self._profile_dir),
            "headless": self._headless,
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
            # Drop Playwright's "--enable-automation" flag: it forces
            # navigator.webdriver=true, which makes hardened sites (YouTube)
            # serve automation an empty shell even with a valid login.
            # This stops announcing automation; it does not bypass logins,
            # CAPTCHAs, or consent walls.
            "ignore_default_args": ["--enable-automation"],
        }
        if self._browser_bin:
            launch_kwargs["executable_path"] = self._browser_bin
        self._context = await self._playwright.chromium.launch_persistent_context(
            **launch_kwargs  # type: ignore[arg-type]
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        self._browser = self._context.browser
        # Persistent profiles restore last session's tabs. Reuse the first
        # as "main" (closing the last window first can make headed
        # Chromium refuse new targets) and drop any extras so the tab
        # model holds every run.
        existing = [p for p in list(self._context.pages) if not p.is_closed()]
        if existing:
            self._page = existing[0]
            for stale in existing[1:]:
                with contextlib.suppress(Exception):
                    await stale.close()
        else:
            self._page = await self._context.new_page()
        self._page.set_default_timeout(self._timeout_ms)
        self._tabs = {"main": self._page}
        self._active_tab = "main"
        self._next_tab_id = 1
        if not self._headless:
            await _bring_page_window_to_front(self._page)

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown_unlocked()

    async def _shutdown_unlocked(self) -> None:
        """Best-effort teardown that never raises; callers manage locking."""
        if self._context is not None:
            with contextlib.suppress(Exception):
                await self._context.close()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._tabs = {}
        self._active_tab = "main"

    async def _recover_unlocked(self) -> None:
        """Force a fresh browser on next use after a crash/close."""
        await self._shutdown_unlocked()

    def _is_alive(self) -> bool:
        try:
            if self._page is None:
                return False
            if self._page.is_closed():
                return False
            return self._browser is None or self._browser.is_connected()
        except Exception:
            return False

    @staticmethod
    def _is_close_error(exc: BaseException) -> bool:
        message = str(exc).casefold()
        return "has been closed" in message or "target closed" in message

    async def new_tab(self, url: str | None = None) -> dict[str, str]:
        """Open a new tab in the SAME browser context (shared cookies/login).

        ``url`` may be ``None`` (blank tab). Returns the tab id + active URL.
        Quick fact-checks / the Google helper use this; deep research gets a
        separate isolated BrowserManager instead.
        """
        if url is not None:
            self._validate_url(url)
        last_error: Exception | None = None
        for _ in range(2):
            await self._get_page()
            assert self._context is not None
            async with self._lock:
                try:
                    page = await self._context.new_page()
                    page.set_default_timeout(self._timeout_ms)
                    tab_id = f"tab-{self._next_tab_id}"
                    self._next_tab_id += 1
                    self._tabs[tab_id] = page
                    self._active_tab = tab_id
                    self._page = page
                    if url is not None:
                        await page.goto(url, wait_until="domcontentloaded")
                        return {
                            "tab": tab_id,
                            **await self._page_summary(page),
                        }
                    return {"tab": tab_id, "url": page.url, "title": ""}
                except PlaywrightTimeoutError as exc:
                    raise BrowserError("The page took too long to load.") from exc
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not open a new tab: {exc}") from exc
        raise BrowserError(f"I could not open a new tab: {last_error}") from last_error

    async def open_helper(self, url: str) -> dict[str, str]:
        """Open/reuse the cheap in-context helper tab (Google assist).

        Keeps a single ``helper`` tab: reuses it when alive, creates it when
        missing, and makes it active. Shares cookies with the main tab.
        """
        self._validate_url(url)
        last_error: Exception | None = None
        for _ in range(2):
            await self._get_page()
            assert self._context is not None
            async with self._lock:
                try:
                    helper = self._tabs.get(self.HELPER_TAB_ID)
                    if helper is None or helper.is_closed():
                        helper = await self._context.new_page()
                        helper.set_default_timeout(self._timeout_ms)
                        self._tabs[self.HELPER_TAB_ID] = helper
                    self._active_tab = self.HELPER_TAB_ID
                    self._page = helper
                    await helper.goto(url, wait_until="domcontentloaded")
                    return {
                        "tab": self.HELPER_TAB_ID,
                        **await self._page_summary(helper),
                    }
                except PlaywrightTimeoutError as exc:
                    raise BrowserError("The page took too long to load.") from exc
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(
                        f"I could not open the helper tab: {exc}"
                    ) from exc
        raise BrowserError(
            f"I could not open the helper tab: {last_error}"
        ) from last_error

    async def list_tabs(self) -> dict[str, object]:
        """Return tab ids + urls without switching. Dead tabs are pruned."""
        await self._get_page()
        async with self._lock:
            return {"active": self._active_tab, "tabs": self._tab_inventory()}

    async def switch_tab(self, tab: str) -> dict[str, str]:
        """Make ``tab`` the active tab (e.g. ``main``, ``helper``, ``tab-2``)."""
        await self._get_page()
        async with self._lock:
            page = self._tabs.get(tab)
            if page is None or page.is_closed():
                # Prune the dead entry and fall back to main when possible.
                self._tabs.pop(tab, None)
                raise BrowserError(f"There is no open tab named {tab!r}.")
            self._active_tab = tab
            self._page = page
            with contextlib.suppress(Exception):
                await page.bring_to_front()
            return {"tab": tab, **await self._page_summary(page)}

    async def close_tab(self, tab: str) -> dict[str, object]:
        """Close one tab; never closes the last remaining tab."""
        if tab == "main" and len(self._tabs) <= 1:
            # Fresh managers lazily recreate main; guard the explicit close.
            pass
        await self._get_page()
        async with self._lock:
            page = self._tabs.get(tab)
            if page is None:
                raise BrowserError(f"There is no open tab named {tab!r}.")
            if len(self._tabs) <= 1:
                raise BrowserError("I cannot close the only open tab.")
            with contextlib.suppress(Exception):
                await page.close()
            self._tabs.pop(tab, None)
            if self._active_tab == tab:
                self._active_tab = (
                    "main" if "main" in self._tabs else next(iter(self._tabs))
                )
                self._page = self._tabs[self._active_tab]
            return {"closed": tab, "active": self._active_tab}

    async def open_url(self, url: str, *, tab: str | None = None) -> dict[str, str]:
        self._validate_url(url)
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    result = await self._page_summary(page)
                    result["tab"] = self._active_tab
                    result["wall"] = detect_page_wall(
                        result["title"], await self._wall_snippet(page)
                    )
                    return result
                except PlaywrightTimeoutError as exc:
                    raise BrowserError("The page took too long to load.") from exc
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not open that page: {exc}") from exc
        raise BrowserError(f"I could not open that page: {last_error}") from last_error

    async def _wall_snippet(self, page: Page, max_chars: int = 1500) -> str:
        """Short body text for wall detection. Never raises, never blocks."""
        try:
            text = await page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return ""
        return re.sub(r"\s+", " ", text).strip()[:max_chars]

    # Buttons a browsing agent may click to clear a blocking overlay.
    # Deliberately excludes anything consequential (send/submit/buy/delete).
    DISMISS_LABELS: tuple[str, ...] = (
        "Reject all",
        "Accept all",
        "I agree",
        "Agree",
        "Got it",
        "Dismiss",
        "Not now",
        "No thanks",
        "Close",
        "OK",
        "Remind me later",
    )

    async def dismiss_popups(self, *, tab: str | None = None) -> dict[str, str | None]:
        """Click the first safe dismiss button found (consent/overlay).

        Tries each DISMISS_LABELS entry via the normal target resolver and
        clicks the first that resolves. Returns the clicked label, or None
        when nothing safe matched (in which case nothing was clicked).
        """
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    for label in self.DISMISS_LABELS:
                        try:
                            locator = await self._resolve_target(page, label)
                        except BrowserError:
                            continue
                        await locator.click()
                        with contextlib.suppress(PlaywrightTimeoutError):
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=5_000
                            )
                        return {
                            "dismissed": label,
                            "tab": self._active_tab,
                            "url": page.url,
                            "title": await page.title(),
                        }
                    return {
                        "dismissed": None,
                        "tab": self._active_tab,
                        "url": page.url,
                    }
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not dismiss pop-ups: {exc}") from exc
        raise BrowserError(f"I could not dismiss pop-ups: {last_error}") from last_error

    async def read_page(
        self, *, max_chars: int = 12_000, tab: str | None = None
    ) -> dict[str, str | bool]:
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    text = await page.locator("body").inner_text(
                        timeout=self._timeout_ms
                    )
                except PlaywrightTimeoutError as exc:
                    raise BrowserError(
                        "The page content was not available in time."
                    ) from exc
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not read the page: {exc}") from exc
                break
        else:
            raise BrowserError(
                f"I could not read the page: {last_error}"
            ) from last_error

        text = re.sub(r"\s+", " ", text).strip()
        truncated = len(text) > max_chars
        return {
            "tab": self._active_tab,
            "url": page.url,
            "title": await page.title(),
            "text": text[:max_chars],
            "truncated": truncated,
        }

    async def inspect_page(
        self, *, max_chars: int = 8_000, tab: str | None = None
    ) -> dict[str, object]:
        """Return readable text plus a compact inventory of interactive elements."""
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    text = await page.locator("body").inner_text(
                        timeout=self._timeout_ms
                    )
                    elements = await page.locator(
                        "button, a, input, textarea, select, [role]"
                    ).evaluate_all(
                        """elements => elements
                          .filter(element => {
                            const style = window.getComputedStyle(element);
                            return style.display !== 'none' && style.visibility !== 'hidden';
                          })
                          .slice(0, 80)
                          .map((element, index) => ({
                            index,
                            tag: element.tagName.toLowerCase(),
                            role: element.getAttribute('role') || '',
                            name: (element.getAttribute('aria-label') ||
                              element.getAttribute('title') ||
                              (element.labels && element.labels[0] && element.labels[0].innerText) ||
                              element.getAttribute('placeholder') ||
                              element.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
                            type: element.getAttribute('type') || '',
                          }))"""
                    )
                except PlaywrightTimeoutError as exc:
                    raise BrowserError(
                        "The page could not be inspected in time."
                    ) from exc
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not inspect the page: {exc}") from exc
                break
        else:
            raise BrowserError(
                f"I could not inspect the page: {last_error}"
            ) from last_error

        text = re.sub(r"\s+", " ", text).strip()
        return {
            "tab": self._active_tab,
            "url": page.url,
            "title": await page.title(),
            "text": text[:max_chars],
            "elements": elements,
        }

    async def go_back(self, *, tab: str | None = None) -> dict[str, str]:
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    await page.go_back(wait_until="domcontentloaded")
                    result = await self._page_summary(page)
                    result["tab"] = self._active_tab
                    return result
                except PlaywrightTimeoutError as exc:
                    raise BrowserError(
                        "The previous page took too long to load."
                    ) from exc
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not go back: {exc}") from exc
        raise BrowserError(f"I could not go back: {last_error}") from last_error

    async def take_screenshot(
        self, *, tab: str | None = None
    ) -> dict[str, str | int | bool]:
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    image = await page.screenshot(type="png")
                    return {
                        "captured": True,
                        "tab": self._active_tab,
                        "url": page.url,
                        "bytes": len(image),
                    }
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not capture the page: {exc}") from exc
        raise BrowserError(
            f"I could not capture the page: {last_error}"
        ) from last_error

    async def click(self, target: str, *, tab: str | None = None) -> dict[str, str]:
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    locator = await self._resolve_target(page, target)
                    await locator.click()
                except PlaywrightTimeoutError as exc:
                    raise BrowserError(
                        f"The control {target!r} did not become clickable in time."
                    ) from exc
                except BrowserError:
                    raise
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not click {target!r}: {exc}") from exc

                with contextlib.suppress(PlaywrightTimeoutError):
                    await page.wait_for_load_state("domcontentloaded", timeout=5_000)

                result = await self._page_summary(page)
                result["tab"] = self._active_tab
                return result
        raise BrowserError(
            f"I could not click {target!r}: {last_error}"
        ) from last_error

    async def type_text(
        self, target: str, text: str, *, tab: str | None = None
    ) -> dict[str, str]:
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    locator = await self._resolve_textbox(page, target)
                    await locator.fill(text)
                except BrowserError:
                    raise
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(
                        f"I could not type into {target!r}: {exc}"
                    ) from exc

                return {
                    "target": target,
                    "tab": self._active_tab,
                    "url": page.url,
                }
        raise BrowserError(
            f"I could not type into {target!r}: {last_error}"
        ) from last_error

    async def scroll(
        self,
        direction: Literal["up", "down"],
        *,
        tab: str | None = None,
        times: int = 1,
    ) -> dict[str, object]:
        if direction not in {"up", "down"}:
            raise BrowserError("Scroll direction must be 'up' or 'down'.")
        try:
            count = int(times)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(5, count))

        amount = -650 if direction == "up" else 650
        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    before = await _page_scroll_y(page)
                    for _ in range(count):
                        await page.mouse.wheel(0, amount)
                        await page.wait_for_timeout(250)
                    after = await _page_scroll_y(page)
                    return {
                        "direction": direction,
                        "times": count,
                        "moved": abs(after - before) > 2,
                        "scrollY": after,
                        "tab": self._active_tab,
                        "url": page.url,
                    }
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not scroll: {exc}") from exc
        raise BrowserError(f"I could not scroll: {last_error}") from last_error

    async def auto_scroll(
        self,
        direction: Literal["up", "down"] = "down",
        *,
        tab: str | None = None,
        rounds: int = 3,
    ) -> dict[str, object]:
        """Scroll a page repeatedly inside ONE call (any website).

        Each round sends several wheel notches and settles so lazy content
        loads. When the wheel does not move the page (keyboard-driven
        containers, focused elements, Shorts-style players), it falls back
        to End/Home keys. Movement is verified via scrollY; the call is
        bounded (max 5 rounds) so it always terminates.
        """
        if direction not in {"up", "down"}:
            raise BrowserError("Scroll direction must be 'up' or 'down'.")
        try:
            total = int(rounds)
        except (TypeError, ValueError):
            total = 3
        total = max(1, min(5, total))

        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    moved = False
                    amount = -650 if direction == "up" else 650
                    for _ in range(total):
                        before = await _page_scroll_y(page)
                        for _ in range(5):
                            await page.mouse.wheel(0, amount)
                            await page.wait_for_timeout(200)
                        await page.wait_for_timeout(400)
                        if abs(await _page_scroll_y(page) - before) > 2:
                            moved = True
                            continue
                        # Wheel had no effect: keyboard-driven page/container.
                        await page.keyboard.press(
                            "End" if direction == "down" else "Home"
                        )
                        await page.wait_for_timeout(400)
                        if abs(await _page_scroll_y(page) - before) > 2:
                            moved = True
                    return {
                        "direction": direction,
                        "rounds": total,
                        "moved": moved,
                        "tab": self._active_tab,
                        "url": page.url,
                    }
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not auto-scroll: {exc}") from exc
        raise BrowserError(f"I could not auto-scroll: {last_error}") from last_error

    async def press_key(self, key: str, *, tab: str | None = None) -> dict[str, str]:
        allowed_keys = {
            "Enter",
            "Escape",
            "Tab",
            "ArrowDown",
            "ArrowLeft",
            "ArrowRight",
            "ArrowUp",
            "Backspace",
        }
        if key not in allowed_keys:
            raise BrowserError("That keyboard key is not allowed.")

        last_error: Exception | None = None
        for _ in range(2):
            page = await self._get_page(tab)
            async with self._lock:
                try:
                    await page.keyboard.press(key)
                    return {
                        "key": key,
                        "tab": self._active_tab,
                        "url": page.url,
                    }
                except Exception as exc:
                    if self._is_close_error(exc):
                        await self._recover_unlocked()
                        last_error = exc
                        continue
                    raise BrowserError(f"I could not press {key!r}: {exc}") from exc
        raise BrowserError(f"I could not press {key!r}: {last_error}") from last_error

    async def _get_page(self, tab: str | None = None) -> Page:
        if self._page is None or not self._is_alive():
            await self.start()
        assert self._page is not None
        if tab is not None:
            page = self._tabs.get(tab)
            if page is None or page.is_closed():
                self._tabs.pop(tab, None)
                raise BrowserError(f"There is no open tab named {tab!r}.")
            self._active_tab = tab
            self._page = page
            return page
        # Active tab may have been closed out-of-band; fall back gracefully.
        active = self._tabs.get(self._active_tab)
        if active is not None and not active.is_closed():
            self._page = active
            return active
        main = self._tabs.get("main")
        if main is not None and not main.is_closed():
            self._active_tab = "main"
            self._page = main
            return main
        for tab_id, candidate in list(self._tabs.items()):
            if not candidate.is_closed():
                self._active_tab = tab_id
                self._page = candidate
                return candidate
        # Everything is dead: relaunch cleanly on next use.
        await self._recover_unlocked()
        await self.start()
        assert self._page is not None
        return self._page

    def _tab_inventory(self) -> list[dict[str, str]]:
        inventory: list[dict[str, str]] = []
        for tab_id, page in list(self._tabs.items()):
            try:
                if page.is_closed():
                    self._tabs.pop(tab_id, None)
                    continue
            except Exception:
                self._tabs.pop(tab_id, None)
                continue
            try:
                inventory.append({"tab": tab_id, "url": page.url})
            except Exception:
                inventory.append({"tab": tab_id, "url": ""})
        return inventory

    @staticmethod
    def create_research_manager(
        *,
        headless: bool = True,
        timeout_ms: int = 15_000,
        profile_dir: str | Path | None = None,
    ) -> BrowserManager:
        """Create an ISOLATED browser for deep research.

        Separate process/context AND its own persistent profile from the
        main manager: no shared cookies, no shared tabs,
        crash-independent. Used only when the agent decides
        a task needs a whole separate browser (multi-page comparison,
        citations, long reads). The caller owns its lifecycle (close it).
        """
        if profile_dir is None:
            override = os.environ.get("JARVIS_BROWSER_RESEARCH_PROFILE", "").strip()
            if override:
                profile_dir = override
            else:
                profile_dir = _default_profile_dir().parent / (
                    JARVIS_RESEARCH_PROFILE_DIRNAME
                )
        return BrowserManager(
            headless=headless, timeout_ms=timeout_ms, profile_dir=profile_dir
        )

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BrowserError("Only complete http or https URLs can be opened.")
        if parsed.username or parsed.password:
            raise BrowserError("URLs containing embedded credentials are not allowed.")

    @staticmethod
    async def _page_summary(page: Page) -> dict[str, str]:
        return {"url": page.url, "title": await page.title()}

    async def _resolve_target(self, page: Page, target: str) -> Locator:
        for role in ("button", "link", "tab", "menuitem", "checkbox", "radio"):
            locator = page.get_by_role(role, name=target, exact=True)
            if await locator.count():
                return locator.first

            locator = page.get_by_role(role, name=target, exact=False)
            if await locator.count():
                return locator.first

        locator = page.get_by_text(target, exact=True)
        if await locator.count():
            return locator.first

        locator = page.get_by_text(target, exact=False)
        if await locator.count():
            return locator.first

        raise BrowserError(f"I could not find a visible control named {target!r}.")

    async def _resolve_textbox(self, page: Page, target: str) -> Locator:
        # get_by_label matches ANY labelled element (e.g. DuckDuckGo's
        # aria-labelled "Search mode" toggle div), so label/role matches are
        # intersected with actual editable elements. input[type=search] has
        # the implicit role "searchbox", not "textbox".
        editable = page.locator("input, textarea, [contenteditable]")
        for locator in (
            page.get_by_role("textbox", name=target, exact=True),
            page.get_by_role("textbox", name=target, exact=False),
            page.get_by_role("searchbox", name=target, exact=True),
            page.get_by_role("searchbox", name=target, exact=False),
            page.get_by_label(target, exact=True).and_(editable),
            page.get_by_label(target, exact=False).and_(editable),
            page.get_by_placeholder(target, exact=True),
            page.get_by_placeholder(target, exact=False),
        ):
            if await locator.count():
                return locator.first

        normalized_target = target.casefold()
        if any(
            word in normalized_target for word in ("search", "query", "input", "text")
        ):
            for selector in (
                "input[type='search']:visible",
                "input[aria-label*='search' i]:visible",
                "input[placeholder*='search' i]:visible",
                "textarea:visible",
                "input:visible",
            ):
                locator = page.locator(selector)
                if await locator.count():
                    return locator.first

        raise BrowserError(f"I could not find a text field named {target!r}.")
