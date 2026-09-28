"""Sarah's own web browser (Playwright Chromium, stored in the project).

For pages that need a real browser: JavaScript-heavy sites, logins she's
been given, forms, buttons, infinite scroll. It has its own profile in her
workspace (never Zero's browser profiles), runs hidden unless asked to show
itself, and describes pages as text plus numbered interactive elements so
she can say "click 7" or "type into 3".
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from .guard import WORKSPACE

logger = logging.getLogger("sarah.browser")

PROFILE = WORKSPACE / "browser_profile"
DOWNLOADS = WORKSPACE / "downloads"

# Tag visible interactive elements with data-sarah-ref and describe them.
_INDEX_JS = r"""
() => {
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link], [role=tab], [role=menuitem], [contenteditable=true], summary';
  const out = [];
  let n = 0;
  document.querySelectorAll('[data-sarah-ref]').forEach(e => e.removeAttribute('data-sarah-ref'));
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.bottom < 0 || r.top > innerHeight * 3) continue;
    n += 1;
    el.setAttribute('data-sarah-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const kind = el.getAttribute('role') || (tag === 'input' ? 'input:' + (el.type || 'text') : tag);
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    const href = tag === 'a' ? (el.getAttribute('href') || '').slice(0, 120) : '';
    out.push(`[${n}] ${kind}${label ? ' "' + label + '"' : ''}${href ? ' -> ' + href : ''}`);
    if (n >= 120) break;
  }
  return out;
}
"""


class SarahBrowser:
    def __init__(self) -> None:
        self._pw = None
        self._context = None
        self._page = None
        self._headless = True
        self._lock = asyncio.Lock()

    async def _ensure(self, visible: Optional[bool] = None):
        want_headless = self._headless if visible is None else not visible
        if self._context is not None and want_headless != self._headless:
            await self.close()
        if self._context is None:
            from playwright.async_api import async_playwright

            PROFILE.mkdir(parents=True, exist_ok=True)
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            self._headless = want_headless
            self._pw = await async_playwright().start()
            self._context = await self._pw.chromium.launch_persistent_context(
                str(PROFILE), headless=want_headless, accept_downloads=True,
                downloads_path=str(DOWNLOADS), viewport={"width": 1280, "height": 900},
            )
            self._context.on("page", self._on_new_page)
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self._page

    def _on_new_page(self, page) -> None:
        self._page = page  # popups / target=_blank become the current tab

    async def close(self) -> str:
        try:
            if self._context is not None:
                await self._context.close()
            if self._pw is not None:
                await self._pw.stop()
        finally:
            self._pw = self._context = self._page = None
        return "Browser closed."

    async def _summary(self, page, max_chars: int = 5000) -> Dict[str, Any]:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
        elements = await page.evaluate(_INDEX_JS)
        limit = max(500, min(20000, int(max_chars or 5000)))
        return {
            "url": page.url,
            "title": await page.title(),
            "text": text[:limit] + ("\n...[more text: read with a higher max_chars or scroll]" if len(text) > limit else ""),
            "elements": elements,
        }

    async def act(self, action: str, url: str = "", ref: Optional[int] = None, text: str = "",
                  submit: bool = False, key: str = "", direction: str = "down", question: str = "",
                  visible: Optional[bool] = None, max_chars: int = 5000) -> Any:
        async with self._lock:
            if action == "close":
                return await self.close()
            page = await self._ensure(visible)
            if action == "open":
                if not re.match(r"^https?://", url or ""):
                    url = "https://" + (url or "").lstrip("/")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(800)
            elif action == "read":
                pass
            elif action in ("click", "type", "select"):
                if ref is None and not text:
                    raise ValueError("give ref (the element number) or text to find")
                locator = page.locator(f'[data-sarah-ref="{int(ref)}"]') if ref is not None else page.get_by_text(text, exact=False).first
                if action == "click":
                    await locator.click(timeout=10000)
                    await page.wait_for_timeout(900)
                elif action == "type":
                    await locator.fill(text, timeout=10000)
                    if submit:
                        await locator.press("Enter")
                        await page.wait_for_timeout(1200)
                else:
                    await locator.select_option(label=text, timeout=10000)
                page = self._page or page
            elif action == "press":
                await page.keyboard.press(key or "Enter")
                await page.wait_for_timeout(700)
            elif action == "scroll":
                await page.mouse.wheel(0, 900 if direction != "up" else -900)
                await page.wait_for_timeout(600)
            elif action == "back":
                await page.go_back(timeout=20000)
            elif action == "forward":
                await page.go_forward(timeout=20000)
            elif action == "look":
                import base64
                from backend.perception import sight

                shot = await page.screenshot(type="jpeg", quality=60)
                blocked = sight.budget.check(urgent=True)
                if blocked:
                    return f"Can't look right now: {blocked}"
                ok = limited = False
                try:
                    seen = await sight.look(base64.b64encode(shot).decode(), None, question or "What is on this page?")
                    ok = True
                except sight.RateLimited:
                    limited = True
                    return "Vision is rate limited right now; use read instead."
                finally:
                    sight.budget.done(ok=ok, rate_limited=limited)
                return {"url": page.url, "seen": {k: v for k, v in seen.items() if not k.startswith("_")}}
            else:
                raise ValueError(f"unknown browser action {action!r}")
            return await self._summary(self._page or page, max_chars)


browser = SarahBrowser()
