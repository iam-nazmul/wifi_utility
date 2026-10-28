"""PostToolUse(Edit|Write): format/lint Python, and remind about workplace review."""

import os
import shutil
import subprocess

from _common import PROJECT_DIR, add_context, read_input, rel_path

data = read_input()
path = rel_path((data.get("tool_input") or {}).get("file_path", ""))
notes = []

# Format + lint edited Python once the project has a pyproject.toml.
if path.endswith(".py") and not path.startswith(".claude/") and shutil.which("uv") \
        and os.path.exists(os.path.join(PROJECT_DIR, "pyproject.toml")):
    run = lambda *args: subprocess.run(  # noqa: E731
        ["uv", "run", "--quiet", "ruff", *args, path],
        cwd=PROJECT_DIR, capture_output=True, text=True, timeout=60,
    )
    try:
        run("format")
        check = run("check", "--fix")
        if check.returncode != 0:
            notes.append(f"ruff check reported issues in {path}:\n{check.stdout.strip()[:1500]}")
    except (OSError, subprocess.TimeoutExpired):
        pass  # ruff not installed yet or too slow; don't block editing

# Files reachable in workplace mode (SPEC §4.7, §6).
SENSITIVE = (
    "src/wifi_utility/modes/workplace.py",
    "src/wifi_utility/store.py",
    "src/wifi_utility/notifier.py",
    "src/wifi_utility/display/",
    "src/wifi_utility/dashboard/",
    "src/wifi_utility/cli.py",
    "config.example.toml",
    "policy/",
)
if path.startswith(SENSITIVE):
    notes.append(
        f"{path} is reachable in workplace mode. Before finishing, run the workplace-guard skill "
        "or the workplace-privacy-reviewer agent and make sure tests/test_workplace_gates.py passes."
    )

if notes:
    add_context("PostToolUse", "\n\n".join(notes))
