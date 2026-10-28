"""`wifi-utility watch`: one line per second in the terminal (SPEC §4.7c). Home mode only."""

from __future__ import annotations

import sys
from collections import deque
from datetime import datetime
from typing import TextIO

SPARK = "▁▂▃▄▅▆▇█"
RED, GREEN, YELLOW, DIM, RESET = "\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"


def sparkline(values: list[float | None], top: float = 10.0) -> str:
    chars = []
    for v in values:
        if v is None:
            chars.append(" ")
        else:
            i = min(len(SPARK) - 1, max(0, int(v / top * (len(SPARK) - 1) + 0.5)))
            chars.append(SPARK[i])
    return "".join(chars)


class TerminalView:
    def __init__(self, out: TextIO = sys.stdout, color: bool | None = None):
        self.out = out
        self.color = out.isatty() if color is None else color
        self.scores: deque[float | None] = deque(maxlen=60)
        self._last_state: str | None = None

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.color else text

    def format(self, msg: dict) -> str:
        if msg.get("mode") != "home":
            raise ValueError("terminal view only supports home mode")
        self.scores.append(msg["score"])
        t = datetime.fromtimestamp(msg["ts"]).strftime("%H:%M:%S")
        sig = msg["signal"]
        rssi = f"{sig['rssi_dbm']:4d} dBm" if sig else "   -    "
        score = f"{msg['score']:4.1f}" if msg["score"] is not None else "  - "
        state = msg["state"]
        label = {
            "motion": self._c(RED, "MOTION"),
            "idle": self._c(GREEN, "idle  "),
            "uncalibrated": self._c(YELLOW, "uncalibrated (run: wifi-utility calibrate)"),
            "warming_up": self._c(DIM, "warming up"),
            "no_signal": self._c(YELLOW, "no signal"),
        }.get(state, state)
        if state == "idle" and msg["score"] is not None and msg["score"] >= msg["release"]:
            label = self._c(YELLOW, "rising")
        extra = ""
        if msg["confidence"] and state in ("idle", "motion"):
            extra = f" conf={msg['confidence']} cal={msg['calibration_quality']}"
        line = f"{t} {sparkline(list(self.scores))} {score} {label} RSSI {rssi}{extra}"
        if self._last_state is not None and state != self._last_state:
            line += self._c(RED if state == "motion" else GREEN, f"  ← {state}")
        self._last_state = state
        return line

    def __call__(self, msg: dict) -> None:
        print(self.format(msg), file=self.out, flush=True)
