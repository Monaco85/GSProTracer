# GSPro Tracer – Kontext für Claude

Projekt von Sebastian (Repo `Monaco85/GSProTracer`). Kommunikation **auf Deutsch**, kurz und praktisch –
er ist Golfer, kein Entwickler. Befehle für ihn immer als PowerShell/`git` in eigenem Code-Block.

## Was es macht
Garmin R10 → MLM2PRO-GSPro-Connector → **Tracer-Proxy** (`pc/gsprotracer.py`, Port 9211) → GSPro OpenAPI (921).
Bei jedem Schuss triggert der PC zwei Handys (Poco X3, App in `android/`), die rückwirkend aus einem
12-s-Ringpuffer (240 fps) einen Clip schneiden und hochladen. PC: Analyse (OpenCV), Export-Video (FFmpeg),
Analyse-Seite im Browser (`pc/viewer.html`), Live-Seite zum Ausrichten (`pc/live.html`).

## Aufbau beim Nutzer (Stand 17.11.2026)
- PC: Windows, Programm in `C:\GSProTracer`, Python 3.14, OpenCV 5.0, FFmpeg 9.0.2 (Gyan, winget),
  **RTX 3060 Ti** (NVENC wird bisher NICHT genutzt – Grund unbekannt, Log-Zeile „Grafikkarte NVIDIA NVENC nicht
  nutzbar: …“ steht noch aus; Verdacht: Treiber zu alt für FFmpeg 9).
- Monitor 16:10, Browser ca. 1920×1080 sichtbar – Analyse-Seite muss ohne Scrollen passen.
- Spieler meist **Linkshänder**; seit 18.11. schlägt auch ein **Rechtshänder** (Kameras dann auf der anderen Seite).
  Schlaghand wird erkannt (`detect_handedness`, Bewegung links/rechts vom Ball), Analyse ist spiegelsymmetrisch
  (gespiegelter Beispiel-Clip liefert dieselben Winkel).
- **Kamera hinten** (Down-the-Line): Handy liegt **quer und kopfüber** → `clip_orientation: "quer_180"`.
- **Kamera seitlich** (90°, Face-On): Video kommt **hochkant** an (`clip_orientation_side: ""`).
- App-Rolle per Knopf „Kamera: hinten/seitlich“ (ab Build 11). Neueste App: **Build 13** (Fokus fest auf Ball).
- Er holt Updates per `git pull` in `C:\GSProTracer`; `pc/config.json` ist NICHT im Repo (gehört ihm).

## Arbeitsweise in diesem Repo
- Entwicklungs-Branch `claude/exciting-goodall-ea4bel`; Änderungen gehen **zusätzlich auf `main`**
  (er hat dem ausdrücklich zugestimmt – er zieht von `main`). App-Release `cam-latest` baut die CI nur aus `main`.
- Kotlin kann lokal nicht kompiliert werden → nach App-Änderungen CI-Lauf prüfen (GitHub-Actions-MCP).
- Neue Standardwerte für bestehende Installationen: **Migration in `Config.__init__`** über `settings_version`
  (aktuell 6) bzw. `orientation_version` – seine `config.json` überschreibt sonst die Defaults.
- `VERSION` in `gsprotracer.py` hochzählen (steht beim Start im Fenster – so sieht man, ob `git pull` geklappt hat).
- Aussehen der Export-Videos geändert → `EXPORT_VERSION` in `exporter.py` erhöhen (alte Videos werden neu erstellt).
- Tests: `python pc/test_sample.py` (Beispiel-Schlag in `samples/`, hochkant, alte Kameraposition).
  End-to-End: Server-Kopie im Scratchpad mit anderen Ports, Uploads per `curl` auf `/api/upload?...&cam=`,
  Browser-Prüfung mit Playwright. Kein echter Clip seiner aktuellen Aufbauten (quer/kopfüber, seitlich) vorhanden.

## Wichtige Entscheidungen / Wünsche
- Export: Schwarzweiß, aufgehellt (Tonwertkurve), zugeschnitten (0,15 s vor dem Rückschwung bis 0,6 s nach
  Treffen), Zeitlupe 1/8, **beide Kameras nebeneinander** (Seitenkamera auf gleiche Höhe), Daten in **mph**,
  **Carry vom Garmin in m** (`BallData.CarryDistance`, Units Yards; im alten Beispiel 0).
- Linien: nur **Ansprechen** und **Abschwung/Treffmoment** (Schlägerkopf-Bahn wurde auf Wunsch entfernt –
  „ein anderes Mal“). Seitlich zusätzlich Veränderung ggü. Ansprechen; seitlich nur „dünne Linien“ (Ridge-Test).
  Linien in beiden Videos von Hand korrigierbar.
- Nur vollständig erkannte Schüsse behalten (`only_complete_shots`), Verworfene nach `pc/verworfen/`.
- Aufnahme 6 s vor dem R10-Trigger; Synchronisierung: Startsignal, dann Treffmoment, von Hand ±1 Bild.

## Offene Punkte
1. **Export dauert bei ihm > 1 min** (hier ~6 s Analyse + ~16 s Video + 8 s Wartezeit). Log-Zeilen mit Uhrzeiten
   von ihm fehlen noch; größter Hebel: NVENC auf der 3060 Ti zum Laufen bringen.
2. **Ansprechlinie seitlich** an echtem Face-On-Clip prüfen (er soll einen Schuss nach `samples/` pushen).
3. **Ansprechlinie hinten** wurde bei seinem neuen Aufbau anfangs nicht erkannt → ebenfalls echter Clip nötig.
4. **Connector-Abbrüche / R10 blinkt rot**: Ursache war, dass GSPro nicht antwortete (`GSPro->Connector 0`);
   Lösung: GSPro API Connect neu starten. Tracer loggt das inzwischen mit Diagnose (Port 921).
5. Carry: prüfen, ob der Connector überhaupt einen Wert liefert.
