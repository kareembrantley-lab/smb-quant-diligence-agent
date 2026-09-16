"""
Thin wrapper around the Claude API.

Every LLM-assisted step in this agent (chart-of-accounts mapping for labels
the fuzzy matcher can't place, and plain-English anomaly narratives) is
designed to degrade gracefully with no API key: the pipeline still runs
end-to-end, it just leans harder on the rules layer and says so in the memo.
That matters for a portfolio repo -- a reviewer cloning this should be able
to run it in five minutes without first getting a key from Kareem.

Set ANTHROPIC_API_KEY in the environment to turn the LLM assist on.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

DEFAULT_MODEL = os.environ.get("SMB_DILIGENCE_MODEL", "claude-sonnet-4-5")


@lru_cache(maxsize=1)
def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=api_key)


def llm_available() -> bool:
    return _client() is not None


def classify_line_item(raw_label: str, statement: str, candidates: list[str]) -> tuple[str | None, str]:
    """Ask Claude to map one ambiguous raw label onto the canonical schema.

    Returns (canonical_item_or_None, rationale). Never raises on a bad or
    missing key -- callers should check llm_available() first, but this is
    defensive in case the key is revoked mid-run.
    """
    client = _client()
    if client is None:
        return None, "LLM not configured"

    prompt = (
        "You are mapping one line item from a small business's financial "
        f"statement onto a fixed canonical chart of accounts.\n\n"
        f"Statement type: {statement}\n"
        f"Raw label from the source file: \"{raw_label}\"\n\n"
        f"Candidate canonical line items:\n{json.dumps(candidates, indent=2)}\n\n"
        "Reply with strict JSON only, no prose outside the JSON object:\n"
        '{"canonical_item": "<one of the candidates, or null if none fit>", '
        '"rationale": "<one sentence>"}'
    )
    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        parsed = json.loads(text)
        item = parsed.get("canonical_item")
        if item not in candidates:
            item = None
        return item, parsed.get("rationale", "")
    except Exception as exc:  # noqa: BLE001 - defensive: never break the pipeline on an LLM hiccup
        return None, f"LLM call failed: {exc}"


def narrate_anomaly(flag_name: str, detail: dict) -> str | None:
    """Ask Claude for a one-paragraph, plain-English explanation of a rules-detected flag.

    Returns None (not an empty string) if the LLM isn't configured, so
    callers can fall back to the rule's own templated description.
    """
    client = _client()
    if client is None:
        return None

    prompt = (
        "You are a McKinsey-trained M&A diligence analyst writing one short "
        "paragraph for a buy-side memo. Explain the following flag in plain "
        "English for a business owner who is not an accountant. Be direct, "
        "state the number, state why it matters, and do not editorialize "
        "beyond what the data supports. No more than 3 sentences. No "
        "consultant jargon, no em-dashes.\n\n"
        f"Flag: {flag_name}\n"
        f"Supporting data: {json.dumps(detail, default=str)}\n"
    )
    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=220,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
    except Exception:  # noqa: BLE001
        return None
