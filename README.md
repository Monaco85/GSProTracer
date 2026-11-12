# GSPro Tracer – Schwungvideos (Poco X3, 240 fps) passend zu jedem R10-Schuss

```
Garmin R10 ──BT──► MLM2PRO-Connector ──► Tracer-Proxy :9211 ──► GSPro OpenAPI :921
                                              │  (erkennt den Schuss)
                                              ▼
                       Poco X3 (App) ◄── Trigger über WLAN (Port 9300)
                       Ringpuffer 720p/240fps → schneidet Clip → Upload (HTTP :8090)
                                              ▼
                    PC: pc\shots\<Zeit>\clip.mp4 + shot.json  →  Analyse im Browser
```

**Warum Ringpuffer?** Der R10 meldet die Werte erst **nach** dem Treffmoment (meist 1–3 s später).
Ein Video, das erst dann startet, würde den Schwung verpassen. Das Handy nimmt deshalb ununterbrochen
auf, behält die letzten 12 s im Speicher und schneidet beim Trigger rückwirkend den passenden Ausschnitt aus.
Nur dieser Clip (≈ 2–4 s, ~10–20 MB) wird gespeichert, an den PC geschickt und danach auf dem Handy gelöscht.

---

## 1. PC einrichten

1. `pc\start_tracer.bat` doppelklicken (benötigt nur Python, ist installiert – keine Pakete nötig).
   Beim ersten Start fragt die Windows-Firewall → **Private Netzwerke erlauben** (Handy muss Port 9300 und 8090 erreichen).
   Im Fenster steht die PC-IP für die Handy-App (bei dir vermutlich `192.168.1.41`).
2. Connector auf den Proxy umstellen – in
   `C:\GSProV1\MLM2Pro-GSPro-Connector_V1_04_20\appdata\settings.json`:
   ```json
   "port": 9211,
   ```
   (statt `921`). Connector neu starten. **Reihenfolge:** zuerst GSPro/OpenAPI, dann Tracer, dann Connector.
   Zurück zum Originalzustand: wieder `921` eintragen.
3. Analyse öffnen: <http://localhost:8090>

Läuft der Tracer nicht, bekommt der Connector keine Verbindung zu GSPro → dann einfach Tracer starten
oder Port wieder auf 921 stellen.

## 2. App bauen (GitHub Actions) und aufs Poco X3 bringen

Bei jedem Push mit Änderungen unter `android/` baut GitHub die App (`.github/workflows/android.yml`).
Auf dem Standard-Branch entsteht zusätzlich das Release **cam-latest** mit `gsprotracer-cam-v<Build>.apk`
(wie bei Tennisrecord). Die Build-Nummer ist die Laufnummer von GitHub Actions.

### Signaturschlüssel (einmalig, wichtig für Updates)

Android installiert ein Update nur, wenn es mit **demselben** Schlüssel signiert ist. Weil dieses Repository
**öffentlich** ist, liegt der Schlüssel nicht im Repo, sondern als GitHub-Secret:

