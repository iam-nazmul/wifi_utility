"""The running app: owns the engine thread and lets the mode be switched while running.

A switch is a config change (SPEC §6.4): switching to workplace passes the same policy gates as
`run --mode workplace`, the new mode is written to config.toml, and the change is audited.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .config import Config, ConfigError, data_dir
from .display.stream import LiveStream
from .engine import Engine
from .modes import home as home_mode
from .modes import workplace as wp
from .notifier import Notifier
from .sources.base import SignalSource
from .store import Store

log = logging.getLogger(__name__)

MODE_LINE = re.compile(r"^mode\s*=.*$", re.M)


class SwitchError(RuntimeError):
    pass


def audit_config(store: Store, cfg: Config, now: float, actor: str) -> None:
    """Record config changes in the audit log (SPEC §6.4)."""
    text = cfg.source_path.read_bytes() if cfg.source_path else b"<defaults>"
    digest = hashlib.sha256(text + cfg.mode.encode()).hexdigest()
    if store.kv_get("config_hash") != digest:
        store.kv_set("config_hash", digest)
        store.audit(
            now, actor, "config_changed", f"{cfg.source_path or 'defaults'} mode={cfg.mode}"
        )


def write_mode(path: Path | None, mode: str) -> Path:
    """Set `mode = "<mode>"` in the config file, creating one in the data dir if there is none."""
    if path is None:
        path = data_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    line = f'mode = "{mode}"'
    text = MODE_LINE.sub(line, text, count=1) if MODE_LINE.search(text) else f"{line}\n{text}"
    path.write_text(text)
    return path


class Runtime:
    def __init__(
        self,
        cfg: Config,
        source_factory: Callable[[], SignalSource],
        notifier: Notifier,
        clock: Callable[[], float] = time.time,
        persist: bool = True,
    ):
        self.cfg = cfg
        self.source_factory = source_factory
        self.notifier = notifier
        self.clock = clock
        self.persist = persist
        self.app_stop = threading.Event()  # set when the whole app should exit
        self.on_change: list[Callable[[Runtime], None]] = []
        self.store: Store | None = None
        self.stream: LiveStream | None = None
        self.engine: Engine | None = None
        self._engine_stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending = None

    @property
    def mode(self) -> str:
        return self.cfg.mode

    def _build(self, mode: str):
        cfg = dataclasses.replace(self.cfg, mode=mode)
        try:
            cfg.validate()
        except ConfigError as e:
            raise SwitchError(str(e)) from e
        store = Store(workplace=mode == "workplace")
        if mode == "workplace":
            try:
                handler = wp.WorkplaceMode(cfg, store, self.notifier, self.clock())
            except wp.GateError as e:
                store.close()
                raise SwitchError(f"workplace mode will not start: {e}") from e
        else:
            handler = home_mode.HomeMode(cfg, store, self.notifier)
        stream = LiveStream(mode)
        engine = Engine(cfg, self.source_factory(), store=store, stream=stream, mode=handler)
        return cfg, store, stream, engine

    def _activate(self, built) -> None:
        self.cfg, self.store, self.stream, self.engine = built
        stop = threading.Event()
        engine = self.engine

        def run():
            try:
                engine.run(stop)
            except Exception:
                log.exception("engine stopped")
                if not stop.is_set():
                    self.app_stop.set()

        self._engine_stop = stop
        self._thread = threading.Thread(target=run, name=f"engine-{self.cfg.mode}", daemon=True)
        self._thread.start()
        for fn in self.on_change:
            fn(self)

    def prepare(self) -> None:
        """Check the configured mode can start (policy gates) without starting anything."""
        self._pending = self._build(self.cfg.mode)

    def start(self) -> None:
        """Start in the configured mode; raises SwitchError if its gates don't pass."""
        built = self._pending or self._build(self.cfg.mode)
        self._pending = None
        audit_config(built[1], built[0], self.clock(), "engine")
        self._activate(built)

    def stop(self) -> None:
        if self._engine_stop:
            self._engine_stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def switch(self, mode: str, actor: str) -> str:
        if mode not in ("home", "workplace"):
            raise SwitchError(f"unknown mode {mode!r}")
        with self._lock:
            old = self.cfg.mode
            if mode == old:
                return f"Already in {mode} mode"
            built = self._build(mode)  # gates checked before anything is stopped
            # The old store isn't closed here: a dashboard request may still be using it.
            self.stop()
            cfg = built[0]
            if self.persist:
                cfg.source_path = write_mode(cfg.source_path, mode)
            now = self.clock()
            built[1].audit(now, actor, "mode_changed", f"{old} → {mode}")
            audit_config(built[1], cfg, now, actor)
            self._activate(built)
            log.info("switched mode %s → %s", old, mode)
            return f"Switched to {mode} mode"
