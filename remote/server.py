"""Remote webszerver — FastAPI app + uvicorn futtatás háttérszálon.

A Standalone BetPlacer egyszerűsített remote-ja: ÉLŐ NAPLÓ + ÚJRAINDÍTÁS gomb, más
semmi. A főablak (gui.py) a naplósorokat ide is továbbítja, az „Újraindítás" pedig
a meglévő GUI start/stop logikáját hívja egy Qt signalon keresztül.

Komponensek:
  - RemoteServer(QObject): a GUI-szálon él; gyűrűpufferbe gyűjti a naplósorokat és
    szálbiztosan elküldi a WebSocket-klienseknek. Az újraindítást Qt signallal jelzi
    a GUI-szálnak (queued connection).
  - create_app(): a FastAPI alkalmazás (statikus oldal + /ws/logs + /api/restart).
  - run_server(): uvicorn programatikus indítás, blokkoló — háttérszálon futtatandó.

Szálkezelés: a naplósorok a GUI-szálról érkeznek; a WebSocketek az uvicorn asyncio
event loopján futnak. A híd a kettő közt: loop.call_soon_threadsafe(...).
"""
import asyncio
import json
import sys
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock

from fastapi import (
    Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect,
    WebSocketException,
)
from fastapi.responses import FileResponse

from PySide6.QtCore import QObject, Signal

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"

_MAX_LINES = 1000


class RemoteServer(QObject):
    """Híd a GUI (napló + futási állapot) és a WebSocket-kliensek (asyncio) közt."""

    # Az asyncio-szálról emittáljuk; queued connection-nel a GUI-szálon futó slot
    # végzi el a tényleges újraindítást (lásd gui.py _remote_restart).
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer: deque[dict] = deque(maxlen=_MAX_LINES)
        self._clients: set[asyncio.Queue] = set()
        self._running = False
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- az uvicorn event loop becsatolása (a FastAPI startup-ból hívva) ---
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # --- futási állapot (a GUI-szálról frissítve) ---
    def set_running(self, running: bool) -> None:
        with self._lock:
            self._running = bool(running)

    def status_snapshot(self) -> dict:
        with self._lock:
            running = self._running
        return {"running": running}

    # --- naplósor a GUI-tól (GUI-szál) ---
    def push_log(self, line: str, kind: str = "info") -> None:
        entry = {"ts": datetime.now().strftime("%H:%M:%S"), "line": line, "kind": kind}
        with self._lock:
            self._buffer.append(entry)
            clients = list(self._clients)
        loop = self._loop
        if loop is None or not clients:
            return
        payload = json.dumps({"t": "log", **entry})
        for q in clients:
            try:
                loop.call_soon_threadsafe(q.put_nowait, payload)
            except RuntimeError:
                pass  # a loop épp leállt — a kliens úgyis lecsatlakozik

    # --- WebSocket-kliensek (az asyncio-szálról hívva) ---
    def register_client(self, queue: asyncio.Queue) -> list[dict]:
        with self._lock:
            self._clients.add(queue)
            return list(self._buffer)

    def unregister_client(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._clients.discard(queue)

    # --- vezérlés (az asyncio-szálról hívva) ---
    def request_restart(self) -> None:
        self.restart_requested.emit()


def create_app(remote_server: RemoteServer | None = None, token: str = "") -> FastAPI:
    """Felépíti a Remote FastAPI alkalmazást.

    A / (statikus váz) token nélkül is betölt (hogy a „Hozzáférés megtagadva"
    képernyő megjelenhessen), de az /api/* és a /ws/logs csak helyes token mellett.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if remote_server is not None:
            remote_server.attach_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="BetPlacer Remote", docs_url=None, redoc_url=None,
                  lifespan=lifespan)

    async def require_token(t: str = Query(default="", alias="token")):
        if not token or t != token:
            raise HTTPException(status_code=401, detail="Hozzáférés megtagadva")

    async def require_ws_token(websocket: WebSocket, t: str = Query(default="", alias="token")):
        if not token or t != token:
            raise WebSocketException(code=1008, reason="Hozzáférés megtagadva")

    @app.get("/")
    async def index():
        return FileResponse(_INDEX_HTML)

    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def api_status():
        if remote_server is None:
            return {"running": False}
        return remote_server.status_snapshot()

    @app.post("/api/restart", dependencies=[Depends(require_token)])
    async def api_restart():
        if remote_server is None:
            return {"ok": False, "message": "A szerver nincs összekötve a programmal."}
        remote_server.request_restart()
        return {"ok": True, "message": "Újraindítás elindítva."}

    @app.websocket("/ws/logs")
    async def ws_logs(ws: WebSocket, _=Depends(require_ws_token)):
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue()
        history = remote_server.register_client(queue) if remote_server else []
        try:
            await ws.send_text(json.dumps({"t": "hist", "lines": history}))
            while True:
                payload = await queue.get()
                await ws.send_text(payload)
        except WebSocketDisconnect:
            pass
        finally:
            if remote_server:
                remote_server.unregister_client(queue)

    return app


def run_server(host: str, port: int, remote_server: RemoteServer | None = None,
               token: str = "") -> None:
    """Elindítja a webszervert (blokkoló — háttérszálon kell futtatni)."""
    import os as _os
    import uvicorn

    # pythonw.exe / frozen exe alatt nincs konzol → sys.stdout=None; az uvicorn
    # naplózás-beállítása elhasal rajta. Pótoljuk.
    if sys.stdout is None:
        sys.stdout = open(_os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(_os.devnull, "w", encoding="utf-8")

    app = create_app(remote_server, token)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            access_log=False)
    server = uvicorn.Server(config)
    server.run()
    # Foglalt portnál az uvicorn nem dob kivételt, csak started=False — érthető hibává
    # alakítjuk, hogy a hívó (gui.py) elkaphassa és naplózhassa.
    if not getattr(server, "started", False):
        raise OSError(f"A webszerver nem tudott elindulni ({host}:{port}) — foglalt port?")


if __name__ == "__main__":
    # Kézi teszt log-forrás nélkül: python -m remote.server  →  http://127.0.0.1:8765/
    run_server("127.0.0.1", 8765)
