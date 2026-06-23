"""Remote — mobilbarát web-vezérlő a Standalone BetPlacerhez.

A futó BetPlacer egy háttérszálán indul (lásd remote/server.py). Csak két dolgot
tud: élő napló megtekintése telefonról, és a fogadó-session ÚJRAINDÍTÁSA. Nem
indít külön folyamatot, a meglévő GUI start/stop logikáját hívja (Qt signalon át).
"""
