# GSPro Tracer – Verlauf und Stand (12.–19.11.2026)

Zusammenfassung von allem, was in der ersten großen Arbeitssitzung gebaut, entschieden und herausgefunden wurde.
Für Claude: zusammen mit `CLAUDE.md` (Kurzfassung/Arbeitsregeln) lesen. Für die Bedienung: `README.md`.

---

## 1. Was der Tracer heute kann

**Aufnahme**
- Garmin R10 → MLM2PRO-GSPro-Connector → **Tracer-Proxy** (Port 9211) → GSPro OpenAPI (Port 921).
  Der Proxy reicht alles unverändert weiter und erkennt dabei jeden Schuss.
- Bei jedem Schuss triggert der PC **zwei Poco X3** (App in `android/`, aktuell **Build 13**, Release `cam-latest`):
  Ringpuffer 12 s mit **240 fps / 720p**, Clip **6 s vor bis 0,5 s nach** dem R10-Signal.
- Rollen per Knopf in der App: **„Kamera: hinten“** (Down-the-Line, Linien/Analyse/Export) und **„Kamera: seitlich“**
  (90°, Face-On). Upload: `clip.mp4` / `clip_side.mp4` im selben Schuss-Ordner `pc\shots\<id>\`.
- **Fokus fest auf den Ball**: am Handy ins Bild tippen oder auf der Live-Seite „🎯 Fokus auf Ball“ + Klick. Wird
  gespeichert und nach Neustart wieder angewendet. (Früher: Dauer-Autofokus suchte bei Bewegung nach; „sperren“ schaltete
  AF ganz aus → Objektiv sprang evtl. auf unendlich – behoben.)
- **Live-Seite** `/live`: Bild je Kamera (Umschalter hinten/seitlich), Raster, Markierungen je Kamera.

**Verarbeitung am PC**
- **Videolage** verlustfrei (nur Drehvermerk, kein Neukodieren): hinten `quer_180` (Handy quer + kopfüber),
  seitlich „wie aufgenommen“. Einstellbar unter Analyse-Seite → Einstellungen → Videolage.
- **Analyse** (`pc/swing_analysis.py`, OpenCV): Treffmoment (Ball verschwindet), Ansprechen (letzter ruhiger Moment vor
  dem Rückschwung, inkl. langsamem Start über 0,1 s), Schaftlinie beim **Ansprechen** und kurz vor dem **Treffmoment**,
  **Rechts-/Linkshänder** (Bewegung links/rechts vom Ball). Seitenkamera: nur „dünne Linien“ (Ridge-Test gegen Bein-/
  Wand-Kanten), Treffmoment-Linie nur wenn das Segment am Ball endet (sonst lieber keine).
- **Synchronisierung** der Seitenkamera: grob über das Startsignal, fein über den Treffmoment in beiden Videos (auf 1 Bild),
  von Hand ±1 Bild auf der Analyse-Seite (`sync.json`).
- **Nur vollständige Schüsse** (Treffmoment + Ansprech- + Schlaglinie) bleiben; andere → `pc\verworfen\` (letzte 30, mit Grund).
- **Export-Video** `pc\exports\<Datum>_<Schläger>_<mph>.mp4`: **beide Kameras nebeneinander**, Schwarzweiß, aufgehellt
  (Tonwertkurve + leichtes Entrauschen), Zeitlupe 1/8, zugeschnitten (0,15 s vor dem Rückschwung bis 0,6 s nach dem Treffen),
  Datenpanel (quer: oben links) mit **mph**, **Carry vom Garmin in m**, Spin, Winkeln, Kopfzeile mit Schläger und
  Rechts-/Linkshänder.
- **Linien**: blau Ansprechen, orange Treffmoment, **halb so lang** wie bis zum Bildrand, **ohne Gradzahl** – am Treffmoment
  nur der **Versatz zum Ansprechen** („Treffmoment −3°“), weil die absoluten Winkel von der Kameraneigung abhängen.
  In beiden Videos von Hand ziehbar (ersetzt die automatische, Video wird neu erstellt).

**Analyse-Seite** `http://localhost:8090`
- Liste links (mph, VLA, **Carry m**), beide Videos synchron nebeneinander (Seitenvideo schmal, passt auf 16:10 ohne Scrollen),
  Zeitlupe bis 1/16, Bild für Bild, Schlagdaten in mph, Schlaghand mit ⚠ bei Abweichung von GSPro, CSV-Export,
  „Alle Schüsse löschen“ (Exporte bleiben), Einstellungen (Vorlauf, Videolage, R10-Verzögerung).

