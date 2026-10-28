"""launchd agent so the utility runs at login (SPEC §8)."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .config import data_dir

LABEL = "com.iam-nazmul.wifi-utility"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(mode: str, config_path: Path | None) -> dict:
    exe = shutil.which("wifi-utility") or str(Path(sys.executable).parent / "wifi-utility")
    args = [exe, "run", "--mode", mode]
    if config_path:
        args += ["--config", str(config_path)]
    logs = data_dir() / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": str(logs / "wifi-utility.log"),
        "StandardErrorPath": str(logs / "wifi-utility.err.log"),
    }


def install(mode: str, config_path: Path | None) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    (data_dir() / "logs").mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(build_plist(mode, config_path), f)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, check=False)
    subprocess.run(["launchctl", "load", "-w", str(path)], check=True)
    return path


def uninstall() -> bool:
    path = plist_path()
    if not path.exists():
        return False
    subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True, check=False)
    path.unlink()
    return True
