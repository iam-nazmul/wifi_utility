"""Shared helpers for project hooks. Stdlib only."""

import json
import os
import re
import sys

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

# Fixtures must use placeholder BSSIDs (00:00:00:00:00:xx); anything else is a real router.
MAC_RE = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")
PLACEHOLDER_PREFIX = "00:00:00:00:00:"


def read_input():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def block(reason):
    """Exit 2: the tool call is blocked and the reason is shown to Claude."""
    print(reason, file=sys.stderr)
    sys.exit(2)


def add_context(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
    sys.exit(0)


def rel_path(path):
    if not path:
        return ""
    try:
        return os.path.relpath(os.path.abspath(path), PROJECT_DIR)
    except ValueError:
        return path


def real_bssids(text):
    return sorted({m for m in MAC_RE.findall(text) if not m.lower().startswith(PLACEHOLDER_PREFIX)})