**Schwung-Check** `http://localhost:8090/schwung` (Knopf „🏌 Schwung-Check“)
- Körperpunkte (MoveNet Thunder, TensorFlow.js) **auf der Grafikkarte** im Browser (WebGL), offline (`pc\static\`).
- Schuss wählen → hinten und seitlich einzeln analysiert; Ansprechen/Treffmoment aus der Tracer-Analyse, „Oben“ = Hände am
  höchsten; Skelett über dem Video, gestrichelt die Haltung beim Ansprechen.
- Messwerte mit Ampel + Tipp je Schlägerart (Driver/Holz/Hybrid/Eisen/Wedge): hinten Rückenwinkel, Aufrichten, Hüfte zum
  Ball, Kopfhöhe, Knie; seitlich Standbreite, Schulterneigung, Kopf im Rückschwung, Hüfte/Kopf zum Ziel. Verdeckte Punkte →
  „n. m.“. 2D-Richtwerte, nicht validiert.
- Eine frühere Version als claude.ai-Artefakt lief beim Nutzer nicht (veraltet:
  <https://claude.ai/artifact/XcFQShjGLwsZquAwvmjS1Y>); Quelltext zur Ansicht in `docs/artefakt-schwungcheck.html`.

**Robustheit / Diagnose**
- Beim Start im Fenster: `Stand …` (zeigt, ob `git pull` geklappt hat) und alle wichtigen Einstellungen.
- `pc\config.json` ist **nicht im Repo** (gehört dem Nutzer, `git pull` scheitert nie mehr daran); fehlende Werte werden ergänzt,
  Tippfehler (Kommas) repariert, neue Standards per Migration (`settings_version`, aktuell 6).
- Alte Export-Videos werden bei geändertem Aussehen automatisch neu erstellt (`EXPORT_VERSION`), gespeicherte Schüsse bei
  verbesserter Erkennung einmal neu analysiert (`ANALYSIS_VERSION`, Handlinien bleiben).
- Proxy-Log: wer die Verbindung trennt, Nachrichtenzähler, „GSPro antwortet seit 8 s nicht“ + **Diagnose aller Verbindungen
  auf Port 921** mit Programmnamen (auch deutsche netstat-Ausgabe). Doppelt gesendete Schüsse → nur ein Video.
- Export mit niedriger Priorität, Wartezeit 8 s nach dem Schlag (einmal pro Schlag), 4 Kerne, x264 `veryfast`;
  Grafikkarten-Export (NVENC/Quick Sync/AMF) wird automatisch getestet, mit Grund im Log, Rückfall auf Prozessor.
- Windows-Dateisperren (Virenscanner) werden abgefangen.

---

## 2. Wichtige Erkenntnisse

- **Connector bricht ab / R10 blinkt rot**: Ursache war **GSPro**, nicht der Tracer – GSPro hat nicht geantwortet
  (`GSPro->Connector 0`). Der Connector wiederholt den Schuss nach ~2 s und gibt dann auf („GSPro Send Error“), der R10 wird
  nicht wieder freigegeben. Abhilfe: **GSPro API Connect neu starten**, Reihenfolge GSPro → Tracer → Connector. Einmal hat GSPro
  erst nach 9 s geantwortet.
- **Videos kamen falsch herum/hochkant**: Kombination aus App-Ausrichtung und Handy-Lage; gelöst über die verlustfreie Drehung am PC.
- **`git pull` scheiterte tagelang still** an der lokal geänderten `config.json` → deshalb liefen alte Stände. Seit 15.11. behoben.
- **Ansprechlinie falsch** beim Rechtshänder-Schlag: Ansprechen wurde mitten im Rückschwung erkannt (langsamer Start) – behoben.
- **Seitlich beim Treffen** ist der Schaft bei wenig Licht nur ein verwischter Streifen (lange Belichtung) → mehr Licht von vorne
  würde die automatische Treffmoment-Linie seitlich ermöglichen.
- Die Analyse ist **spiegelsymmetrisch** (gespiegelter Clip → gleiche Winkel); Rechtshänder brauchen nur die Kameras auf der
  anderen Seite.
- GSPro-Spielerprofil stand beim Rechtshänder-Schlag noch auf „LH“ – Bild-Erkennung ist zuverlässiger.

---

## 3. Aufbau beim Nutzer

- Windows-PC `C:\GSProTracer`, Python 3.14, OpenCV 5.0, FFmpeg 9.0.2 (winget Gyan), **RTX 3060 Ti**, Monitor 16:10.
- Spieler meist **Linkshänder**, seit 18.11. auch ein **Rechtshänder**.
- Kamera hinten: quer, kopfüber (`quer_180`). Kamera seitlich: hochkant.
- Updates: `git pull` in `C:\GSProTracer`, danach Tracer neu starten; App über den Update-Knopf.

---

## 4. Offene Punkte

1. **Export dauert beim Nutzer > 1 min** (hier ~6 s Analyse + ~16 s Video + 8 s Wartezeit). Log-Zeilen mit Uhrzeiten fehlen.
   Größter Hebel: **NVENC** auf der 3060 Ti – Log-Zeile „Grafikkarte NVIDIA NVENC nicht nutzbar: …“ steht aus (Verdacht:
   Treiber zu alt für FFmpeg 9).
2. **Treffmoment-Linie seitlich** wird meist nicht erkannt (Bewegungsunschärfe) – mehr Licht oder von Hand ziehen.
3. **Schwung-Check**: Richtwerte/Tipps sind Faustwerte; an mehr echten Schlägen prüfen. Auf der GPU des Nutzers noch nicht
   gemessen (erwartet 10–20 s pro Schuss).
4. **Carry**: Connector schickte bisher immer 0 – prüfen, ob er je einen Wert liefert.
5. **Schlägerkopf-Bahn** (Ausholen/Abschwung) wurde auf Wunsch entfernt – „ein anderes Mal“ neu angehen.

---

## 5. Zeitleiste (Commits)

| Datum | Änderung |
|---|---|
| 12.11. | Grundsystem: Proxy, Handy-Trigger, Ringpuffer 240 fps, Upload, Live-Bild, Export mit km/h, 120-fps-Modus, Schaftlinien automatisch und von Hand, README, Beispiel-Schlag + Test |
| 13.11. | Querformat, Aufhellung, Qualität ~7 MB, Export bremst GSPro nicht, Proxy-Diagnose, „Alle Schüsse löschen“, Doppel-Schüsse, verlustfreie Drehung, `config.json`-Reparatur, Videolage 180°, Schlägerkopf-Bahn entfernt, 6 s Vorlauf, nur vollständige Schüsse, Schwarzweiß |
| 15.11. | Stand-Anzeige, `config.json` aus dem Repo, Video-Vorlauf einstellbar, GSPro-Diagnose Port 921, Grafikkarten-Export mit Fehlergrund, verworfene Schüsse nach `pc\verworfen` |
| 17.11. | Zwei Kameras synchron (App-Rolle, `clip_side.mp4`, Sync + Korrektur ±1 Bild), Fokus fest auf Ball, Linien seitlich, mph + Carry, Layout 16:10, Export beide Kameras nebeneinander, schneller (Wartezeit einmal, Sprünge statt Durchlesen, OpenCV-Linien, `veryfast`), `CLAUDE.md` |
| 19.11. | Rechts-/Linkshänder-Erkennung, genauere Ansprechposition, Rechtshänder-Beispiel in `samples/`, Linien halb so lang ohne Gradzahl (nur Versatz), **Schwung-Check auf der Grafikkarte** (`/schwung`) |

---

## 6. Wichtige Dateien

| Datei | Inhalt |
|---|---|
| `pc/gsprotracer.py` | Proxy, Handy-Steuerung, Upload, Webserver, Konfiguration + Migrationen, Diagnose |
| `pc/exporter.py` | Analyse-Ablauf, Synchronisierung, Export-Video (Linien, Panel, beide Kameras), Grafikkarten-Encoder |
| `pc/swing_analysis.py` | OpenCV-Analyse: Treffmoment, Ansprechen, Schaftlinien, Schlaghand |
| `pc/viewer.html` / `pc/live.html` / `pc/schwung.html` | Analyse-Seite / Kamera ausrichten / Schwung-Check |
| `pc/static/` | TensorFlow.js + MoveNet-Thunder-Modell (offline) |
| `pc/test_sample.py` | Test gegen `samples/*/reference.json` (Linkshänder 12.11. + Rechtshänder 18.11.) |
| `android/…/HighSpeedRecorder.kt` | Kamera, Ringpuffer, Clips, Fokus |
| `android/…/MainActivity.kt`, `PcLink.kt` | App-Oberfläche, Rolle, Verbindung zum PC |
| `CLAUDE.md` | Kurzkontext und Arbeitsregeln für Claude |