* Schlüssel + Passwort liegen lokal in `%USERPROFILE%\Documents\GSProTracer-Signatur\` → **Backup machen**.
* Einmal `secrets_setzen.ps1` in diesem Ordner ausführen (setzt `TRACER_KEYSTORE_BASE64` und `TRACER_KEYSTORE_PASSWORD`).
* Ohne Secrets baut der Workflow nur mit Debug-Schlüssel und veröffentlicht **kein** Release.

### Installieren / Updaten am Handy

* **Erste Installation:** Auf GitHub unter *Releases → cam-latest* die APK am Handy herunterladen und öffnen
  („Unbekannte Apps installieren“ erlauben; MIUI fragt evtl. zusätzlich).
* **Updates:** in der App **Update** → *Nach Update suchen*. Lädt den neuesten Build aus `cam-latest` und startet
  die Installation (bestätigen). Ein Token ist nur nötig, falls das Repo später privat wird
  (Fine-grained Token, nur dieses Repo, *Contents: Read-only*).
## 3. Benutzung

1. App starten, PC-IP eintragen → *Verbinden*. Oben links muss **PC verbunden** stehen und
   **(High-Speed)** mit 240 fps (720p) oder – falls das Poco das nicht freigibt – 120 fps (1080p).
2. Handy mit Stativ/Halterung quer ausrichten (Face-On oder Down-the-Line). Wenn das Video auf dem Kopf steht:
   *Video drehen* (0/90/180/270°).
3. Ausrichtung prüfen: **Testclip** (am Handy oder im Browser) → Clip erscheint nach ein paar Sekunden am PC.
4. *Belichtung/Fokus sperren* drücken, wenn das Bild steht (verhindert Pumpen, wenn du ins Bild trittst).
5. *Display dimmen* reduziert die Wärme deutlich.
6. Schlagen. Bei jedem Schuss blinkt am Handy ein grüner Rahmen, am PC erscheint der Clip mit allen R10-Werten
   und wird automatisch in Zeitlupe (¼) abgespielt.

### Kalibrierung (einmalig, 3–5 Schläge)

Anfangs nimmt die App großzügig **4 s vor bis 0,5 s nach** dem Eintreffen der R10-Daten auf.
1. Im Browser bei einigen Schüssen per Frame-Schritt (← / →) zum Treffmoment gehen und **„Impact hier“** (Taste `I`) drücken.
2. *Einstellungen* → *Vorschlag übernehmen* → Speichern.
   Danach sind die Clips genau **1,5 s vor bis 1,0 s nach dem Impact** (= 2,5 s, wie gewünscht).
   Die Werte gelten sofort, auch ohne Neustart.

### Bedienung Analyse-Seite

| Taste | Funktion |
|---|---|
| Leertaste | Play/Pause |
| ← / → bzw. `,` / `.` | 1 Frame (1/240 s) zurück/vor, mit Shift 10 Frames |
| ↑ / ↓ | vorheriger/nächster Schuss |
| `I` | aktuelles Bild als Impact markieren |
| Maus ziehen im Video | Hilfslinie zeichnen (z. B. Schwungebene, Kopfposition) |

Tempo 1× bis 1/16 (bei 240 fps entspricht 1/8 ungefähr echter 30-fps-Wiedergabe jedes Einzelbildes).
Rechts: Ball-/Schlägerdaten, Smash-Faktor, Notiz pro Schuss, Durchschnitt pro Schläger (Schläger kommt aus GSPro).
*CSV-Export* öffnet sich direkt in Excel (Semikolon, deutsches Komma).

Dateien: `pc\shots\<JJJJMMTT-hhmmss-nn>\` → `clip.mp4`, `shot.json` (Original-Daten des Connectors),
`video.json`, `notes.json`. Einstellungen: `pc\config.json`.

---

## Wichtige Hinweise / bekannte Grenzen

* **240 fps hängt am Poco-Kamera-Treiber.** Die Xiaomi-Kamera-App kann Zeitlupe, aber ob der Hersteller 720p@240
  auch **für fremde Apps** (Camera2 *Constrained High Speed*) freigibt, lässt sich erst am Gerät sehen.
  Die App sucht automatisch den besten Modus und zeigt ihn oben links an; die vollständige Liste aller Kamera-Modi
  steht im Tooltip des Handy-Status im Browser. Steht dort *KEIN High-Speed verfügbar*, nimmt sie mit der höchsten
  normalen Bildrate (meist 30/60 fps) auf – dann bitte die Kamera-Liste schicken, dann suchen wir den nächsten Weg.
* **Licht:** 240 fps = max. 1/240 s Belichtung → viel Licht nötig. Normale LED-/Leuchtstoffröhren flackern mit
  100 Hz; bei 240 fps sieht man das als Helligkeitspumpen. Flimmerfreie Lampen (oder Tageslicht) verwenden.
* **Wärme:** Dauerhafte 240-fps-Kodierung erwärmt das Handy. Akku-Temperatur wird angezeigt (Warnung ab 42 °C).
  Ladegerät dran lassen, Display dimmen, nicht in die Sonne/an den Beamer legen.
* Der Zeitbezug Trigger ↔ Video ist auf einige 10 ms genau (WLAN + Encoder-Latenz); der Impact wird deshalb
  über die Kalibrierung im Clip-Fenster gehalten, nicht framegenau berechnet.
* Clips, die nicht hochgeladen werden konnten (PC aus, WLAN weg), bleiben auf dem Handy und werden automatisch
  nachgeschickt, sobald der PC wieder erreichbar ist.
