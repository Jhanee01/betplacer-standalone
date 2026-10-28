"""
Fióknév, egyenleg és nyitott fogadások kiolvasása — CSAK OLVAS.

Az iroda már belépett böngészőjében, egy KÜLÖN lapon fut (a motor lapja két tipp
között parkol, és a fogadó lapot nem zavarjuk). A nyitott fogadások oldalát nyitjuk
meg, és az oldal saját API-válaszait fogjuk el (2026-10-28-i feltérképezés):

  TippmixPro: Napló „nyitott” fül → .../v1/player/session/player (Username),
              .../v1/player/<id>/balance (calculatedBalances[0].amount),
              everymatrix bets-api /open-bets (lista, totalBetAmount)
  Vegas:      Fogadásaim → /api/player (nickName), /api/player/balance (balance),
              Altenar widgetExpandedBetHistory (az alapnézet = nyitott, bets[].totalStake)

A motor saját szálán kell hívni (run_in_executor az iroda executorával).
"""

_PAGES = {
    "tippmixpro": "https://www.tippmixpro.hu/hu/fogadas/i/naplo/open/1",
    "vegas":      "https://vegas.hu/sports/my-bets",
}


def _kind(bm: str, url: str) -> str:
    u = url.split("?", 1)[0]
    if bm == "tippmixpro":
        if u.endswith("/player/session/player"):
            return "name"
        if "/v1/player/" in u and u.endswith("/balance"):
            return "balance"
        if u.endswith("/open-bets"):
            return "open"
    else:
        if u.endswith("vegas.hu/api/player"):
            return "name"
        if u.endswith("vegas.hu/api/player/balance"):
            return "balance"
        if "widgetExpandedBetHistory" in u:
            return "open"
    return ""


def read_info(engine, bm: str, timeout_s: int = 40) -> dict:
    """{'account', 'balance', 'open_count', 'open_stake'} — ami nem jött meg, kimarad."""
    page0 = engine._page
    context = page0.context
    got: dict = {}

    def on_response(resp):
        kind = _kind(bm, resp.url)
        if not kind or kind in got:
            return
        try:
            got[kind] = resp.json()
        except Exception:
            pass

    tab = context.new_page()
    tab.on("response", on_response)
    try:
        tab.goto(_PAGES[bm], wait_until="domcontentloaded", timeout=60000)
        for _ in range(timeout_s * 2):
            if {"name", "balance", "open"} <= got.keys():
                break
            tab.wait_for_timeout(500)
    finally:
        try:
            tab.close()
        except Exception:
            pass

    out: dict = {}
    name, bal, opn = got.get("name"), got.get("balance"), got.get("open")
    if isinstance(name, dict):
        out["account"] = name.get("Username") or name.get("nickName") or None
    if isinstance(bal, dict):
        if bm == "tippmixpro":
            cb = bal.get("calculatedBalances") or []
            amount = cb[0].get("amount") if cb else None
        else:
            amount = bal.get("balance")
        if amount is not None:
            out["balance"] = float(amount)
    if opn is not None:
        bets = opn if isinstance(opn, list) else (opn.get("bets") if isinstance(opn, dict) else None)
        if isinstance(bets, list):
            out["open_count"] = len(bets)
            out["open_stake"] = sum(float(b.get("totalBetAmount") or b.get("totalStake") or 0)
                                    for b in bets)
    return out
