# GSPro Tracer

Schwungvideos mit **240 fps** vom Handy (Poco X3) – automatisch zu jedem Schlag, den der **Garmin R10** an
**GSPro** meldet. Am PC: Zeitlupen-Analyse im Browser, fertige Videos mit eingeblendeten Schlagdaten in **km/h**
und automatisch eingezeichneten **Schaftlinien** (Ansprechen / Abschwung) sowie der **Schwungbahn**
(Ausholen / Abschwung).

```
Garmin R10 ──BT──► MLM2PRO-GSPro-Connector ──► Tracer-Proxy :9211 ──► GSPro OpenAPI :921
                                                     │ erkennt den Schuss
                                                     ▼
                     Poco X3 (App)  ◄── Trigger über WLAN (Port 9300)
                     Ringpuffer 240 fps → schneidet Clip → Upload (HTTP :8090)
                                                     ▼
          PC: pc\shots\…\clip.mp4 + Schlagdaten → Analyse-Seite + pc\exports\…mp4 (mit Daten & Linien)
```

**Warum ein Ringpuffer?** Der R10 meldet die Werte erst **nach** dem Treffmoment (bei uns ca. 0,8 s später).
Ein Video, das erst dann startet, würde den Schwung verpassen. Das Handy nimmt deshalb ununterbrochen auf,
hält die letzten 12 s im Speicher und schneidet beim Trigger rückwirkend den passenden Ausschnitt heraus.
Nur dieser Clip wird an den PC geschickt und danach auf dem Handy gelöscht.

---

## Inhalt

