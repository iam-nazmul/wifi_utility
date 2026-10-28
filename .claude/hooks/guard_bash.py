"""PreToolUse(Bash): enforce scope limits and git rules from CLAUDE.md."""

import re
import subprocess

from _common import PROJECT_DIR, block, read_input, real_bssids

data = read_input()
cmd = (data.get("tool_input") or {}).get("command", "")

# Scope limits: read only the Mac's own link, no root, no capture or scanning.
SCOPE_RULES = [
    (r"(^|[;&|]\s*|\s)sudo\s", "sudo is not needed; the utility reads RSSI without root."),
    (r"\btcpdump\b|\btshark\b|\bwireshark\b|\bdumpcap\b", "Packet capture is out of scope (SPEC §3 non-goals)."),
    (r"\bairport\b", "The airport CLI is removed/out of scope; use CoreWLAN (.claude/references/corewlan.md)."),
    (r"\bwdutil\b", "wdutil needs sudo; use CoreWLAN instead."),
    (r"\b(nmap|arp-scan|aircrack-ng|airodump-ng|bettercap|kismet)\b", "Network scanning/attack tools are out of scope."),
    (r"\bmonitor\s*mode\b|\bsniff\b", "Monitor mode / sniffing is out of scope."),
    (r"networksetup\s+-setairportpower\s+\S+\s+off", "Turning WiFi off would cut the sensing link; ask the user first."),
]
for pattern, reason in SCOPE_RULES:
    if re.search(pattern, cmd, re.IGNORECASE):
        block(f"Blocked by project hook: {reason}")

if re.search(r"\bgit\b.*\s--no-verify\b", cmd):
    block("Blocked by project hook: don't skip git hooks with --no-verify.")

if re.search(r"\bgit\s+commit\b", cmd):
    # User's global rule: no Claude attribution in commits.
    if re.search(r"Co-Authored-By:\s*Claude|Generated with \[?Claude Code", cmd, re.IGNORECASE):
        block("Blocked by project hook: remove Claude attribution lines (Co-Authored-By / Generated with Claude Code).")

    # User's global rule: commit only as iam-nazmul.
    email = subprocess.run(
        ["git", "config", "user.email"], cwd=PROJECT_DIR, capture_output=True, text=True
    ).stdout.strip()
    if email and email != "nazmul.cse48@gmail.com":
        block(f"Blocked by project hook: git user.email is '{email}', expected nazmul.cse48@gmail.com. Ask the user.")

    # Staged fixtures must not contain real router BSSIDs.
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", "tests/fixtures"],
        cwd=PROJECT_DIR, capture_output=True, text=True,
    ).stdout.split()
    leaks = []
    for path in staged:
        if not path.endswith(".csv"):
            continue
        content = subprocess.run(
            ["git", "show", f":{path}"], cwd=PROJECT_DIR, capture_output=True, text=True
        ).stdout
        if real_bssids(content):
            leaks.append(path)
    if leaks:
        block(
            "Blocked by project hook: real BSSIDs in staged fixtures "
            f"({', '.join(leaks)}). Replace with 00:00:00:00:00:xx placeholders (see record-fixture skill)."
        )
