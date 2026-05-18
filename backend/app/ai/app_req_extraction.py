"""Agentic extraction of application requirements using OpenAI function-calling + Playwright."""

import json
import logging

from playwright.async_api import Page

from app.ai.browser_tools import BROWSER_TOOLS, PlaywrightToolExecutor
from app.ai.client import get_openai_client

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = "gpt-4.1-mini"
MAX_TOOL_ROUNDS = 15
MAX_COMPLETION_TOKENS = 16384
MAX_RETRIES_ON_PARSE_FAILURE = 1


def _normalize_response_types(extraction: dict) -> dict:
    """Normalize legacy 'free_text' values to 'short_answer' for backward compat."""
    for field in extraction.get("application_fields", []):
        if field.get("response_type") == "free_text":
            field["response_type"] = "short_answer"
    return extraction


EXTRACTION_AGENT_SYSTEM = """\
You are an assistant that extracts application requirements from job posting pages.

You have browser tools to navigate and inspect web pages. Your goal is to find and fully \
explore the application form, collecting ALL fields across ALL pages/steps.

Follow this process:

1. Navigate to the provided application URL.
2. Use get_page_text to understand the page layout.
3. Find the application form. It may NOT be immediately visible — look for:
   - "Apply" or "Apply Now" buttons
   - Tabs labeled "Application", "Apply", or similar (common on Ashby, Greenhouse, Lever)
   - Links or buttons that lead to the application form
   Click whatever is needed to reach the actual form.
4. Once on the application form, use get_form_fields to extract structured field data.
5. IMPORTANT — Multi-step/multi-page forms: Many applications split fields across multiple \
   pages or steps. After extracting fields from the current view:
   - Look for "Next", "Continue", "Next Step", pagination indicators, or step numbers
   - Click through ALL steps/pages and run get_form_fields on EACH one
   - Combine fields from ALL steps into a single application_fields array
6. If get_form_fields returns nothing but the page has visible text about requirements, \
   use get_page_text and extract what you can from the text content.
7. When you have collected fields from ALL steps, return your findings.

When you have gathered enough information, respond with a JSON object (no markdown fences) \
with this exact structure:

{
  "cover_letter_status": "required" | "optional" | "not_needed",
  "needs_resume": true | false,
  "application_fields": [
    {
      "label": "Why do you want to work here?",
      "response_type": "short_answer",
      "field_type": "textarea",
      "required": true,
      "max_length": 500
    },
    {
      "label": "Email",
      "response_type": "personal_info",
      "field_type": "email",
      "required": true
    },
    {
      "label": "First Name",
      "response_type": "personal_info",
      "field_type": "text",
      "required": true
    },
    {
      "label": "LinkedIn URL",
      "response_type": "personal_info",
      "field_type": "url",
      "required": false
    },
    {
      "label": "Resume/CV",
      "response_type": "personal_info",
      "field_type": "file",
      "required": true
    },
    {
      "label": "Are you authorized to work in the US?",
      "response_type": "fixed_response",
      "field_type": "select",
      "required": true,
      "options": ["Yes", "No"]
    },
    {
      "label": "Do you require visa sponsorship?",
      "response_type": "fixed_response",
      "field_type": "select",
      "required": true,
      "options": ["Yes", "No"]
    }
  ],
  "notes": "Any additional observations about the application process"
}

CRITICAL — response_type classification:

Every field MUST be classified as exactly one of these three response_type values. \
Do NOT use "free_text" or any other value.

1. "short_answer" — Open-ended prompts requiring a thoughtful WRITTEN response. These are \
   essay questions where an LLM could draft a substantive multi-sentence answer. The answer \
   requires thought, not just recalling a fact.
   Examples: "Why do you want to work here?", "Tell us about a project you're proud of", \
   "Describe your experience with distributed systems", "What is AI missing today?", \
   "Additional information", "If you selected Other, please describe your dream role"

2. "personal_info" — Standard identity, contact, and profile fields. The applicant already \
   knows the answer — it's a fact about themselves, not something requiring composition. \
   This includes: name (first, last, full), email, phone, address/city/state/country, \
   LinkedIn URL, GitHub URL, website/portfolio URL, current employer, current title/role, \
   salary expectations, start date/availability, referral source, resume/CV uploads, \
   publications URL, links to previous work.
   KEY: if the field asks for a name, URL, date, employer, title, or other factual personal \
   data point — it is personal_info, NOT short_answer.

3. "fixed_response" — Fields with constrained choices. Dropdowns, selects, radio buttons, \
   checkboxes, yes/no questions, date pickers, number-only inputs. \
   This includes work authorization, visa sponsorship, relocation, and similar yes/no questions. \
   Examples: "Are you authorized to work in the US?" (yes/no), "Preferred start date" (date \
   picker), "Years of experience" (number/select)

How to decide: Ask "Does this field need the applicant to WRITE something thoughtful and \
multi-sentence?" If yes → short_answer. If the applicant just fills in a known fact about \
themselves → personal_info. If they pick from predefined options → fixed_response.

Other rules:
- cover_letter_status: "required" if explicitly asked for, "optional" if mentioned but not \
mandatory, "not_needed" if no mention.
- Extract EVERY form field. Do not skip fields. Include name, email, phone, resume, \
work authorization, and all other standard fields.
- For each field include:
  - label: The question or field label text
  - response_type: "short_answer", "personal_info", or "fixed_response" (NEVER "free_text")
  - field_type: The HTML input type if visible ("textarea", "select", "checkbox", "radio", \
    "text", "email", "tel", "number", "date", "file", "url", etc.)
  - required: Whether the field is marked as required
  - options: For fixed_response fields, list the available choices if visible
  - max_length: For short_answer fields, include character limit if shown
  - description: Any helper text or additional context shown near the field
- If the page requires login or is behind an auth wall, note that in "notes" and extract \
what you can from the visible page.
- If you can't find any application form, extract requirements from the job description text.
- Return ONLY the JSON object as your final response, no extra text."""


