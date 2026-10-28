# Standalone BetPlacer — dashboard átalakítás: funkció-terv

Egyeztetve: 2026-10-27. Állapot: **funkciók rögzítve, nincs nyitott funkció-kérdés;
design még nincs** (sablon-választás és elrendezés a következő lépés). Kódot még nem írtunk.

## Alapelv

A **számla az igazság forrása**. A böngészőben látott eredmény (szelvény kiürült,
nyugta-ID) csak gyors első jelzés; a végleges állapotot a fiók adja.
Csak a program **futása alatti** időszakot vizsgáljuk (nincs csatorna-visszaolvasás).

## 1. Számla-szinkron (a mostani napi egyszeri helyett)

Három alkalommal fut, külön időzítő nincs:

1. **Induláskor** — teljes kép: egyenleg, nyitott + lezárt fogadások.
2. **Megrakás után ~1–2 perccel** — a tényleges megrakástól számítva (nem a tipp
   beérkezésétől; a 30–120 mp-es véletlen késleltetés előtte van) → rajta van-e a számlán.
3. **A várható elszámolás után** — 8p: kezdés + 15 perc, 12p: kezdés + 20 perc.
   Ha még nyitott: 10 percenként újra; 1 óra után ⚠️ „nem számolták el”.
   Egy lekérés minden esedékes tippet lefed.

- A már belépett böngészőben fut (nincs második belépés), fogadás közben nem.
- **A szinkron saját hibáját is jelzi**: látszik az utolsó sikeres szinkron ideje.
  Ha a számla nem kérdezhető le, **3-szor újrapróbálja** (javaslat: 2–3 perces
  szünettel), és csak utána → piros jelzés + Telegram. (2026-10-21…27: a Vegas-lekérés csendben
  elromlott, napokig régi gyorsítótárból dolgozott → téves „13 eltérés”.)

## 2. Háromirányú összevetés

*csatornába kiküldött tipp ↔ BetPlacer művelete ↔ számla*

Minden **felismert** tipp tartós naplóba kerül a végső állapotával (most csak a
sikeres megrakás naplózódik).

| Állapot | Jelentés |
|---|---|
| ✔ Rendben | tipp → megrakva → a számlán van |
| ❌ Sikertelen megrakás | megpróbálta, nem ment (okkal) — MÁR LÉTEZIK |
| ⚠️ Szándékosan kihagyva | pl. elmozdult a vonal — vonal-őr MÁR LÉTEZIK |
| ⚠️ Kimaradt tipp | futás közben felismerte, de nem jutott végső állapotba |
| ⚠️ Megrakottnak jelölt, de nincs a számlán | képernyő szerint siker, számla szerint nem (dashboardon „nincs megerősítve”, reggel összesítve) |
| ⚠️ A számlán van, de nincs hozzá tipp | kézi vagy dupla fogadás |
| ⚠️ Eltérő adat | vonal / irány / tét / odds más a számlán |

Párosítás: játékosnév + vonal + irány (az event-ID csatorna és iroda között nem egyezik).

**Folyamatban lévő tipp NEM eltérés.** Amíg a BetPlacer egy tippet még próbál megrakni
(várakozás, esemény-újrapróbálás, késői újrapróbálás), a szinkron nem jelzi „nincs a
számlán”-nak. Eltérés csak **végső állapotú** tippre jöhet: sikeres megrakás után a
megerősítési határidő lejártával, vagy végleges feladás / kihagyás után.

## 3. Értesítések

- Telegramon **és** a dashboardon is.
- **✅ csak számla-megerősítés után** megy ki (pár perc késés belefér — a valós
  eredményt közölje). Tipp beérkezése → ✅ kb. 2–4 perc.
- Ha a megrakott fogadás nem jelenik meg a számlán: **nincs azonnali riasztás** — a
  dashboardon „nincs megerősítve” állapotban látszik, az eltérés a **reggeli
  összesítőben** jelenik meg (naponta vizsgálódunk). Újramegrakás SOSEM (dupla fogadás).
