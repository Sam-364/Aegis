"""Neutralising model-authored text before a human reads it.

Everything the model writes eventually lands in front of an operator who is deciding whether to
approve an infrastructure change. Control characters, bidirectional overrides and zero-width
joiners let a prompt-injected model render one thing and mean another — "restart order-service"
that is really a rollback of the payment gateway. The runtime strips those at ingress, so the
stored record and every surface that reads it (console, CLI, Slack, audit export) agree.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# C0/C1 controls except tab and newline, bidi embedding/override/isolate marks, zero-width
# characters, the byte-order mark and Unicode line/paragraph separators.
_DANGEROUS = re.compile(
    "[\u0000-\u0008\u000b-\u001f\u007f-\u009f"
    "\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f"
    "\u2028\u2029\ufeff]"
)
_TRAILING_SPACE = re.compile(r"[ \t]+(?=\n)|[ \t]+$")


def sanitize_display_text(value: str) -> str:
    """Strip characters that can make text render differently from how it reads."""
    cleaned = unicodedata.normalize("NFC", value)
    cleaned = _DANGEROUS.sub("", cleaned)
    cleaned = cleaned.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    return _TRAILING_SPACE.sub("", cleaned)


def sanitize_tree(value: Any) -> Any:
    """Apply :func:`sanitize_display_text` to every string inside a nested structure."""
    if isinstance(value, str):
        return sanitize_display_text(value)
    if isinstance(value, list):
        return [sanitize_tree(v) for v in value]
    if isinstance(value, tuple):
        return tuple(sanitize_tree(v) for v in value)
    if isinstance(value, dict):
        return {k: sanitize_tree(v) for k, v in value.items()}
    return value
