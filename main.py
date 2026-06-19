"""
Standalone BetPlacer — entry point.

Indítás:
  run.bat                      (ajánlott — nyitva tartja az ablakot)
  python main.py               tét: .env-ből
  python main.py --stake 1000  tét felülírva
  python main.py --dry-run     teszt mód
  python main.py --setup       wizard újra
"""

import argparse
import asyncio
import os
import sys
import traceback
from pathlib import Path

from paths import APP_DIR

ENV_PATH = APP_DIR / ".env"


def _load_env():
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)


def _parse_args():
    parser = argparse.ArgumentParser(description="Standalone BetPlacer")
    parser.add_argument("--stake",   type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--setup",  action="store_true")
    parser.add_argument("--no-gui", action="store_true",
                        help="Csak parancssor, GUI ablak nélkül")
    return parser.parse_args()


def _log(msg: str):
    from datetime import datetime
    print(f"[main] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def main():
    args = _parse_args()

    if not ENV_PATH.exists() or args.setup:
        reason = "Nincs .env konfiguráció" if not ENV_PATH.exists() else "--setup flag"
        _log(f"{reason} — setup wizard indul...")
        try:
            from setup_wizard import run_wizard
            run_wizard()
        except Exception as e:
            print(f"HIBA a wizardban: {e}")
            traceback.print_exc()
            input("\nNyomj Entert a kilépéshez...")
            sys.exit(1)

        if not ENV_PATH.exists():
            _log("Beállítás megszakítva (ablak bezárva).")
            input("\nNyomj Entert a kilépéshez...")
            sys.exit(0)

    _load_env()

    if args.stake is not None:
        os.environ["BET_STAKE"] = str(args.stake)
    if args.dry_run:
        os.environ["BET_DRY_RUN"] = "1"

    # ── GUI mód (alapértelmezett) ────────────────────────────────────────────
    if not args.no_gui:
        try:
            from gui import BetPlacerGUI
            app = BetPlacerGUI()
            app.run()
        except Exception as e:
            print(f"GUI hiba: {e}")
            traceback.print_exc()
            input("\nNyomj Entert a kilépéshez...")
        return

    # ── Parancssori mód (--no-gui) ───────────────────────────────────────────
    from betplacer_core import run_session

    stake   = os.getenv("BET_STAKE", "500")
    dry_run = os.getenv("BET_DRY_RUN", "0") == "1"
    mode    = "[DRY RUN] " if dry_run else ""
    _log(f"{mode}Indul — tét: {stake} Ft")

    def _cli_log(msg: str, kind: str = "info"):
        _log(msg)

    try:
        asyncio.run(run_session(log=_cli_log))
    except KeyboardInterrupt:
        _log("Leállítva (Ctrl+C).")
    except Exception as e:
        print(f"\nKRITIKUS HIBA: {e}")
        traceback.print_exc()
        input("\nNyomj Entert a kilépéshez...")
        sys.exit(1)


if __name__ == "__main__":
    main()