- Tipster-eredménnyel való összevetés: NEM kell (később hozzáadható).

## 4. Fiók-adatok

- **Account-név és egyenleg** mindkét irodánál, irodánként külön.
- Egyenleg-pillanatképek → egyenleg-görbe.

## 5. Számla-statisztika (dashboard)

- **Csak a BetPlacer indulása utáni** számok.
- **Napi határ = Battle-nap**, nem naptári nap (javaslat, ld. fő dashboard
  `core/battle_day.py`): éjszakai liga + 07:00 előtti kezdés → előző nap; minden más →
  a kezdés napja. A liga a tipp-üzenetben van. (Éjszaka az utolsó meccs 06:38-kor,
  nappal az első 06:30-kor kezdődik → kis átfedés, ezt a liga dönti el.)
- Mai / heti / havi / összes: fogadásszám, nyert–vesztett, tét, profit, ROI.
- Irodánként és stratégiánként; párosítás nélküli tétel = „kézi”.

## 6. Naplózás

- Első induláskor a teljes számlatörténet letöltése, utána folyamatos hozzáírás.
- **Semmit nem töröl**, korlátlan megőrzés, irodánként külön.
- A `placed_bets.jsonl` sem töröl többé (most 30 nap) — a régi fogadások
  stratégia-hozzárendelése így megmarad.
- A dashboardon ebből csak az indulás utáni rész látszik.

## 7. Tétajánlás (bankroll-menedzsment)

- **Bankroll = egyenleg + nyitott fogadások tétje**, irodánként külön.
- **Tét = bankroll 2%-a, minden stratégiánál egységesen**, 100 Ft-ra kerekítve.
- A forrásonkénti (stratégiánkénti) külön tét megszűnik → irodánként egy tét.
- Emelés/csökkentés javaslatakor értesítés; **jóváhagyás után** állítja át, magától nem.
- **A tét 500 Ft-os lépésekben változik** (ez a legkisebb emelés/csökkentés):
  változtatást **csak akkor javasol, ha a 2% legalább 500 Ft-tal eltér** a mostani
  téttől — és jóváhagyást kér.
- **Felülvizsgálat naponta egyszer**, a reggeli Telegram-összesítővel együtt (a
  TippmixPro + Vegas számla-szinkron napi teljesítmény-üzenete). A tétajánlás ebbe az
  üzenetbe kerül. → a reggeli napi összesítő MARAD.
- **Minimum tét:** ha a 2% az iroda minimum tétje alá esne, a minimumot rakja; ez a
  reggeli összesítőben jelenik meg (külön riasztás nincs).

## 8. Telegram-összekötés (egyszerűsítés)

- A standalone felülete **gyakorlatilag teljesen átalakul**.
- A csatorna-ID-ket **mi adjuk meg** (beégetve) — a felhasználó nem állítja, és nem
  is feltétlenül kell látnia őket. Elég egy állapotjelzés (pl. „Telegram: kapcsolódva,
  2 csatorna figyelve”).
- Ami továbbra is a felhasználóé (egyszeri beállítás, megerősítve):
  - a saját Telegram-belépése (ezzel figyeli a csatornákat),
  - az értesítő bothoz kapcsolódás (hogy tudja, kinek küldjön),
  - az irodák belépési adatai (TippmixPro, Vegas).

## Ami változatlanul marad (a mostani standalone-ból)

Telegram-csatorna figyelés (saját fiók), tipp-felismerés + dedup, 30–120 mp véletlen
késleltetés (szoros kezdésnél rövidebb), esemény-újrapróbálás, vonal-őr, szelvény-őr
(TMX), Vegas felugró ablakok + szabályzat-elfogadás, dupla fogadás elleni védelem,
értesítő bot, **Remote (élő napló telefonon) — MARAD**, alvásgátlás, beállító varázsló,
önfrissítés, dry-run.

**Real Event: NEM kerül az új felületre** (egyelőre; ha élesedik, visszatesszük).

## Következő lépés

1. Sablon kiválasztása.
2. Dashboard elrendezés: mi hova kerül.