async def run_extraction_agent(
    page: Page,
    url: str,
    job_title: str,
    company: str,
) -> dict:
    """Run the extraction agent loop using OpenAI function calling."""
    client = get_openai_client()
    executor = PlaywrightToolExecutor(page)

    user_message = (
        f"Extract the application requirements for this job:\n"
        f"Title: {job_title}\n"
        f"Company: {company}\n"
        f"Application URL: {url}\n\n"
        f"Navigate to the URL and find out what materials are needed to apply."
    )

    messages = [
        {"role": "system", "content": EXTRACTION_AGENT_SYSTEM},
        {"role": "user", "content": user_message},
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        logger.info("Extraction agent round %d", round_num + 1)

        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=messages,
            tools=BROWSER_TOOLS,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )

        choice = response.choices[0]
        message = choice.message

        # If no tool calls, this is the final response
        if not message.tool_calls:
            final_text = (message.content or "").strip()
            logger.info("Agent final response (round %d): %s", round_num + 1, final_text[:500])

            # Check for truncation
            if choice.finish_reason == "length":
                logger.warning(
                    "Extraction response truncated (finish_reason=length), "
                    "asking model to continue"
                )
                messages.append(message)
                messages.append({
                    "role": "user",
                    "content": "Your response was truncated. Please output the "
                    "complete JSON object again from the beginning.",
                })
                continue

            # Strip markdown fences if present
            if final_text.startswith("```"):
                final_text = final_text.split("\n", 1)[1] if "\n" in final_text else final_text[3:]
                if final_text.endswith("```"):
                    final_text = final_text[:-3]
                final_text = final_text.strip()

            try:
                result = json.loads(final_text)
            except json.JSONDecodeError:
                logger.warning(
                    "Extraction returned invalid JSON, asking model to reformat"
                )
                messages.append(message)
                messages.append({
                    "role": "user",
                    "content": "Your response was not valid JSON. Please output "
                    "the complete JSON object again, with no extra text.",
                })
                continue

            fields = result.get("application_fields", [])
            if len(fields) < 3:
                logger.warning(
                    "Extraction returned only %d fields — may be incomplete",
                    len(fields),
                )

            return _normalize_response_types(result)

        # Append the assistant message (with tool_calls) to history
        messages.append(message)

        # Execute each tool call and append results
        for tool_call in message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
            logger.info("Executing tool: %s(%s)", fn_name, fn_args)

            result_text = await executor.execute(fn_name, fn_args)
            # Log tool results (truncate long outputs)
            log_result = result_text[:500] if len(result_text) > 500 else result_text
            logger.info("Tool %s result: %s", fn_name, log_result)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_text,
            })

    raise RuntimeError(f"Extraction agent did not produce a result after {MAX_TOOL_ROUNDS} rounds")
