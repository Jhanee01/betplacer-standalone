# BetPlacer Standalone

Automatikus fogadás Tippmixpro-n Telegram-csatorna alapján.  
A program figyeli a megadott Telegram-csatornát, és amikor új tipp érkezik, automatikusan megrakja a fogadást.

---

## Rendszerkövetelmények

- **Windows 10 / 11**
- Stabil internetkapcsolat
- **Python 3.11+** — ezt az alábbi **1. lépésben** telepíted

---

## Telepítés (egyszer kell)

### 1. lépés — Python telepítése  ⚠️ NE HAGYD KI!

A program Python nélkül **nem indul el**. 

1. Töltsd le innen: **https://www.python.org/downloads/** → sárga *Download Python* gomb
2. Indítsd el a letöltött telepítőt
3. **A telepítő legalján MINDENKÉPP pipáld be: „Add python.exe to PATH"** ← enélkül nem fog működni!
4. Kattints az *Install Now* gombra, várd meg, amíg végez

> **Már van Pythonod?** Ellenőrizd: `Win + R` →  írd be: `cmd` → Enter, majd a fekete ablakba: `python --version`
> Ha verziószámot ír (pl. `Python 3.12.1`), kész vagy, ugorj a 2. lépésre.

### 2. lépés — A program telepítése

