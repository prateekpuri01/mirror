"""Playwright-backed tools for the extraction agent (OpenAI function-calling format)."""

import json
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)

BROWSER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Navigate the browser to a URL. Returns the page title and HTTP status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to navigate to"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_text",
            "description": "Extract all visible text content from the current page. Truncated to ~8000 characters.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_form_fields",
            "description": "Extract all form elements (inputs, textareas, selects) with their labels, types, and attributes as structured data.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "Click a button or link by its visible text content or CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "The visible text of the element to click, or a CSS selector.",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_url",
            "description": "Return the current page URL (useful after redirects or navigation).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clickable_elements",
            "description": "List all visible buttons, links, and tab-like elements on the page. "
            "Useful for discovering navigation tabs (e.g. 'Application'), 'Apply' buttons, "
            "'Next'/'Continue' buttons in multi-step forms, or other interactive elements.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


# Tools specifically for the careers page crawler (superset of BROWSER_TOOLS)
CAREERS_CRAWLER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fill_and_search",
            "description": "Type a search query into an input field and press Enter. "
            "Use this when you see a search box on a careers page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the input field, e.g. 'input[type=\"search\"]', "
                        "'input[placeholder*=\"Search\"]', '#search-input', or 'input[name=\"q\"]'",
                    },
                    "query": {
                        "type": "string",
                        "description": "The search query to type, e.g. 'data scientist' or 'AI safety'",
                    },
                },
                "required": ["selector", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "Click a button or link by its visible text content or CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "The visible text of the element to click, or a CSS selector.",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_job_links",
            "description": "Extract all job posting links from the current page. "
            "Call this when you can see job listings on the page. "
            "Returns a list of {url, title} objects for each job posting found.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_job_card",
            "description": (
                "Click the first visible element that looks like a job listing "
                "CARD (li/article/div) to discover the URL it navigates to. "
                "Use this when extract_job_links returns no URLs but you can "
                "SEE job titles in the page text — this means jobs are rendered "
                "as JS-driven cards (no <a href>). Common on Google Careers, "
                "Apple Jobs, and other SPAs. After click, the URL changes to "
                "a specific job page; we capture it and navigate back so you "
                "can continue extracting. Returns {url, title} on success."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contains_text": {
                        "type": "string",
                        "description": (
                            "Optional substring the card should contain (e.g. "
                            "'Machine Learning') to prefer a card matching the "
                            "search. Empty string = click any first card."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Stop browsing — no matching jobs can be found on this page. "
            "Use this when the page has no search functionality, no job listings, "
            "or is behind a login wall.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief reason why no jobs were found",
                    },
                },
                "required": ["reason"],
            },
        },
    },
]


# JavaScript to extract form fields from a page
_FORM_FIELDS_JS = """
() => {
    const fields = [];
    const elements = document.querySelectorAll('input, textarea, select, [contenteditable="true"]');
    for (const el of elements) {
        if (el.type === 'hidden') continue;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;

        const field = {
            tag: el.tagName.toLowerCase(),
            type: el.type || null,
            name: el.name || null,
            id: el.id || null,
            placeholder: el.placeholder || null,
            required: el.required || false,
            maxLength: el.maxLength > 0 ? el.maxLength : null,
        };

        // Try to find a label — multiple strategies for different ATS platforms
        let label = null;
        if (el.id) {
            const labelEl = document.querySelector(`label[for="${el.id}"]`);
            if (labelEl) label = labelEl.textContent.trim();
        }
        if (!label) {
            const parent = el.closest('label, .field, .form-group, .form-field, [class*="field"], [class*="Field"]');
            if (parent) {
                const labelEl = parent.querySelector('label, .label, legend');
                if (labelEl) label = labelEl.textContent.trim();
            }
        }
        // Ashby/SPA pattern: label is in a sibling or parent div as plain text
        if (!label) {
            const wrapper = el.closest('[class*="field"], [class*="Field"], [role="group"]')
                         || el.parentElement?.parentElement;
            if (wrapper) {
                // Look for first text node or div that looks like a label
                for (const child of wrapper.children) {
                    if (child === el || child.contains(el)) continue;
                    const txt = child.textContent?.trim();
                    if (txt && txt.length < 200 && txt.length > 0) {
                        label = txt;
                        break;
                    }
                }
            }
        }
        if (!label && el.getAttribute('aria-label')) {
            label = el.getAttribute('aria-label');
        }
        if (!label && el.placeholder) {
            label = el.placeholder;
        }
        field.label = label;

        // Select options
        if (el.tagName === 'SELECT') {
            field.options = Array.from(el.options).map(o => o.text.trim()).filter(Boolean);
        }

        // Radio/checkbox group options
        if ((el.type === 'radio' || el.type === 'checkbox') && el.name) {
            const group = document.querySelectorAll(`input[name="${el.name}"]`);
            if (group.length > 1) {
                const opts = [];
                for (const opt of group) {
                    const optLabel = opt.labels?.[0]?.textContent?.trim()
                        || opt.parentElement?.textContent?.trim()
                        || opt.value;
                    if (optLabel) opts.push(optLabel);
                }
                field.options = opts;
                // Only include the group once
                if (el !== group[0]) continue;
            }
        }

        fields.push(field);
    }
    return fields;
}
"""