1. [Funktionen](#funktionen)
2. [Voraussetzungen](#voraussetzungen)
3. [PC einrichten](#1-pc-einrichten)
4. [App aufs Handy bringen und updaten](#2-app-aufs-handy-bringen-und-updaten)
5. [Benutzung](#3-benutzung)
6. [Videos mit Schlagdaten und Linien](#4-videos-mit-schlagdaten-und-linien)
7. [Einstellungen (`pc\config.json`)](#5-einstellungen-pcconfigjson)
8. [Kamera-Aufbau, Licht, Weitwinkel](#6-kamera-aufbau-licht-weitwinkel)
9. [Fehlersuche](#7-fehlersuche)
10. [Für Entwickler: Aufbau, Build, Signatur](#8-für-entwickler)

---

## Funktionen

**Handy-App (Android, getestet auf Poco X3 NFC / M2007J20CG)**
- Dauerhafte Aufnahme im Highspeed-Modus: **720p mit 240 fps** (alternativ 1080p mit 120 fps)
- Clip pro Schlag, ausgelöst vom PC, automatischer Upload; nicht hochgeladene Clips werden nachgeschickt
- **Quer** (Standard) oder hochkant, Video-Drehung für kopfüber montierte Handys
- Belichtung/Fokus sperren, Display dimmen (weniger Wärme), Akku-Temperatur-Anzeige
- Live-Bild (~5 fps) für den PC zum Ausrichten der Kamera
- **In-App-Update** aus dem GitHub-Release `cam-latest`

**PC (Windows, Python)**
- Transparenter Proxy zwischen Connector und GSPro – GSPro merkt davon nichts
- **Analyse-Seite** <http://localhost:8090>: Zeitlupe bis 1/16, Bild für Bild, Ball-/Schlägerdaten in km/h,
  Smash, Face-to-Path, Notizen, Durchschnitt pro Schläger, CSV-Export (Excel)
- **Kamera ausrichten** <http://localhost:8090/live>: Live-Bild mit Raster und Markierungen
- **Automatischer Export** nach `pc\exports\`: Zeitlupe 1/8 mit Datenpanel, aufgehellt, ca. 7 MB
- **Automatische Analyse** (OpenCV): Treffmoment, Ansprechen, **Schaftlinien** beim Ansprechen und im Abschwung,
  **Schwungbahn** des Schlägerkopfs beim Ausholen und im Abschwung
  inkl. Winkel; Linien lassen sich auch von Hand zeichnen

---

## Voraussetzungen

| Was | Wofür | Installation |
|---|---|---|
| GSPro + OpenAPI (APIv1 Connect) | Simulator | – |
| MLM2PRO-GSPro-Connector (getestet V1.04.20, R10 per Bluetooth) | R10 → GSPro | – |
| Python 3.10+ | PC-Programm | <https://python.org> |
| FFmpeg | Videos mit Schlagdaten | `winget install Gyan.FFmpeg` |
| OpenCV + NumPy (optional) | automatische Linien | `pip install opencv-python-headless numpy` |
| Android-Handy mit Highspeed-Video | Kamera | App aus den Releases |

PC und Handy müssen im **selben WLAN** sein.

---

## 1. PC einrichten

1. Repository herunterladen (*Code → Download ZIP*) oder klonen, z. B. nach `C:\GSProTracer`.
2. `pc\start_tracer.bat` doppelklicken.
   Beim ersten Start fragt die Windows-Firewall → **Private Netzwerke erlauben** (das Handy braucht Port 9300 und 8090).
   Im Fenster steht die **PC-IP**, die du in der App einträgst.
3. **Connector auf den Proxy umstellen** – in der `settings.json` des Connectors
   (z. B. `C:\GSProV1\MLM2Pro-GSPro-Connector_V1_04_20\appdata\settings.json`):
   ```json
   "port": 9211,
   ```
   statt `921`. Zum Zurückstellen wieder `921` eintragen.
4. **Startreihenfolge:** GSPro (mit APIv1 Connect) → Tracer → Connector → App am Handy.
   Läuft der Tracer nicht, findet der Connector GSPro nicht.
5. Analyse öffnen: **<http://localhost:8090>** (nicht die Datei `viewer.html` direkt öffnen).

---

## 2. App aufs Handy bringen und updaten

- **Erste Installation:** auf GitHub unter **Releases → cam-latest** die APK am Handy herunterladen und öffnen.
  „Unbekannte Apps installieren“ erlauben (MIUI fragt evtl. zusätzlich nach).
- **Updates:** in der App **Update → Nach Update suchen**. Lädt den neuesten Build und startet die Installation.
  Ein GitHub-Token ist nur nötig, falls das Repository privat ist
  (Fine-grained Token, nur dieses Repo, Berechtigung *Contents: Read-only*).

---

## 3. Benutzung

### Handy-App

1. App starten, Kamera erlauben, **PC-IP** eintragen → **Verbinden**.
   Oben links: *PC verbunden* und z. B. *1280x720 @ 240 fps (High-Speed)*, *gemessen ~240 fps*.
2. Handy fest montieren (Stativ/Halterung) und mit <http://localhost:8090/live> ausrichten.
3. Schlagen. Bei jedem Schuss blinkt am Handy ein grüner Rahmen, wenige Sekunden später ist der Clip am PC.

| Knopf | Funktion |
|---|---|
| Verbinden | Verbindung zur eingetragenen PC-IP |
| Testclip | 3-s-Clip ohne R10 (zum Testen/Ausrichten) |
| Format: quer / hochkant | Ausrichtung der Aufnahme (Standard: quer) |
| Modus: 240 fps / 720p ↔ 120 fps / 1080p | 1080p kann einen größeren Bildausschnitt haben |
| Weitwinkel | nur sichtbar, wenn das Handy Zoom < 1× für Fremd-Apps anbietet |
| Video drehen | Zusatzdrehung, wenn das Handy kopfüber/seitlich montiert ist (sonst 0°) |
| Belichtung/Fokus sperren | verhindert Pumpen, wenn du ins Bild trittst |
| Display dimmen | spart Akku und Wärme |
| Update | In-App-Update von GitHub |

### Kamera ausrichten – <http://localhost:8090/live>

Live-Bild vom Handy (~5 fps), Drittel-Raster, Bildmitte, Klick setzt eine Markierung (bleibt gespeichert, damit
die Kamera später wieder gleich steht), Testclip-Knopf und **Kamera-Diagnose** (was das Handy anbietet).
Das Handy sendet nur, solange die Seite offen ist.

### Analyse-Seite – <http://localhost:8090>

| Bedienung | Funktion |
|---|---|
| Leertaste | Play/Pause |
| ← / → bzw. `,` / `.` | 1 Bild (1/240 s) zurück/vor, mit Shift 10 Bilder |
| ↑ / ↓ | vorheriger/nächster Schlag |
| `I` | aktuelles Bild als Treffmoment markieren |
| Tempo 1× … 1/16 | Zeitlupe (1/8 ≈ jedes 240-fps-Bild einzeln) |
| Linie: Ansprechen / Abschwung / Hilfslinie + Maus ziehen | Linie vom Schlägerkopf den Schaft entlang ziehen; wird gespeichert und ins Export-Video übernommen |
| Linien ✕ | gespeicherte Linien des Schlags löschen |
| CSV-Export | alle Schläge für Excel (Semikolon, Komma, km/h) |
| Einstellungen | Clip-Länge und R10-Verzögerung (Kalibrierung) |

Rechts: Ball- und Schlägerdaten (km/h), Smash, Face-to-Path, Video-Infos, **Video mit Schlagdaten**
(Download / Neu erstellen), Notiz, Durchschnitt pro Schläger.

### Kalibrierung der R10-Verzögerung

Anfangs nimmt die App großzügig **4 s vor bis 0,5 s nach** dem Eintreffen der R10-Daten auf.
Der Treffmoment wird automatisch erkannt (oder per Taste `I` markiert). Nach einigen Schlägen:
**Einstellungen → Vorschlag übernehmen → Speichern**. Danach sind die Clips ca. **1,5 s vor bis 1,0 s nach**
dem Treffmoment. Gilt sofort, ohne Neustart.

---

## 4. Videos mit Schlagdaten und Linien

Sobald ein Clip ankommt, erzeugt der PC automatisch `pc\exports\<Datum>_<Zeit>_<Schläger>_<Ballspeed>kmh.mp4`:

- **Zeitlupe 1/8** (jedes 240-fps-Bild sichtbar), Format wie aufgenommen (quer/hochkant)
- **Aufgehellt** per Tonwertkurve (Schatten und Mitten heller, Schwarz bleibt schwarz), vorher leicht entrauscht
- **Datenpanel:** Schläger, Datum/Uhrzeit, Ballgeschwindigkeit, Schlägerkopf (km/h), Smash, Abflugwinkel,
  Richtung (L/R), Spin, Spinachse, Angle of Attack, Club Path, Face to Target, Face to Path
- **Schaftlinien mit Winkel:** blau = Ansprechen (ganzes Video), orange = Abschwung (ab kurz vor dem Treffen),
  verlängert bis zum Bildrand (Schwungebene)
- **Schwungbahn:** hellblau = Ausholen, orange-rot = Abschwung. Die Bahn des Schlägerkopfs wächst beim Abspielen
  mit, dazu dünne Schaftlinien als Fächer. Gezeigt wird der Teil, in dem der Schläger im Bild ist.
- **Ganzer Clip** in guter Qualität (CRF 20) → ca. **7 MB** pro Schlag. Kleiner: `export_trim: true` und/oder
  `export_crf: 28` (ca. 0,5 MB)

**So funktioniert die automatische Analyse** (`pc\swing_analysis.py`):
1. *Treffmoment:* Der Ball ist der hellste Punkt unten im Bild; beim Treffen verschwindet er schlagartig.
2. *Ansprechen:* letztes ruhiges Bild, bevor sich der Schläger um den Ball bewegt.
3. *Schaft:* Bild mit Schläger am Ball minus Bild ohne Schläger → Kanten → Hough-Linien → die längste Linie,
   die zum Schlägerkopf zeigt.
4. *Schwungbahn:* Schlägerkopf Bild für Bild verfolgen (Schaft per Hough-Linie, Kopf = kompakter Fleck am
   Schaftende), beim Ausholen vorwärts ab dem Ansprechen, im Abschwung rückwärts ab dem Treffmoment, bis er das
   Bild verlässt.

Handgezeichnete Linien haben Vorrang. Ohne Ball im Bild oder vor sehr unruhigem Hintergrund kann die Erkennung
danebenliegen – dann die Linie auf der Analyse-Seite von Hand ziehen.

> Hinweis: Der R10-Connector liefert *Face to Path* nicht (immer 0). Der Wert wird als
> *Face to Target − Club Path* berechnet.

---

## 5. Einstellungen (`pc\config.json`)

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `proxy_listen_port` | 9211 | Port, an den der Connector sendet |
| `gspro_host` / `gspro_port` | 127.0.0.1 / 921 | GSPro OpenAPI |
| `phone_control_port` | 9300 | Steuerverbindung zum Handy |
| `http_port` | 8090 | Analyse-Seite und Upload |
| `clip_pre_seconds` / `clip_post_seconds` | 3,5 / 0,5 | Clip vor/nach dem (geschätzten) Treffmoment |
| `trigger_delay_seconds` | 0 | R10-Verzögerung (per Kalibrierung) |
| `shots_dir` / `exports_dir` | shots / exports | Speicherorte |
| `export_playback_fps` | 30 | 30 = Zeitlupe 1/8, 60 = 1/4, 240 = Echtzeit |
| `export_crf` | 20 | Qualität: 20 ≈ 7 MB, 23 ≈ 3–4 MB, 28 ≈ 0,5 MB (niedriger = besser/größer) |
| `export_preset` | medium | x264-Preset (`slow` = etwas kleiner, dauert länger) |
| `export_brightness` | 0,6 | Aufhellen: 0 = aus, 0,6 = deutlich, 1,0 = stark (gilt auch für die Analyse-Seite) |
| `export_denoise` | 3 | leichtes Entrauschen vor dem Aufhellen (0 = aus) |
| `export_paths` | true | Schwungbahn (Ausholen/Abschwung) einzeichnen |
| `export_trim` | false | true = Export nur vom Schwung (Ansprechen − 0,25 s bis Treffen + 0,6 s) |
| `auto_analysis` | true | Treffmoment + Linien automatisch |
| `ffmpeg_path` | "" | leer = automatisch suchen |

Ordner pro Schlag: `pc\shots\<JJJJMMTT-hhmmss-nn>\` mit `clip.mp4` (Original 240 fps), `shot.json`
(Daten vom Connector + Schläger aus GSPro), `video.json`, `notes.json` (Notiz, Treffmoment, Linien), `export.json`.

---

## 6. Kamera-Aufbau, Licht, Weitwinkel

- **Position:** hinter dem Spieler auf der Verlängerung der Ziellinie (Down-the-Line), Hüft- bis Handhöhe,
  **quer**. Ball und Hände möglichst in die Bildmitte.
- **Abstand** für den ganzen Schwung (hochkant, Poco X3 Hauptkamera ≈ 26 mm):

  | | Breite / Höhe bei 1,5 m | Abstand für ganzen Schwung |
  |---|---|---|
  | ohne Aufsatz | ca. 1,1 m / 2,0 m | ca. 2,5–3 m |
  | mit 0,45×-Weitwinkel-Aufsatz | ca. 2,5 m / 4,4 m | ca. 1,2–1,5 m |

  Das eingebaute Ultra-Weitwinkel gibt Xiaomi für Fremd-Apps nicht frei. Ein Aufsatz muss genau über der
  Hauptlinse sitzen; am Bildrand verzerrt er – Linien/Winkel dort nur grob.
- **Licht:** 240 fps = höchstens 1/240 s Belichtung → viel Licht auf die Abschlagzone. Normale LED-/Leuchtstoff-
  lampen flackern mit 100 Hz (sichtbares Pumpen) → flimmerfreie Lampen verwenden.
- **Wärme:** Dauer-Aufnahme mit 240 fps erwärmt das Handy; Warnung ab 43 °C. Ladegerät dran, Display dimmen.

---

## 7. Fehlersuche

| Problem | Lösung |
|---|---|
| Seite meldet „Seite bitte über http://localhost:8090 öffnen“ | Seite wurde als Datei geöffnet – Adresse im Browser verwenden |
| „Tracer-Programm nicht erreichbar“ | `pc\start_tracer.bat` starten |
| „Connector nicht verbunden“ | Port in der Connector-`settings.json` auf 9211; Reihenfolge GSPro → Tracer → Connector |
| App: „PC nicht erreichbar“ | richtige PC-IP? gleiches WLAN? Firewall für Python (private Netzwerke) erlaubt? |
| Kein Video zum Schuss | App im Vordergrund? Handy verbunden (Analyse-Seite oben)? |
| Treffmoment nicht im Clip | Kalibrierung durchführen (Einstellungen) |
| Keine Linien im Export | OpenCV installiert? Ball im Bild? Sonst von Hand zeichnen |
| Kein Export-Video | FFmpeg installiert (`winget install Gyan.FFmpeg`), Tracer neu starten |
| App zeigt „KEIN High-Speed“ | Kamera-Diagnose auf der Live-Seite prüfen; Modus 120 fps versuchen |

---

## 8. Für Entwickler

```
pc/
  gsprotracer.py     Proxy, Handy-Steuerkanal, HTTP-Server, Speicherung
  exporter.py        FFmpeg-Export: Datenpanel, Linien, Zuschnitt
  swing_analysis.py  OpenCV: Treffmoment, Ansprechen, Schaftlinien
  viewer.html        Analyse-Seite        live.html   Kamera ausrichten
  config.json        Einstellungen        start_tracer.bat
android/             Kotlin-App, nur Android-Framework (+ androidx.core für FileProvider)
  HighSpeedRecorder  Camera2 Constrained High Speed → MediaCodec (H.264) → Ringpuffer → MediaMuxer
  PcLink             TCP-Steuerkanal (JSON-Zeilen), HTTP-Upload, Live-Bild
  Updater            In-App-Update aus GitHub-Release cam-latest
.github/workflows/android.yml   APK-Build, Release cam-latest
```

**Beispieldaten:** In [`samples/`](samples/) liegt ein echter Schlag (240-fps-Original, R10-Daten, von Hand
gesetzte Referenz für Treffmoment und Schaftlinien) und ein fertiges Export-Video. Test der automatischen Analyse:
`python pc\test_sample.py`. Damit kann man ohne Handy, R10 und GSPro weiterentwickeln.

**Protokoll:** Handy ↔ PC über TCP 9300, eine JSON-Nachricht pro Zeile
(`hello`/`status` vom Handy, `trigger`/`preview` vom PC). Upload per `POST /api/upload?shot=…&fps=…&trigger=…`.

**Build:** Jeder Push mit Änderungen unter `android/` baut die APK mit GitHub Actions (Gradle 8.11, JDK 17);
auf dem Standard-Branch wird das Release **cam-latest** mit `gsprotracer-cam-v<Laufnummer>.apk` ersetzt.
Die `versionCode` ist die Laufnummer – daran erkennt die App Updates.

**Signatur:** Updates installieren sich nur bei gleichem Schlüssel. Der Schlüssel liegt **nicht** im (öffentlichen)
Repository, sondern als Secrets `TRACER_KEYSTORE_BASE64` und `TRACER_KEYSTORE_PASSWORD` (PKCS12, Alias `tracer`).
Ohne Secrets baut der Workflow mit Debug-Schlüssel und veröffentlicht kein Release.
Schlüssel-Backup gut aufbewahren – ohne ihn muss die App für ein Update neu installiert werden.

---

## Bekannte Grenzen / Ideen

- Ob ein Handy 240 fps für Fremd-Apps freigibt, hängt vom Hersteller ab (Poco X3: ja, 720p).
- Zeitbezug Trigger ↔ Video ist auf einige 10 ms genau; der Treffmoment wird deshalb im Bild erkannt.
- Geplant: Entzerrung für Weitwinkel-Aufsätze (Kalibrierung mit Schachbrettmuster), Vergleich zweier Schwünge
  nebeneinander, Schwungebene über den ganzen Rückschwung.