1. Csomagold ki a `standalone_betplacer` mappát bárhova (pl. `C:\BetPlacer\`)
2. Nyisd meg a mappát, kattints duplán az **`install.bat`** fájlra
3. Várd meg, amíg befejezi (Python csomagok + Chromium letöltése ~2-5 perc)
4. Ha kész: `TELEPITES KESZ` felirat jelenik meg

> Ha az `install.bat` azt írja, hogy *„A Python nem található"*, akkor az 1. lépés
> kimaradt, vagy elfelejtetted bepipálni az *„Add python.exe to PATH"* opciót —
> telepítsd újra a Pythont a pipával.

---

## Első indítás

Kattints duplán a **`run.bat`** fájlra (vagy parancssorból: `python main.py`).

Megnyílik a **beállítási varázsló** (5 lépés):

| Lépés | Mit kér? | Honnan? |
|-------|----------|---------|
| 1 | Telefonszám | Saját Telegram-fiók |
| 2 | Megerősítő kód | SMS / Telegram értesítés |
| 3 | Tippmixpro felhasználónév + jelszó | Saját Tippmixpro-fiók |
| 4 | Értesítő bot token | @BotFather (lásd lentebb) — **kihagyható** |
| 5 | Alap tét (Ft) | Saját döntés |

A beállítások elmentődnek — **következő indításkor már nem kell újra megadni.**

> A 4. lépés (értesítő bot) **nem kötelező**, de erősen ajánlott: enélkül nem
> kapsz értesítést, ha egy fogadás nem sikerül. A „Most kihagyom" gombbal
> átugorható, és később a `python main.py --setup` paranccsal pótolható.

---

## Értesítő bot létrehozása (sikertelen fogadás riasztása)

A program egy **saját Telegram-boton** keresztül értesít, ha egy fogadást nem
sikerül megrakni. Így **push-értesítést** kapsz a telefonod kijelzőjén — nem
csak akkor látod, ha megnyitod a Telegramot.

> **Miért bot, és miért nem a saját üzeneteid?** A Telegram a *saját magadnak*
> küldött üzenetekről (Saved Messages) nem ad push-értesítést. Egy külön bot
> viszont „bejövő" üzenetként ír neked → felugrik a riasztás a telefonon.

A bot létrehozása kb. 1 perc, **egyszer kell**:

1. Telegramban keresd meg: **@BotFather** (a kék pipás, hivatalos)
2. Küldd neki: **`/newbot`**
3. Adj a botnak egy **nevet** (bármi lehet, pl. „BetPlacer Riasztó")
4. Adj egy **felhasználónevet** — `bot`-ra kell végződnie (pl. `betplacer_riaszto_bot`)
5. A BotFather válaszában kapsz egy **tokent**, ilyesmit:
   `123456789:AAH4d…xYz` — **ezt másold ki**
6. A beállítási varázsló **4. lépésénél** illeszd be ezt a tokent
7. **Fontos:** nyisd meg a most létrehozott saját botodat a Telegramban, és nyomj
   rá a **Start** gombra (vagy küldj neki egy `/start` üzenetet)
8. A varázslóban kattints az **„Összekapcsolás"** gombra — ekkor automatikusan
   megtalálja, hova küldje az értesítéseket, és **azonnal küld egy teszt-üzenetet**
   („✅ BetPlacer összekapcsolva"), amit látnod kell a Telegramodban / a telefonod
   kijelzőjén. Ha megjött, kész — működik a push.

Ha még nem nyomtál Start-ot, a varázsló szól, hogy tedd meg, majd kattints újra.

---

## Napi használat

Kattints duplán a **`run.bat`** fájlra. Megnyílik a BetPlacer ablak:

```
⚽ BetPlacer                        ● LEÁLLÍTVA
──────────────────────────────────────────────
Csatorna: (beállítva)  |  Tét: [ 500 ] Ft    [▶ Indítás]
──────────────────────────────────────────────
[14:32:01] BetPlacer kész. Kattints az Indítás gombra.
```

1. Szükség esetén **írd át a tét értékét** a „Tét" mezőben (Ft)
2. Kattints az **`▶ Indítás`** gombra
3. A program bejelentkezik Tippmixpro-ra (~15 mp)
4. Figyeli a csatornát — ha jön tipp, automatikusan megrakja

> **Tét a GUI-ban:** a fő ablak „Tét" mezőjében bármikor átírhatod a tétet —
> az Indításkor lép érvénybe, és **elmentődik** a következő indításhoz is.
> Futás közben a mező zárolva van; a tét módosításához állítsd le, írd át, és
> indítsd újra. (A `python main.py --stake 1000` indítási kapcsoló is működik.)

**Konzol színek:**
- 🟡 Arany — új tipp érkezett, várakozás
- 🟢 Zöld — `[BET_OK]` sikeres fogadás
- 🔴 Piros — `[BET_FAIL]` sikertelen fogadás
- Szürke — általános infó

---

## Alvó mód

Amíg a BetPlacer fut (Indítás után), a program **megakadályozza, hogy a gép
elaludjon** — így a fogadásokat éjjel is megrakja. A **kijelző közben
elsötétülhet** (áramot spórol), a rendszer viszont ébren marad.

Fontos tudni:
- A gép **valódi alvás közben nem tud scriptet futtatni** (a processzor áll) —
  ezt egyetlen program sem tudja megkerülni. Ezért tartjuk inkább ébren.
- A védelem csak **futás közben** él. Ha leállítod a BetPlacert, a gép újra
  normálisan elalhat.
- **Monitor lekapcsolása vagy képernyőkímélő nem gond** — a gép „agya" tovább
  dolgozik, a script fut. Csak a tényleges alvás állítaná meg.

### Laptop: fedél lecsukásakor is fusson

Laptopon a fedél lecsukása **alapból elaltatja a gépet** (és leállítja a
scriptet). Ezt a Windows külön kezeli, ezért egyszer át kell állítani:

**Vezérlőpultból (ajánlott):**
1. **Vezérlőpult** → **Hardver és hang** → **Energiagazdálkodási lehetőségek**
   *(vagy a Start melletti keresőbe írd: „fedél" / „lid")*
2. Bal oldalt: **„A fedél lecsukásának hatása"**
3. A **„A fedél lecsukásakor:"** sorban állítsd **„Nem történik semmi"**-re.
   Két oszlop van — **Akkumulátorról** és **Hálózatról**; legalább a
   **Hálózatról** oszlopot állítsd erre (legjobb mindkettőt)
4. **Módosítások mentése**

**Vagy egy paranccsal** (nyiss egy parancssort és illeszd be):
```bat
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```
*(A `0` = „nem történik semmi". `setac…` = töltőn, `setdc…` = akkun.)*

Ezután lecsukott fedéllel: a kijelző lekapcsol, de a **rendszer ébren marad és a
script fut tovább.**

> **Tippek:** tartsd **töltőn**, és tudd, hogy lecsukva a laptop kissé
> **melegebb** lehet (szűkebb szellőzés). Hosszú, éjszakai futáshoz jobb nyitott
> fedéllel (a kijelző úgyis lekapcsol).

- 0–24 órás, gépfüggetlen működéshez egy mindig bekapcsolt szerver (VPS) a
  legbiztosabb — ez nagyobb átalakítás, kérésre megoldható.

---

## Parancssori opciók

```bash
run.bat                      ← ajánlott indítás (GUI ablakkal)
python main.py               ← ugyanaz
python main.py --stake 1000  ← más tét erre a futásra
python main.py --dry-run     ← teszt mód (nem fogad élesben)
python main.py --setup       ← beállítások újra (wizard)
python main.py --no-gui      ← csak parancssor, ablak nélkül
```

---

## Hogyan működik?

```
Telegram-csatorna
      ↓ új tipp üzenet
  BetPlacer értelmezi
      ↓ (meccs, pick, sor, odds)
  30–120 mp véletlenszerű várakozás
      ↓
  Megnyitja a Tippmixpro oldalt
      ↓ bejelentkezik (automatikusan)
  Megkeresi a meccset
      ↓ kitölti a szelvényt
  Feladja a fogadást
      ↓
  [BET_OK] / [BET_FAIL] az ablakban
```

---

## Hibaelhárítás

| Hiba | Megoldás |
|------|----------|
| `HIBA: Hiányos .env konfiguráció` | Futtasd: `python main.py --setup` |
| `Bejelentkezés sikertelen` | Ellenőrizd a Tippmixpro felhasználónevet/jelszót |
| `esemény nem található` | A meccs valószínűleg már elindult vagy nem elérhető |
| Playwright hiba | Futtasd újra: `python -m playwright install chromium` |
| GUI nem nyílik meg | Futtasd: `python main.py --no-gui` |
| Nem jön bot-értesítés | Nyomtál Start-ot a botnál? Állítsd be újra: `python main.py --setup` |
| `Érvénytelen token` | Másold be újra a @BotFather-től kapott teljes tokent |

---

## Fájlstruktúra

```
standalone_betplacer/
├── main.py              ← belépési pont
├── gui.py               ← GUI ablak (konzol, Start/Stop gomb)
├── setup_wizard.py      ← beállítási varázsló (első indításkor)
├── telegram_watcher.py  ← Telegram figyelő
├── tip_parser.py        ← üzenet értelmező
├── notifier.py          ← értesítő bot (sikertelen fogadás → push)
├── bet_engine.py        ← Tippmixpro fogadás (Playwright)
├── requirements.txt     ← Python csomagok listája
├── install.bat          ← telepítő (egyszer kell futtatni)
├── run.bat              ← napi indítás
├── .env                 ← beállítások (automatikusan jön létre)
├── telegram_session.session  ← Telegram munkamenet (automatikusan)
└── logs/                ← képernyőmentések hibakereséshez
```

---

*A `telegram_session.session` és `.env` fájlokat ne add ki senkinek — ezek személyes belépési adatokat tartalmaznak.*