_CLICKABLE_ELEMENTS_JS = """
() => {
    const results = [];
    const seen = new Set();
    const els = document.querySelectorAll(
        'a, button, [role="button"], [role="tab"], [role="link"], ' +
        'input[type="submit"], input[type="button"], [tabindex="0"], ' +
        '[data-tab], .tab, .nav-link, .nav-item'
    );
    for (const el of els) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        const text = (el.textContent || '').trim().substring(0, 100);
        if (!text || seen.has(text)) continue;
        seen.add(text);
        results.push({
            tag: el.tagName.toLowerCase(),
            text: text,
            role: el.getAttribute('role') || null,
            href: el.getAttribute('href') || null,
            type: el.getAttribute('type') || null,
            ariaSelected: el.getAttribute('aria-selected') || null,
        });
        if (results.length >= 50) break;
    }
    return results;
}
"""


class PlaywrightToolExecutor:
    """Maps Claude tool_use calls to Playwright Page operations."""

    def __init__(self, page: Page):
        self.page = page

    async def execute(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool call and return a string result."""
        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                return f"Unknown tool: {tool_name}"
            return await handler(tool_input)
        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            return f"Error executing {tool_name}: {e}"

    async def _tool_navigate(self, input: dict) -> str:
        import asyncio

        url = input["url"]
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else "unknown"
        # Wait for SPA frameworks to hydrate — domcontentloaded fires before
        # React/Vue render actual content.  Try networkidle first, then fall
        # back to a fixed delay.
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        # Extra settle time for heavy SPAs (Ashby, Lever, etc.)
        await asyncio.sleep(2.0)
        title = await self.page.title()
        return f"Navigated to {url}\nStatus: {status}\nTitle: {title}"

    async def _tool_get_page_text(self, input: dict) -> str:
        text = await self.page.inner_text("body")
        # Truncate to ~8000 chars to stay within context limits
        if len(text) > 8000:
            text = text[:8000] + "\n... [truncated]"
        return text

    async def _tool_get_form_fields(self, input: dict) -> str:
        fields = await self.page.evaluate(_FORM_FIELDS_JS)
        if not fields:
            return "No form fields found on this page."
        return json.dumps(fields, indent=2)

    async def _tool_click_element(self, input: dict) -> str:
        target = input["target"]

        async def _post_click_wait(self) -> str:
            """Wait for content to settle after a click.

            SPA tab switches (Ashby, etc.) don't trigger a page navigation,
            so ``wait_for_load_state("domcontentloaded")`` returns instantly.
            We first try ``networkidle`` (catches API-driven re-renders) with a
            short timeout, then always add a small sleep so React/Vue/etc. can
            flush their render queue.
            """
            import asyncio

            try:
                await self.page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            # Extra settle time for SPA frameworks to render new DOM nodes
            await asyncio.sleep(2.0)
            return await self.page.title()

        # Try clicking by visible text first
        try:
            locator = self.page.get_by_text(target, exact=False)
            count = await locator.count()
            if count > 0:
                await locator.first.click(timeout=5000)
                title = await _post_click_wait(self)
                return f"Clicked element with text '{target}'. Page title: {title}, URL: {self.page.url}"
        except Exception:
            pass

        # Fall back to CSS selector
        try:
            await self.page.click(target, timeout=5000)
            title = await _post_click_wait(self)
            return f"Clicked element '{target}'. Page title: {title}, URL: {self.page.url}"
        except Exception as e:
            return f"Could not click '{target}': {e}"

    async def _tool_get_page_url(self, input: dict) -> str:
        return self.page.url

    async def _tool_get_clickable_elements(self, input: dict) -> str:
        elements = await self.page.evaluate(_CLICKABLE_ELEMENTS_JS)
        if not elements:
            return "No clickable elements found on this page."
        return json.dumps(elements, indent=2)

    async def _tool_fill_and_search(self, input: dict) -> str:
        """Fill a search input and press Enter, then wait for results.

        SPA careers pages often lazy-render inputs — bumped timeouts so we
        wait for the element to become interactive instead of giving up
        after a few seconds.
        """
        import asyncio

        selector = input["selector"]
        query = input["query"]
        try:
            await self.page.fill(selector, query, timeout=8000)
            await self.page.keyboard.press("Enter")
        except Exception:
            # Fallback: try clicking the element first, then typing
            try:
                await self.page.click(selector, timeout=8000)
                await self.page.keyboard.type(query, delay=50)
                await self.page.keyboard.press("Enter")
            except Exception as e:
                return f"Could not fill search field '{selector}': {e}"
        # Wait for results to load
        try:
            await self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await asyncio.sleep(3.0)
        title = await self.page.title()
        return f"Typed '{query}' into '{selector}' and pressed Enter. Page title: {title}"

    async def _tool_extract_job_links(self, input: dict) -> str:
        """Extract candidate job-posting URLs from the current page.

        Previously this only scanned ``<a href>`` tags, which missed many
        enterprise SPAs (IBM, Google Careers, Cox, BlackLine) that render
        job cards as ``<div onclick>``, ``[role="link"]``, or use
        ``data-href`` attributes with JS navigation.

        Now we:
          1. Scroll the page to trigger lazy-loading of job cards.
          2. Wait briefly for networkidle so JS can render/fetch results.
          3. Pull URLs from <a href>, [data-href]/[data-url]/[data-job-url],
             [role="link"][href], and [onclick] handlers that contain a URL.

        Returns a JSON array of {url, title} objects (deduplicated by URL).
        """
        import asyncio

        # 1. Scroll to trigger lazy-loads (many SPAs render cards only when
        #    scrolled into view). We go down, then back to top, so the LLM
        #    picker sees job titles from the full list.
        try:
            for _ in range(3):
                await self.page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                await asyncio.sleep(0.3)
            await self.page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.3)
        except Exception:
            pass

        # 2. Let any fetch-triggered content settle
        try:
            await self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

        # 3. Extract from multiple clickable-element types
        links = await self.page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            const add = (rawHref, titleSrc) => {
                if (!rawHref) return;
                let abs;
                try {
                    abs = new URL(rawHref, window.location.href).href;
                } catch (_) { return; }
                if (!abs.startsWith('http')) return;
                if (seen.has(abs)) return;
                seen.add(abs);
                const title = (titleSrc || '').replace(/\\s+/g, ' ').trim().substring(0, 200);
                if (!title) return;
                results.push({url: abs, title: title});
            };

            // a) Standard anchors
            for (const a of document.querySelectorAll('a[href]')) {
                add(a.getAttribute('href'), a.innerText || a.textContent);
            }

            // b) Data-attribute URLs (common on card-based listings)
            const dataAttrs = ['data-href', 'data-url', 'data-job-url', 'data-target-url', 'data-link'];
            for (const attr of dataAttrs) {
                for (const el of document.querySelectorAll('[' + attr + ']')) {
                    add(el.getAttribute(attr), el.innerText || el.textContent);
                }
            }

            // c) role="link" with an href attribute
            for (const el of document.querySelectorAll('[role="link"][href]')) {
                add(el.getAttribute('href'), el.innerText || el.textContent);
            }

            // d) onclick handlers with a URL inside
            //    Patterns: window.location='...', location.href='...', window.open('...'), navigate('...')
            const onclickRe = /(?:location(?:\\.href)?\\s*=\\s*|window\\.open\\s*\\(\\s*|navigate\\s*\\(\\s*|router\\.push\\s*\\(\\s*)['\"]([^'\"]+)['\"]/;
            for (const el of document.querySelectorAll('[onclick]')) {
                const oc = el.getAttribute('onclick') || '';
                const m = oc.match(onclickRe);
                if (m && m[1]) add(m[1], el.innerText || el.textContent);
            }

            return results;
        }""")
        if not links:
            return "No links found on this page."
        # Return all links — the caller will filter by URL pattern and relevance
        return json.dumps(links[:150], indent=2)

    async def _tool_click_job_card(self, input: dict) -> str:
        """Click a card-like element to discover the URL it navigates to.

        Many SPAs (Google Careers, Apple Jobs) render job listings as
        clickable <li> or <div> elements with JavaScript click handlers
        instead of plain <a href> anchors. extract_job_links can't see
        these. This tool:
          1. Finds visible card-like containers across multiple selectors
          2. Optionally filters by substring match against card text
          3. Clicks the first match (polls page.url for pushState changes)
          4. If URL changed, captures it and goes back so the listing
             page is ready for further extraction

        Returns a JSON {url, title} on success, or an error string.
        """
        import asyncio

        contains_text = (input.get("contains_text") or "").strip().lower()

        initial_url = self.page.url

        # Broad selector list. We filter visibility + text in Python.
        # Order matters: more-specific first.
        selectors = [
            '[role="article"]',
            '[role="listitem"]',
            'li[class*="job" i]',
            'li[class*="card" i]',
            "article",
            "li",
            "tr",
            'div[class*="job-card" i]',
            'div[class*="JobCard"]',
            'div[class*="listing" i]',
            'div[class*="posting" i]',
        ]

        clicked_count = 0
        cards_tried: list[str] = []

        for selector in selectors:
            if clicked_count >= 3:
                break
            try:
                elements = await self.page.query_selector_all(selector)
            except Exception:
                continue

            # Score candidates by text match
            candidates: list = []
            for el in elements:
                try:
                    if not await el.is_visible():
                        continue
                    text = (await el.inner_text()).strip()
                    if not text or len(text) < 15 or len(text) > 1500:
                        continue
                    if contains_text and contains_text not in text.lower():
                        continue
                    candidates.append((el, text))
                except Exception:
                    continue
                if len(candidates) >= 20:
                    break

            for el, text in candidates[:5]:
                if clicked_count >= 3:
                    break
                cards_tried.append(text[:60])
                clicked_count += 1
                try:
                    await el.scroll_into_view_if_needed(timeout=3000)
                    await el.click(timeout=5000)
                except Exception:
                    # Maybe the element itself isn't clickable —
                    # try clicking a child anchor/button
                    try:
                        child = await el.query_selector("a, button")
                        if child:
                            await child.click(timeout=5000)
                        else:
                            continue
                    except Exception:
                        continue

                # Poll for URL change (catches pushState / SPA routing)
                new_url = initial_url
                for _ in range(24):  # up to 6s @ 250ms
                    await asyncio.sleep(0.25)
                    if self.page.url and self.page.url != initial_url:
                        new_url = self.page.url
                        break

                if new_url != initial_url:
                    try:
                        await self.page.go_back(timeout=6000)
                        await self.page.wait_for_timeout(1000)
                    except Exception:
                        pass
                    return json.dumps(
                        {
                            "url": new_url,
                            "title": text[:200],
                            "discovered_via": "click_through",
                        }
                    )

                # URL didn't change — maybe a modal/side-panel appeared.
                # Look for the first visible apply/details link.
                try:
                    apply_link = await self.page.evaluate("""() => {
                        const sels = 'a[href*="/job"], a[href*="/career"], a[href*="apply"], a[href*="requisition"]';
                        for (const a of document.querySelectorAll(sels)) {
                            const rect = a.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && a.href.startsWith('http')) {
                                return {url: a.href, text: (a.innerText || '').trim().substring(0, 100)};
                            }
                        }
                        return null;
                    }""")
                except Exception:
                    apply_link = None
                if apply_link and apply_link.get("url") and apply_link["url"] != initial_url:
                    return json.dumps(
                        {
                            "url": apply_link["url"],
                            "title": text[:200],
                            "discovered_via": "click_through_modal",
                        }
                    )

        if clicked_count == 0:
            return "No clickable cards found matching criteria. Page may be empty or still loading."
        return (
            f"Clicked {clicked_count} card(s) but URL never changed and no "
            f"apply link appeared. Tried: {cards_tried[:3]}"
        )

    async def _tool_done(self, input: dict) -> str:
        reason = input.get("reason", "No reason given")
        return f"DONE: {reason}"
