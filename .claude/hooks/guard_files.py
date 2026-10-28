"""PreToolUse(Edit|Write): protect secrets and data, keep real BSSIDs out of fixtures."""

import fnmatch

from _common import block, read_input, real_bssids, rel_path

data = read_input()
tool_input = data.get("tool_input") or {}
path = rel_path(tool_input.get("file_path", ""))

PROTECTED = [".env", ".env.*", "*.db", "*.sqlite", "*.sqlite3", ".git/*"]
for pattern in PROTECTED:
    if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.split("/")[-1], pattern):
        block(f"Blocked by project hook: {path} is protected (secrets, local data or git internals).")

if path.startswith("tests/fixtures/") and path.endswith(".csv"):
    new_text = tool_input.get("content") or tool_input.get("new_string") or ""
    found = real_bssids(new_text)
    if found:
        block(
            f"Blocked by project hook: {path} would contain real BSSIDs ({', '.join(found[:3])}). "
            "Use 00:00:00:00:00:xx placeholders."
        )
