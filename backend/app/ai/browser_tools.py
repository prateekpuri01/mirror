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

        // Try to find a label
        let label = null;
        if (el.id) {
            const labelEl = document.querySelector(`label[for="${el.id}"]`);
            if (labelEl) label = labelEl.textContent.trim();
        }
        if (!label) {
            const parent = el.closest('label, .field, .form-group, .form-field');
            if (parent) {
                const labelEl = parent.querySelector('label, .label, legend');
                if (labelEl) label = labelEl.textContent.trim();
            }
        }
        if (!label && el.getAttribute('aria-label')) {
            label = el.getAttribute('aria-label');
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
        url = input["url"]
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else "unknown"
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
        # Try clicking by visible text first
        try:
            locator = self.page.get_by_text(target, exact=False)
            count = await locator.count()
            if count > 0:
                await locator.first.click(timeout=5000)
                await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                title = await self.page.title()
                return f"Clicked element with text '{target}'. Page title: {title}, URL: {self.page.url}"
        except Exception:
            pass

        # Fall back to CSS selector
        try:
            await self.page.click(target, timeout=5000)
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            title = await self.page.title()
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
