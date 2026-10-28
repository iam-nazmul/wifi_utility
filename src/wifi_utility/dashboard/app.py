"""Local dashboard (SPEC §4.6, §4.7b, §6.3, §6.5).

Admin routes need the password when one is configured (required for non-local binds).
The public policy page (/policy) is open to the LAN in workplace mode and shows the same
aggregate data the employer sees.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse

from ..config import Config
from ..display.stream import LiveStream
from ..modes import home as home_mode
from ..modes import workplace as wp
from ..store import Store

STATIC = Path(__file__).parent / "static"
DAY = 86400


@dataclass
class DashboardContext:
    cfg: Config
    store: Store
    stream: LiveStream
    clock: Callable[[], float] = time.time

    @property
    def workplace(self) -> bool:
        return self.cfg.mode == "workplace"


def _authorized(header: str | None, password: str) -> bool:
    if not password:
        return True
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        _, _, given = base64.b64decode(header[6:]).decode().partition(":")
    except ValueError:
        return False
    return secrets.compare_digest(given, password)


def create_app(ctx: DashboardContext) -> FastAPI:
    app = FastAPI(title="wifi-utility", docs_url=None, redoc_url=None, openapi_url=None)
    password = ctx.cfg.dashboard.password

    def admin(request: Request) -> None:
        if not _authorized(request.headers.get("authorization"), password):
            raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="wifi-utility"'})

    def audit_view(what: str) -> None:
        # Every workplace dashboard view and export is audited (SPEC §6.4).
        if ctx.workplace:
            ctx.store.audit(ctx.clock(), "dashboard", "view", what)

    def page(name: str) -> HTMLResponse:
        return HTMLResponse((STATIC / name).read_text())

    def require(mode: str) -> None:
        if ctx.cfg.mode != mode:
            raise HTTPException(404)

    @app.get("/", dependencies=[Depends(admin)])
    def index():
        audit_view("dashboard")
        return page("index.html")

    @app.get("/live", dependencies=[Depends(admin)])
    def live():
        audit_view("live")
        return page("live.html")

    @app.get("/api/status", dependencies=[Depends(admin)])
    def status():
        _, latest = ctx.stream.latest()
        out = {"mode": ctx.cfg.mode, "latest": latest}
        if ctx.workplace:
            st = wp.collection_status(ctx.store, ctx.cfg, ctx.clock())
            out["collection"] = {
                "active": st.collecting,
                "reason": st.reason,
                "version": st.version,
            }
        else:
            out["notice"] = "Tell other household members and guests that the motion sensor is on."
        return out

    @app.get("/api/history", dependencies=[Depends(admin)])
    def history():
        return ctx.stream.history()

    @app.get("/api/events", dependencies=[Depends(admin)])
    def events(days: int = 7):
        require("home")
        return ctx.store.events(since=ctx.clock() - days * DAY)

    @app.post("/api/arm", dependencies=[Depends(admin)])
    def arm():
        require("home")
        _, latest = ctx.stream.latest()
        quality = latest["calibration_quality"] if latest else None
        try:
            home_mode.arm(ctx.store, ctx.clock(), "dashboard", quality)
        except home_mode.ArmError as e:
            raise HTTPException(409, str(e)) from e
        return {"armed_at": ctx.clock(), "exit_delay_secs": ctx.cfg.home.exit_delay_secs}

    @app.post("/api/disarm", dependencies=[Depends(admin)])
    def disarm():
        require("home")
        home_mode.disarm(ctx.store, ctx.clock(), "dashboard")
        return {"armed": False}

    @app.get("/api/occupancy", dependencies=[Depends(admin)])
    def occupancy(days: int = 7):
        require("workplace")
        audit_view(f"occupancy days={days}")
        return ctx.store.occupancy(since=ctx.clock() - days * DAY)

    @app.get("/api/export.csv", dependencies=[Depends(admin)])
    def export(days: int = 30):
        require("workplace")
        ctx.store.audit(ctx.clock(), "dashboard", "export", f"days={days}")
        return PlainTextResponse(
            occupancy_csv(ctx.store.occupancy(since=ctx.clock() - days * DAY)),
            media_type="text/csv",
        )

    @app.get("/api/audit", dependencies=[Depends(admin)])
    def audit_log():
        audit_view("audit_log")
        return ctx.store.audit_log()

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        if not _authorized(ws.headers.get("authorization"), password):
            await ws.close(code=4401)
            return
        await ws.accept()
        if ctx.workplace:
            ctx.store.audit(ctx.clock(), "dashboard", "view", "live_ws")
        try:
            await ws.send_json({"history": ctx.stream.history()})
            last_seq = ctx.stream.seq
            while True:
                await asyncio.sleep(0.25)
                seq, latest = ctx.stream.latest()
                if seq != last_seq and latest is not None:
                    last_seq = seq
                    await ws.send_json(latest)
        except (WebSocketDisconnect, RuntimeError):
            return

    # Public, read-only transparency page (workplace only, no password).
    @app.get("/policy")
    def policy_page():
        require("workplace")
        return page("policy.html")

    @app.get("/policy/data")
    def policy_data():
        require("workplace")
        now = ctx.clock()
        st = wp.collection_status(ctx.store, ctx.cfg, now)
        active = ctx.store.active_policy()
        return {
            "collection_active": st.collecting,
            "status": st.reason,
            "version": st.version,
            "effective_ts": active["effective_ts"] if active else None,
            "text": active["text"] if active else "",
            "records_names": bool(st.policy and st.policy.records_names),
            "acknowledgements": ctx.store.ack_count(st.version) if st.version else 0,
            "occupancy": ctx.store.occupancy(since=now - 7 * DAY),
        }

    @app.post("/policy/ack")
    async def policy_ack(request: Request):
        require("workplace")
        body = await request.json() if await request.body() else {}
        name = str(body.get("name", "")).strip()[:100] or None
        try:
            version = wp.record_ack(ctx.store, ctx.clock(), name)
        except wp.PolicyError as e:
            raise HTTPException(409, str(e)) from e
        return {"acknowledged_version": version}

    return app


def occupancy_csv(rows: list[dict]) -> str:
    from datetime import UTC, datetime

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["zone_id", "bucket_start_iso", "occupied"])
    for r in rows:
        iso = datetime.fromtimestamp(r["bucket_start"], UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        writer.writerow([r["zone_id"], iso, int(r["occupied"])])
    return buf.getvalue()


def serve(ctx: DashboardContext, stop_event=None) -> None:
    """Run uvicorn (blocking). Call from a background thread when the menu bar owns the main one."""
    import uvicorn

    config = uvicorn.Config(
        create_app(ctx),
        host=ctx.cfg.dashboard.bind,
        port=ctx.cfg.dashboard.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    if stop_event is not None:
        import threading

        def watch_stop():
            stop_event.wait()
            server.should_exit = True

        threading.Thread(target=watch_stop, daemon=True).start()
    server.run()
