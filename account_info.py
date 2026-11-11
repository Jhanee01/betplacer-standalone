"""
Fióknév, egyenleg és nyitott fogadások kiolvasása — CSAK OLVAS.

Az iroda már belépett böngészőjében, egy KÜLÖN lapon fut (a motor lapja két tipp
között parkol, és a fogadó lapot nem zavarjuk). A nyitott fogadások oldalát nyitjuk
meg, és az oldal saját API-válaszait fogjuk el (2026-10-28-i feltérképezés):

  TippmixPro: Napló „nyitott” fül → .../v1/player/session/player (Username),
              .../v1/player/<id>/balance (calculatedBalances[0].amount),
              everymatrix bets-api /open-bets (lista, totalBetAmount)
  Vegas:      Fogadásaim (?page=betHistory) → ews-crm/player/players (username),
              ews-crm/v2/player/live-chat/data (totalBalance),
              Altenar widgetExpandedBetHistory (az alapnézet = nyitott, bets[].totalStake)

A motor saját szálán kell hívni (run_in_executor az iroda executorával).
"""

_PAGES = {
    "tippmixpro": "https://www.tippmixpro.hu/hu/fogadas/i/naplo/open/1",
    # 2026-11-05 óta (új Vegas-oldal) a Fogadásaim címe ?page=betHistory
    "vegas":      "https://vegas.hu/sports?page=betHistory",
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
        # régi oldal: vegas.hu/api/player(/balance); új oldal (2026-11-05):
        # egt-digital.com …/ews-crm/player/players (username) és
        # …/ews-crm/v2/player/live-chat/data (totalBalance)
        if u.endswith("vegas.hu/api/player") or u.endswith("/ews-crm/player/players"):
            return "name"
        if u.endswith("vegas.hu/api/player/balance") or u.endswith("/player/live-chat/data"):
            return "balance"
        if "widgetExpandedBetHistory" in u:
            return "open"
    return ""


def _keepalive(tab) -> None:
    """Felhasználói aktivitás (egérmozgás + kis görgetés) a Vegas-oldalon.

    2026-11-11: a belépés után ~30 perccel a Vegas kiléptette a programot, pedig a
    5 percenkénti fiókolvasás közben is ment — a szerverkérések nem számítanak
    aktivitásnak, az oldal a böngészős bevitelt figyeli. Kiléptetés után pedig a
    Cloudflare miatt csak kézzel lehet visszalépni. Ezért minden fiókolvasáskor
    „mozgunk” a megnyitott Vegas-oldalon."""
    import random
    try:
        for _ in range(3):
            tab.mouse.move(random.randint(400, 1400), random.randint(250, 800),
                           steps=random.randint(8, 20))
            tab.wait_for_timeout(random.randint(150, 400))
        dy = random.randint(120, 300)
        tab.mouse.wheel(0, dy)
        tab.wait_for_timeout(random.randint(300, 700))
        tab.mouse.wheel(0, -dy)
        tab.wait_for_timeout(500)
    except Exception:
        pass


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
        if bm == "vegas":
            _keepalive(tab)
    finally:
        try:
            tab.close()
        except Exception:
            pass

    out: dict = {}
    name, bal, opn = got.get("name"), got.get("balance"), got.get("open")
    if isinstance(name, dict):
        out["account"] = (name.get("Username") or name.get("nickName")
                          or name.get("username") or None)
    if isinstance(bal, dict):
        if bm == "tippmixpro":
            cb = bal.get("calculatedBalances") or []
            amount = cb[0].get("amount") if cb else None
        else:
            amount = bal.get("balance", bal.get("totalBalance"))
        if amount is not None:
            out["balance"] = float(amount)
    if opn is not None:
        bets = opn if isinstance(opn, list) else (opn.get("bets") if isinstance(opn, dict) else None)
        if isinstance(bets, list):
            out["open_count"] = len(bets)
            out["open_stake"] = sum(float(b.get("totalBetAmount") or b.get("totalStake") or 0)
                                    for b in bets)
    return out
