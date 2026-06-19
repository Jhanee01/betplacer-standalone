"""
BetPlacer Standalone — GUI ablak (tkinter, FIFA Tipster stílus).
"""

import asyncio
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import ttk

from config import APP_VERSION

# ── Színek ────────────────────────────────────────────────────────────────────
C_BG      = "#12121f"
C_CARD    = "#1e2028"
C_BORDER  = "#2a2d3a"
C_FG      = "#d9d9d9"
C_MUTED   = "#8d8d9f"
C_ACCENT  = "#FDB900"
C_GREEN   = "#3ba560"
C_RED     = "#d44a3a"
C_BLUE    = "#5794f2"
C_BTN_BG  = "#0a1e0a"
C_ENTRY_BG = "#16161f"

FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_HDR   = ("Segoe UI", 11, "bold")
FONT_BODY  = ("Segoe UI", 10)
FONT_MONO  = ("Consolas", 10)
FONT_BTN   = ("Segoe UI", 11, "bold")

# ── Log tag → szín ────────────────────────────────────────────────────────────
TAG_COLORS = {
    "info":   C_FG,
    "muted":  C_MUTED,
    "tip":    C_ACCENT,   # új tipp érkezett
    "ok":     C_GREEN,    # [BET_OK]
    "fail":   C_RED,      # [BET_FAIL]
    "warn":   "#f2cc0c",
    "error":  C_RED,
}


class _TeeToConsole:
    """
    sys.stdout / stderr átirányítás a GUI „Konzol" fülre — az eredeti stream
    (CMD ablak) megtartása mellett. Szálbiztos: a Text widgetet root.after-en
    keresztül frissíti, így háttérszálból (Playwright/Telethon) is hívható.
    """
    def __init__(self, root, text_widget, tee=None):
        self._root = root
        self._text = text_widget
        self._tee  = tee

    def write(self, s):
        if self._tee is not None:
            try:
                self._tee.write(s)
                self._tee.flush()
            except Exception:
                pass
        if not s:
            return

        def _do():
            try:
                self._text.config(state="normal")
                self._text.insert("end", s)
                # méretkorlát: nagyon hosszú futásnál vágjuk az elejét
                if int(self._text.index("end-1c").split(".")[0]) > 3000:
                    self._text.delete("1.0", "1000.0")
                self._text.see("end")
                self._text.config(state="disabled")
            except tk.TclError:
                pass

        try:
            self._root.after(0, _do)
        except (tk.TclError, RuntimeError):
            pass

    def flush(self):
        if self._tee is not None:
            try:
                self._tee.flush()
            except Exception:
                pass


class BetPlacerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BetPlacer")
        self.root.configure(bg=C_BG)
        self.root.resizable(True, True)
        self.root.geometry("640x720")
        self.root.minsize(520, 520)
        self.root.eval("tk::PlaceWindow . center")

        self._running  = False
        self._loop     = None
        self._thread   = None
        self._stop_evt = threading.Event()
        self._tip_rows = {}          # dedup-kulcs → Treeview sor azonosító

        # Frissítő állapot
        self._checking     = False
        self._update_info  = None    # a legújabb kiadás adatai, ha van újabb
        self._update_state = "idle"

        self._build_ui()
        self._install_stdout_redirect()
        self._log("BetPlacer kész. Kattints az Indítás gombra.", "muted")

        # Csendes auto-ellenőrzés indítás után pár másodperccel.
        self.root.after(4000, lambda: self._check_updates(silent=True))

    # ── UI felépítése ─────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Fejléc ──────────────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=C_BG)
        hdr.pack(fill="x", padx=20, pady=(16, 0))

        tk.Label(hdr, text="⚽  BetPlacer", bg=C_BG, fg=C_ACCENT,
                 font=FONT_TITLE).pack(side="left")

        self._status_lbl = tk.Label(hdr, text="● LEÁLLÍTVA",
                                    bg=C_BG, fg=C_MUTED, font=FONT_BODY)
        self._status_lbl.pack(side="right", padx=4)

        # ── Verzió + Frissítés gomb (a státusztól balra) ─────────────────────
        self._ver_lbl = tk.Label(hdr, text=f"v{APP_VERSION}", bg=C_BG,
                                 fg=C_MUTED, font=("Segoe UI", 9))
        self._ver_lbl.pack(side="right", padx=(0, 12))

        # Sárga keretes stílus — mint a fő dashboard gombjai
        # (border: 1px solid #FDB900, arany szöveg, sötét háttér).
        self._update_btn = tk.Button(
            hdr, text="Frissítés keresése", command=self._on_update_click,
            bg=C_BTN_BG, fg=C_ACCENT,
            activebackground="#1a3a1a", activeforeground=C_ACCENT,
            relief="flat", bd=0, padx=10, pady=3,
            font=("Segoe UI", 9), cursor="hand2",
            highlightthickness=1, highlightbackground=C_ACCENT,
        )
        self._update_btn.pack(side="right", padx=(0, 10))

        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill="x",
                                                          padx=20, pady=(10, 0))

        # ── Info sor ────────────────────────────────────────────────────────
        info = tk.Frame(self.root, bg=C_BG)
        info.pack(fill="x", padx=20, pady=(8, 0))

        channel  = os.getenv("TELEGRAM_CHANNEL", "—")
        stake    = os.getenv("BET_STAKE", "500")
        dry_run  = os.getenv("BET_DRY_RUN", "0") == "1"

        # ── Gomb (jobbra) ────────────────────────────────────────────────────
        self._btn = tk.Button(
            info, text="▶  Indítás", command=self._toggle,
            bg=C_BTN_BG, fg=C_ACCENT,
            activebackground="#1a3a1a", activeforeground=C_ACCENT,
            relief="flat", bd=0, padx=18, pady=6,
            font=FONT_BTN, cursor="hand2",
            highlightthickness=1, highlightbackground=C_ACCENT,
        )
        self._btn.pack(side="right")

        # ── Csatorna + szerkeszthető tét (balra) ──────────────────────────────
        tk.Label(info, text=f"Csatorna: {channel}    |    ",
                 bg=C_BG, fg=C_MUTED, font=FONT_BODY).pack(side="left")
        tk.Label(info, text="Tét:", bg=C_BG, fg=C_MUTED,
                 font=FONT_BODY).pack(side="left")

        self._stake_var = tk.StringVar(value=stake)
        self._stake_entry = tk.Entry(
            info, textvariable=self._stake_var, width=7,
            bg=C_ENTRY_BG, fg=C_FG, insertbackground=C_FG,
            relief="flat", bd=0, font=FONT_BODY, justify="right",
            highlightthickness=1, highlightbackground=C_BORDER,
            highlightcolor=C_ACCENT)
        self._stake_entry.pack(side="left", padx=(6, 4), ipady=2)
        tk.Label(info, text="Ft", bg=C_BG, fg=C_MUTED,
                 font=FONT_BODY).pack(side="left")
        if dry_run:
            tk.Label(info, text="   [DRY RUN]", bg=C_BG, fg=C_ACCENT,
                     font=FONT_BODY).pack(side="left")

        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill="x",
                                                          padx=20, pady=(8, 0))

        # ── Mai tippek panel ─────────────────────────────────────────────────
        tk.Label(self.root, text="Mai tippek", bg=C_BG, fg=C_MUTED,
                 font=FONT_HDR, anchor="w").pack(fill="x", padx=20, pady=(10, 2))

        self._build_tips_table()

        # ── Napló + Konzol fülek ─────────────────────────────────────────────
        style = ttk.Style()
        style.configure("Dark.TNotebook", background=C_BG, borderwidth=0)
        style.configure("Dark.TNotebook.Tab",
                        background=C_CARD, foreground=C_MUTED,
                        padding=(14, 6), font=FONT_BODY, borderwidth=0)
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", C_BORDER)],
                  foreground=[("selected", C_ACCENT)])

        nb = ttk.Notebook(self.root, style="Dark.TNotebook")
        nb.pack(fill="both", expand=True, padx=20, pady=(8, 8))

        log_frame, self._console = self._make_console(nb)   # magas szintű napló
        raw_frame, self._raw     = self._make_console(nb)   # részletes [bet]/[tg] konzol
        nb.add(log_frame, text="  Napló  ")
        nb.add(raw_frame, text="  Konzol (részletes)  ")

        # Színes tagek csak a Naplóhoz
        for tag, color in TAG_COLORS.items():
            self._console.tag_configure(tag, foreground=color)

        # ── Státuszsor ──────────────────────────────────────────────────────
        foot = tk.Frame(self.root, bg=C_BG)
        foot.pack(fill="x", padx=20, pady=(0, 12))
        self._foot_lbl = tk.Label(foot, text="", bg=C_BG, fg=C_MUTED,
                                  font=("Segoe UI", 9))
        self._foot_lbl.pack(side="left")

    def _make_console(self, parent):
        """Egy görgethető, csak-olvasható szövegdoboz (Napló / Konzol fülhöz)."""
        frame = tk.Frame(parent, bg=C_CARD)
        txt = tk.Text(frame, bg=C_CARD, fg=C_FG, font=FONT_MONO,
                      relief="flat", bd=0, state="disabled", wrap="word",
                      insertbackground=C_FG, selectbackground=C_BORDER,
                      padx=10, pady=8)
        sb = tk.Scrollbar(frame, command=txt.yview, bg=C_BORDER,
                          troughcolor=C_BG, activebackground=C_MUTED)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        return frame, txt

    # ── stdout/stderr átirányítás a „Konzol" fülre ─────────────────────────────

    def _install_stdout_redirect(self):
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = _TeeToConsole(self.root, self._raw, tee=self._orig_stdout)
        sys.stderr = _TeeToConsole(self.root, self._raw, tee=self._orig_stderr)

    def _restore_stdout(self):
        try:
            if getattr(self, "_orig_stdout", None) is not None:
                sys.stdout = self._orig_stdout
            if getattr(self, "_orig_stderr", None) is not None:
                sys.stderr = self._orig_stderr
        except Exception:
            pass

    # ── „Mai tippek" táblázat ──────────────────────────────────────────────────

    def _build_tips_table(self):
        # ttk Treeview sötét témára igazítva
        style = ttk.Style()
        try:
            style.theme_use("clam")   # a "clam" engedi a háttérszín-állítást
        except tk.TclError:
            pass
        style.configure("Tips.Treeview",
                        background=C_CARD, fieldbackground=C_CARD,
                        foreground=C_FG, rowheight=24,
                        borderwidth=0, font=FONT_BODY)
        style.configure("Tips.Treeview.Heading",
                        background=C_BORDER, foreground=C_ACCENT,
                        relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Tips.Treeview",
                  background=[("selected", C_BORDER)],
                  foreground=[("selected", C_FG)])

        frame = tk.Frame(self.root, bg=C_CARD,
                         highlightthickness=1, highlightbackground=C_BORDER)
        frame.pack(fill="x", padx=20, pady=(0, 4))

        cols = ("ido", "meccs", "pick", "allapot")
        self._tree = ttk.Treeview(frame, columns=cols, show="headings",
                                  height=7, style="Tips.Treeview")
        self._tree.heading("ido",     text="Idő")
        self._tree.heading("meccs",   text="Meccs")
        self._tree.heading("pick",    text="Pick")
        self._tree.heading("allapot", text="Állapot")
        self._tree.column("ido",     width=50,  anchor="w",      stretch=False)
        self._tree.column("meccs",   width=270, anchor="w",      stretch=True)
        self._tree.column("pick",    width=95,  anchor="w",      stretch=False)
        self._tree.column("allapot", width=120, anchor="w",      stretch=False)

        sb = tk.Scrollbar(frame, command=self._tree.yview,
                          bg=C_BORDER, troughcolor=C_BG, activebackground=C_MUTED)
        self._tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._tree.pack(side="left", fill="x", expand=True)

        # Állapot → sorszín
        self._tree.tag_configure("ok",      foreground=C_GREEN)
        self._tree.tag_configure("fail",    foreground=C_RED)
        self._tree.tag_configure("wait",    foreground=C_ACCENT)
        self._tree.tag_configure("placing", foreground=C_BLUE)

    def _on_status(self, key: str, tip, status: str, detail: str = ""):
        """A core hívja minden tipp-állapotváltáskor (háttérszálból)."""
        labels = {
            "pending":  ("⏳ várakozás",       "wait"),
            "placing":  ("⟳ megrakás...",      "placing"),
            "retry":    (f"⏳ esemény vár {detail}", "wait"),
            "ok":       ("✓ OK",               "ok"),
            "fail":     ("✗ sikertelen" + (f" ({detail})" if detail else ""), "fail"),
        }
        text, tag = labels.get(status, (status, "wait"))
        meccs = f"{tip.home_clean}–{tip.away_clean}"
        pick  = f"{tip.pick} {tip.line}"
        values = (tip.time, meccs, pick, text)

        def _do():
            iid = self._tip_rows.get(key)
            if iid and self._tree.exists(iid):
                self._tree.item(iid, values=values, tags=(tag,))
            else:
                iid = self._tree.insert("", "end", values=values, tags=(tag,))
                self._tip_rows[key] = iid
            self._tree.see(iid)

        try:
            self.root.after(0, _do)
        except tk.TclError:
            pass

    # ── Gomb kezelő ──────────────────────────────────────────────────────────

    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        # Tét beolvasása a mezőből → érvényesítés → env + .env frissítés.
        raw = self._stake_var.get().strip()
        try:
            stake = int(float(raw))
            if stake <= 0:
                raise ValueError
        except (ValueError, TypeError):
            messagebox.showerror("Hibás tét",
                                 "A tét csak pozitív egész szám lehet (Ft).")
            return
        self._stake_var.set(str(stake))
        os.environ["BET_STAKE"] = str(stake)
        self._persist_stake(str(stake))
        self._log(f"Tét: {stake} Ft", "muted")

        self._running  = True
        self._stop_evt.clear()
        self._update_status(True)
        self._log("Indítás...", "info")
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def _persist_stake(self, value: str):
        """A BET_STAKE sor frissítése a .env-ben (a többi sor érintetlen marad)."""
        try:
            from paths import APP_DIR
            path = APP_DIR / ".env"
            if not path.exists():
                return
            lines = path.read_text(encoding="utf-8").splitlines()
            out, found = [], False
            for ln in lines:
                if ln.startswith("BET_STAKE="):
                    out.append(f"BET_STAKE={value}")
                    found = True
                else:
                    out.append(ln)
            if not found:
                out.append(f"BET_STAKE={value}")
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
        except Exception as e:
            self._log(f"Tét mentése a .env-be sikertelen: {e}", "warn")

    def _stop(self):
        self._log("Leállítás... (a böngésző bezárása pár másodperc)", "warn")
        # CSAK jelzünk: a watcher gracefully lekapcsol, a run_session finally
        # ága lezárja a böngészőt, és a coroutine természetesen befejeződik.
        # (Korábban a loop.stop() megölte a coroutine-t a finally előtt → Chromium leak.)
        self._stop_evt.set()
        self._running = False
        self._update_status(False)

    # ── Háttér szál ──────────────────────────────────────────────────────────

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            self._log(f"Kritikus hiba: {e}", "error")
        finally:
            self._running = False
            self.root.after(0, lambda: self._update_status(False))

    async def _async_main(self):
        # A teljes futási logika a közös betplacer_core.run_session()-ben él
        # (ugyanazt használja a parancssori --no-gui mód is).
        from betplacer_core import run_session
        await run_session(
            log=self._log,
            foot=self._foot,
            stop_event=self._stop_evt,
            on_status=self._on_status,
        )

    # ── UI frissítések (mindig root.after-on át) ──────────────────────────────

    def _update_status(self, running: bool):
        def _do():
            if running:
                self._status_lbl.config(text="● FIGYELÉS AKTÍV", fg=C_GREEN)
                self._btn.config(text="■  Leállítás",
                                 bg="#3a1c1c", fg=C_RED,
                                 highlightbackground=C_RED)
                # Futás közben a tét nem módosítható (a motor indításkor olvassa).
                self._stake_entry.config(state="disabled",
                                         disabledbackground=C_BG,
                                         disabledforeground=C_MUTED)
            else:
                self._status_lbl.config(text="● LEÁLLÍTVA", fg=C_MUTED)
                self._btn.config(text="▶  Indítás",
                                 bg=C_BTN_BG, fg=C_ACCENT,
                                 highlightbackground=C_ACCENT)
                self._stake_entry.config(state="normal")
        self.root.after(0, _do)

    def _log(self, msg: str, kind: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")

        def _do():
            self._console.config(state="normal")
            self._console.insert("end", f"[{ts}] ", "muted")
            self._console.insert("end", f"{msg}\n", kind)
            self._console.see("end")
            self._console.config(state="disabled")

        # Záráskor a háttérszál még logolhat egy már megsemmisült ablakba.
        try:
            self.root.after(0, _do)
        except tk.TclError:
            pass

    def _foot(self, text: str):
        try:
            self.root.after(0, lambda: self._foot_lbl.config(text=text))
        except tk.TclError:
            pass

    # ── Frissítés (GitHub Release alapú önfrissítő) ────────────────────────────

    def _set_update_button(self, state: str, info=None):
        """A Frissítés gomb szövege/színe/állapota. Mindig a fő szálon hívd."""
        self._update_state = state
        tag = (info or {}).get("tag", "") if info else ""
        cfg = {
            "checking":    ("Keresés…",                 C_MUTED,  "disabled"),
            "idle":        ("Frissítés keresése",        C_MUTED,  "normal"),
            "uptodate":    ("✓ Naprakész",               C_GREEN,  "normal"),
            "available":   (f"⬇ Frissítés: {tag}",       C_ACCENT, "normal"),
            "downloading": ("Letöltés…",                 C_ACCENT, "disabled"),
        }
        text, fg, st = cfg.get(state, ("Frissítés keresése", C_MUTED, "normal"))
        try:
            self._update_btn.config(text=text, fg=fg, state=st)
        except tk.TclError:
            pass

    def _on_update_click(self):
        """Elérhető frissítés → telepítés; egyébként → kézi ellenőrzés."""
        if self._update_state == "available" and self._update_info:
            self._do_update()
        elif self._update_state not in ("checking", "downloading"):
            self._check_updates(silent=False)

    def _check_updates(self, silent: bool = True):
        """A legújabb kiadás ellenőrzése háttérszálon (nem blokkolja a UI-t)."""
        if self._checking:
            return
        self._checking = True
        self._set_update_button("checking")

        def _work():
            result = {"info": None, "checked": False}
            try:
                import updater
                rel = updater.check_latest_release()
                if rel is not None:
                    result["checked"] = True
                    if updater.is_newer(APP_VERSION, rel["tag"]):
                        result["info"] = rel
            except Exception:
                pass

            def _done():
                self._checking = False
                self._update_info = result["info"]
                if result["info"]:
                    self._set_update_button("available", result["info"])
                    self._log(f"Új verzió elérhető: {result['info']['tag']} — "
                              f"kattints a Frissítés gombra.", "tip")
                elif result["checked"]:
                    self._set_update_button("uptodate")
                    if not silent:
                        self._log("A program naprakész.", "muted")
                else:
                    self._set_update_button("idle")
                    if not silent:
                        messagebox.showwarning(
                            "Frissítés",
                            "Nem sikerült ellenőrizni a frissítést.\n"
                            "Ellenőrizd az internetkapcsolatot, és próbáld újra.")

            try:
                self.root.after(0, _done)
            except tk.TclError:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _do_update(self):
        """Megerősítés → letöltés → segéd indítása → kilépés (a segéd újraindít)."""
        data = self._update_info
        if not data or not data.get("asset_url"):
            messagebox.showwarning(
                "Frissítés",
                "Ehhez a kiadáshoz nincs letölthető frissítőcsomag (update.zip).")
            return

        notes = (data.get("body") or "").strip()
        if len(notes) > 600:
            notes = notes[:600] + "…"
        msg = f"Új verzió érhető el: {data['tag']}\n"
        if notes:
            msg += f"\n{notes}\n"
        msg += ("\nFrissíted most? A program letölti az új verziót, bezár, "
                "és automatikusan újraindul.")
        if not messagebox.askyesno("Frissítés elérhető", msg):
            return

        if self._running:
            self._stop()

        self._set_update_button("downloading")
        self._log("Frissítés letöltése...", "info")

        def _work():
            try:
                import updater
                from paths import APP_DIR
                zip_path = APP_DIR / "update.zip"
                if not updater.download_update(data["asset_url"], zip_path):
                    self.root.after(0, lambda: self._update_failed(
                        "A letöltés sikertelen. Próbáld újra később."))
                    return
                if not updater.apply_update_and_restart(zip_path):
                    self.root.after(0, lambda: self._update_failed(
                        "A frissítő segéd nem indult el."))
                    return
                # Kilépés, hogy a fájlok cserélhetők legyenek; a segéd újraindít.
                self.root.after(0, self._quit_for_update)
            except Exception as e:
                self.root.after(0, lambda: self._update_failed(str(e)))

        threading.Thread(target=_work, daemon=True).start()

    def _update_failed(self, reason: str):
        self._log(f"Frissítés sikertelen: {reason}", "error")
        messagebox.showerror("Frissítés", reason)
        self._set_update_button("available", self._update_info)

    def _quit_for_update(self):
        """A program leállítása, hogy a segéd lecserélhesse a fájlokat."""
        self._log("Bezárás a frissítéshez — a program mindjárt újraindul.", "muted")
        try:
            self._restore_stdout()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ── Belépési pont ─────────────────────────────────────────────────────────

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        if self._running:
            self._stop()
        self._restore_stdout()
        self.root.destroy()
