# Beispieldaten

Ein echter Schlag zum Testen und Weiterentwickeln, ohne Handy, R10 oder GSPro.

**`20261112-132941-01/`**: Holz 3 (W3), Linkshänder, Kamera hochkant hinter dem Spieler (Down-the-Line, recht nah)

| Datei | Inhalt |
|---|---|
| `clip.mp4` | Original vom Poco X3: 720×1280, **240 fps**, 4,5 s, H.264, ca. 22 MB |
| `shot.json` | Daten vom R10/Connector (Ball 83,6 mph ≈ 134,5 km/h, Schläger 76,6 mph) + Schläger aus GSPro |
| `video.json` | Metadaten vom Handy (fps, Größe, Zeitpunkt des R10-Triggers im Clip) |
| `reference.json` | **Von Hand gesetzte Referenz:** Treffmoment 3,232 s, Schaft beim Ansprechen (Bild 1,50 s) und im Abschwung (Bild 3,215 s) |

**`export_beispiel_W3_135kmh.mp4`**: Export-Video vom 12.11. (Zeitlupe 1/8, Daten in km/h, Linien; noch ohne Aufhellung und Schwungbahn, stark komprimiert).

## Benutzen

Automatische Analyse gegen die Referenz prüfen:

```bash
python pc\test_sample.py
```

In der Analyse-Seite ansehen: den Ordner `20261112-132941-01` nach `pc\shots\` kopieren (ohne `reference.json`)
und den Tracer starten. Beim Start werden Analyse und Export-Video automatisch erzeugt.

Koordinaten in `reference.json` sind normiert (0–1, bezogen auf 720×1280). Punkt 1 ist der Schlägerkopf, Punkt 2
liegt weiter oben am Schaft. `from` ist die Sekunde im Clip, ab der die Linie gezeigt wird.
