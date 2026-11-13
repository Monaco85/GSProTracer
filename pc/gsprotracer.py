"""
GSPro Tracer - PC-Seite
=======================

* Transparenter TCP-Proxy zwischen MLM2PRO-GSPro-Connector und GSPro OpenAPI.
  Jeder echte Schuss (ContainsBallData, kein Heartbeat) wird gespeichert und
  als Trigger an das Handy (Poco X3) geschickt.
* Steuerkanal fuer das Handy (TCP, eine JSON-Nachricht pro Zeile).
* HTTP-Server: Upload der Clips vom Handy + Analyse-Oberflaeche im Browser.

Nur Python-Standardbibliothek, keine Installation noetig.
Start:  python gsprotracer.py
"""

import csv
import io
import json
import logging
import os
import re
import shutil
import socket
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from exporter import MPH_TO_KMH, Exporter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
VIEWER_PATH = os.path.join(BASE_DIR, "viewer.html")
LIVE_PATH = os.path.join(BASE_DIR, "live.html")
SHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

log = logging.getLogger("tracer")

DEFAULT_CONFIG = {
    "proxy_listen_port": 9211,
    "gspro_host": "127.0.0.1",
    "gspro_port": 921,
    "phone_control_port": 9300,
    "http_port": 8090,
    "clip_pre_seconds": 6.0,           # Aufnahme beginnt 6 s vor dem R10-Trigger (Export wird zugeschnitten)
    "clip_post_seconds": 0.5,
    "trigger_delay_seconds": 0.0,
    "shots_dir": "shots",
    "exports_dir": "exports",          # fertige Videos mit eingeblendeten Schlagdaten
    "export_playback_fps": 30,         # 240 fps -> 30 fps = Zeitlupe 1/8, jedes Bild sichtbar
    "ffmpeg_path": "",                 # leer = automatisch suchen
    "auto_analysis": True,             # Treffmoment + Schaftlinien automatisch erkennen (OpenCV)
    "export_trim": True,               # Export vom Ansprechen (kurz davor) bis kurz nach dem Treffmoment
    "only_complete_shots": True,       # Schuesse ohne Treffmoment/Ansprech-/Schlaglinie verwerfen
    "export_crf": 20,                  # Qualitaet (niedriger = besser/groesser; 20 ~ 7 MB, 28 ~ 0,5 MB)
    "export_denoise": 3,               # leichtes Entrauschen vor dem Aufhellen (0 = aus)
    "export_preset": "medium",         # x264-Preset (slow = etwas kleiner, dauert laenger)
    "export_brightness": 1.0,          # Aufhellen im Export (0 = aus, 0,6 = deutlich, 1,0 = stark)
    "export_monochrome": True,         # Schwarzweiss: Schwung klarer, farbige Linien heben sich ab
    "clip_orientation": "quer_180",    # Clips verlustfrei drehen: quer = Sensorbild, quer_180 = auf dem Kopf
                                       # (Handy andersherum), "" = so lassen wie aufgenommen
    "export_delay_seconds": 8,         # Export erst nach dem Ballflug starten (GSPro/Connector nicht stoeren)
    "export_threads": 2,               # CPU-Kerne fuer den Export (0 = alle)
}


# --------------------------------------------------------------------------- config
class Config:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = dict(DEFAULT_CONFIG)
        if os.path.exists(path):
            self.data.update(self._load(path))
        self.data.pop("export_paths", None)            # Schlaegerkopf-Bahn gibt es nicht mehr
        # einmalig: "quer" lieferte bei diesem Aufbau ein Bild auf dem Kopf -> auf quer_180 umstellen
        if self.data.get("orientation_version") != 2:
            if self.data.get("clip_orientation") == "quer":
                self.data["clip_orientation"] = "quer_180"
            self.data["orientation_version"] = 2
        # einmalig: langer Vorlauf (es fehlte manchmal ein Stueck vom Schwung), Export zuschneiden
        if self.data.get("settings_version", 0) < 3:
            self.data["clip_pre_seconds"] = max(6.0, float(self.data.get("clip_pre_seconds") or 0))
            self.data["export_trim"] = True
            self.data["only_complete_shots"] = True
            self.data["settings_version"] = 3
        # einmalig: Schwarzweiss und heller
        if self.data.get("settings_version", 0) < 4:
            self.data["export_monochrome"] = True
            self.data["export_brightness"] = max(1.0, float(self.data.get("export_brightness") or 0))
            self.data["settings_version"] = 4
        self.save()

    @staticmethod
    def _load(path):
        """
        config.json lesen. Typische Tippfehler beim Bearbeiten (fehlende/ueberzaehlige Kommas) werden
        repariert; ist die Datei gar nicht lesbar, wird sie gesichert und mit Standardwerten gestartet.
        """
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            err = e
        fixed = re.sub(r'(["\d\]}]|true|false|null)(\s*\n\s*")', r"\1,\2", text)   # fehlendes Komma am Zeilenende
        fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)                               # Komma vor } zu viel
        try:
            data = json.loads(fixed)
            log.warning("config.json hatte einen Tippfehler (%s) - automatisch repariert", err)
            return data
        except json.JSONDecodeError:
            pass
        backup = path + ".defekt.txt"
        shutil.copyfile(path, backup)
        log.error("config.json ist fehlerhaft (%s) - Standardwerte werden benutzt, alte Datei gesichert als %s",
                  err, backup)
        return {}

    def __getitem__(self, key):
        with self.lock:
            return self.data[key]

    def update(self, values):
        with self.lock:
            for k, v in values.items():
                if k == "clip_orientation":
                    if v in ("quer", "quer_180", ""):
                        self.data[k] = v
                elif k in DEFAULT_CONFIG and k.startswith(("clip_", "trigger_")):
                    self.data[k] = float(v)
        self.save()

    def save(self):
        with self.lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)

    @property
    def shots_dir(self):
        d = self["shots_dir"]
        return d if os.path.isabs(d) else os.path.join(BASE_DIR, d)


# --------------------------------------------------------------------------- shot store
class ShotStore:
    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.lock = threading.Lock()
        self._last_id = ""
        self._counter = 0

    def new_id(self):
        with self.lock:
            base = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._counter = self._counter + 1 if base == self._last_id else 1
            self._last_id = base
            return f"{base}-{self._counter:02d}"

    def path(self, shot_id, *parts):
        if not SHOT_ID_RE.match(shot_id):
            raise ValueError("invalid shot id")
        return os.path.join(self.root, shot_id, *parts)

    def write_json(self, shot_id, name, data):
        os.makedirs(self.path(shot_id), exist_ok=True)
        tmp = self.path(shot_id, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path(shot_id, name))

    def read_json(self, shot_id, name):
        p = self.path(shot_id, name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def get(self, shot_id):
        if not os.path.isdir(self.path(shot_id)):
            return None
        return {
            "id": shot_id,
            "shot": self.read_json(shot_id, "shot.json"),
            "video": self.read_json(shot_id, "video.json"),
            "notes": self.read_json(shot_id, "notes.json") or {},
            "export": self.read_json(shot_id, "export.json"),
            "has_video": os.path.exists(self.path(shot_id, "clip.mp4")),
        }

    def list(self):
        ids = [d for d in os.listdir(self.root)
               if SHOT_ID_RE.match(d) and os.path.isdir(os.path.join(self.root, d))]
        ids.sort(reverse=True)
        return [s for s in (self.get(i) for i in ids) if s]

    def delete(self, shot_id):
        p = self.path(shot_id)
        if os.path.isdir(p):
            shutil.rmtree(p)


# --------------------------------------------------------------------------- JSON stream
class JsonStream:
    """Zerlegt einen TCP-Bytestrom in aufeinanderfolgende JSON-Objekte."""

    def __init__(self):
        self.buf = ""
        self.decoder = json.JSONDecoder()

    def feed(self, data: bytes):
        self.buf += data.decode("utf-8", errors="replace")
        out = []
        while True:
            s = self.buf.lstrip()
            if not s:
                self.buf = ""
                break
            start = s.find("{")
            if start < 0:
                self.buf = ""
                break
            s = s[start:]
            try:
                obj, end = self.decoder.raw_decode(s)
            except json.JSONDecodeError:
                self.buf = s if len(s) < 1_000_000 else ""
                break
            out.append(obj)
            self.buf = s[end:]
        return out


# --------------------------------------------------------------------------- phone hub
class PhoneHub:
    """Verwaltet die Steuerverbindungen der Handys."""

    def __init__(self, port):
        self.port = port
        self.lock = threading.Lock()
        self.phones = {}  # socket -> info dict

    def start(self):
        threading.Thread(target=self._accept_loop, daemon=True, name="phone-accept").start()

    def _accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(5)
        log.info("Handy-Steuerkanal lauscht auf Port %d", self.port)
        while True:
            conn, addr = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            with self.lock:
                self.phones[conn] = {"addr": addr[0], "connected_at": time.time(), "status": {}}
            log.info("Handy verbunden: %s", addr[0])
            threading.Thread(target=self._reader, args=(conn,), daemon=True).start()

    def _reader(self, conn):
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    with self.lock:
                        if conn in self.phones:
                            self.phones[conn]["status"] = msg
                            self.phones[conn]["last_seen"] = time.time()
                    if msg.get("type") == "hello":
                        log.info("Handy meldet: %s", msg)
        except OSError:
            pass
        finally:
            with self.lock:
                info = self.phones.pop(conn, None)
            try:
                conn.close()
            except OSError:
                pass
            if info:
                log.info("Handy getrennt: %s", info["addr"])

    def broadcast(self, msg):
        line = (json.dumps(msg) + "\n").encode("utf-8")
        with self.lock:
            conns = list(self.phones.keys())
        sent = 0
        for c in conns:
            try:
                c.sendall(line)
                sent += 1
            except OSError:
                pass
        return sent

    def snapshot(self):
        with self.lock:
            return [{"addr": i["addr"], "connected_at": i["connected_at"],
                     "last_seen": i.get("last_seen"), "status": i["status"]}
                    for i in self.phones.values()]


# --------------------------------------------------------------------------- GSPro proxy
class GSProProxy:
    def __init__(self, cfg: Config, on_shot, on_gspro_msg):
        self.cfg = cfg
        self.on_shot = on_shot
        self.on_gspro_msg = on_gspro_msg
        self.connected = False

    def start(self):
        threading.Thread(target=self._accept_loop, daemon=True, name="proxy-accept").start()

    def _accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", self.cfg["proxy_listen_port"]))
        srv.listen(2)
        log.info("GSPro-Proxy lauscht auf 127.0.0.1:%d -> %s:%d",
                 self.cfg["proxy_listen_port"], self.cfg["gspro_host"], self.cfg["gspro_port"])
        while True:
            client, _ = srv.accept()
            try:
                upstream = socket.create_connection((self.cfg["gspro_host"], self.cfg["gspro_port"]), timeout=5)
                upstream.settimeout(None)
            except OSError as e:
                log.warning("GSPro nicht erreichbar (%s) - Connector-Verbindung wird geschlossen", e)
                client.close()
                continue
            for s in (client, upstream):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connected = True
            log.info("Connector <-> GSPro verbunden (ueber Proxy)")
            # gemeinsamer Zustand beider Richtungen -> beim Ende protokollieren, wer getrennt hat
            st = {"t0": time.time(), "closed_by": None, "n_conn": 0, "n_gspro": 0,
                  "last_conn": "", "last_gspro": "", "lock": threading.Lock()}
            threading.Thread(target=self._pump, args=(client, upstream, True, st), daemon=True).start()
            threading.Thread(target=self._pump, args=(upstream, client, False, st), daemon=True).start()

    @staticmethod
    def _describe(obj):
        if "Code" in obj:
            return f"Code {obj.get('Code')} {str(obj.get('Message') or '')[:60]}".strip()
        opts = obj.get("ShotDataOptions") or {}
        if opts.get("IsHeartBeat"):
            return "Heartbeat"
        if opts.get("ContainsBallData"):
            return "Schuss"
        return "Status" if opts else "?"

    @staticmethod
    def _watch_reply(st):
        """Warnen, wenn GSPro auf einen Schuss nicht antwortet (haeufig: API Connect haengt)."""
        n0 = st["n_gspro"]

        def check():
            if st["closed_by"] is None and st["n_gspro"] == n0:
                log.warning("GSPro antwortet nicht auf den Schuss! Wahrscheinlich haengt GSPro API Connect noch an "
                            "einer alten Verbindung. In GSPro API Connect schliessen und neu starten, dann im "
                            "Connector auf 'Connect' druecken.")
        t = threading.Timer(3.0, check)
        t.daemon = True
        t.start()

    def _pump(self, src, dst, from_connector, st):
        stream = JsonStream()
        side = "Connector" if from_connector else "GSPro"
        reason = f"{side} hat die Verbindung geschlossen"
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                try:
                    dst.sendall(data)  # zuerst weiterleiten -> keine zusaetzliche Latenz fuer GSPro
                except OSError as e:
                    reason = f"Weiterleiten an {'GSPro' if from_connector else 'Connector'} fehlgeschlagen: {e}"
                    break
                try:
                    for obj in stream.feed(data):
                        desc = self._describe(obj)
                        if from_connector:
                            st["n_conn"] += 1
                            st["last_conn"] = desc
                            if desc != "Heartbeat":
                                log.info("Connector -> GSPro: %s", desc)
                            opts = obj.get("ShotDataOptions", {})
                            if opts.get("ContainsBallData") and not opts.get("IsHeartBeat"):
                                self.on_shot(obj)
                                self._watch_reply(st)
                        else:
                            st["n_gspro"] += 1
                            st["last_gspro"] = desc
                            if obj.get("Code") != 200:
                                log.info("GSPro -> Connector: %s", desc)
                            self.on_gspro_msg(obj)
                except Exception:  # Analysefehler duerfen den Proxy nie stoeren
                    log.exception("Fehler beim Auswerten der Nachricht")
        except OSError as e:
            reason = f"Verbindung zu {side} abgebrochen: {e}"
        finally:
            with st["lock"]:
                first = st["closed_by"] is None
                if first:
                    st["closed_by"] = reason
            self.connected = False
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass
            if first:
                log.warning("Connector-Verbindung beendet nach %.0f s: %s | Connector->GSPro %d Nachrichten "
                            "(zuletzt: %s), GSPro->Connector %d (zuletzt: %s)",
                            time.time() - st["t0"], reason, st["n_conn"], st["last_conn"] or "-",
                            st["n_gspro"], st["last_gspro"] or "-")


# --------------------------------------------------------------------------- app
class TracerApp:
    def __init__(self):
        self.cfg = Config(CONFIG_PATH)
        self.store = ShotStore(self.cfg.shots_dir)
        self.hub = PhoneHub(self.cfg["phone_control_port"])
        self.proxy = GSProProxy(self.cfg, self.handle_shot, self.handle_gspro_msg)
        self.exporter = Exporter(self.store, self.cfg)
        self.player = {}
        self.last_shot_id = None
        self._last_shot_key, self._last_shot_at = None, 0.0
        # Live-Bild vom Handy (nur solange die Live-Seite offen ist)
        self.preview_lock = threading.Lock()
        self.preview_jpeg = None
        self.preview_at = 0.0
        self.preview_frames = []      # Zeitstempel der letzten Bilder -> fps
        self.preview_keepalive = 0.0

    def request_preview(self):
        """Wird bei jedem Abruf der Live-Seite aufgerufen; haelt das Handy im Live-Modus."""
        now = time.time()
        if now - self.preview_keepalive > 1.5:
            self.preview_keepalive = now
            self.hub.broadcast({"type": "preview", "upload_port": self.cfg["http_port"]})

    def store_preview(self, jpeg):
        now = time.time()
        with self.preview_lock:
            self.preview_jpeg = jpeg
            self.preview_at = now
            self.preview_frames = [t for t in self.preview_frames if now - t < 2.0] + [now]

    def preview_fps(self):
        with self.preview_lock:
            f = [t for t in self.preview_frames if time.time() - t < 2.0]
        return round(len(f) / 2.0, 1)

    def handle_gspro_msg(self, obj):
        if obj.get("Code") == 201 and isinstance(obj.get("Player"), dict):
            self.player = obj["Player"]
            log.info("GSPro Spieler-Info: %s", self.player)

    def trigger_phones(self, shot_id):
        msg = {
            "type": "trigger",
            "shot_id": shot_id,
            "pre": self.cfg["clip_pre_seconds"],
            "post": self.cfg["clip_post_seconds"],
            "delay": self.cfg["trigger_delay_seconds"],
            "upload_port": self.cfg["http_port"],
        }
        n = self.hub.broadcast(msg)
        if n == 0:
            log.warning("Schuss %s: kein Handy verbunden - kein Video", shot_id)
        return n

    def handle_shot(self, payload, test=False):
        # Connector wiederholt einen Schuss, wenn GSPro nicht antwortet -> nicht doppelt aufnehmen
        if not test:
            key = json.dumps({k: (payload or {}).get(k) for k in ("ShotNumber", "BallData", "ClubData")},
                             sort_keys=True)
            now = time.time()
            if key == self._last_shot_key and now - self._last_shot_at < 15:
                log.info("Gleicher Schuss nochmal gesendet (Wiederholung des Connectors) - wird ignoriert")
                return self.last_shot_id
            self._last_shot_key, self._last_shot_at = key, now
        shot_id = self.store.new_id()
        # Trigger sofort raus, erst danach speichern (Timing!)
        n = self.trigger_phones(shot_id)
        record = {
            "received_at": datetime.now().isoformat(timespec="milliseconds"),
            "test": test,
            "club": self.player.get("Club"),
            "player": self.player,
            "payload": payload,
            "phones_triggered": n,
        }
        self.store.write_json(shot_id, "shot.json", record)
        self.last_shot_id = shot_id
        bd = payload.get("BallData", {}) if payload else {}
        cd = payload.get("ClubData", {}) if payload else {}
        log.info("Schuss %s  Ball %.1f mph  VLA %.1f  HLA %.1f  Spin %.0f  Club %.1f mph",
                 shot_id, bd.get("Speed", 0) or 0, bd.get("VLA", 0) or 0, bd.get("HLA", 0) or 0,
                 bd.get("TotalSpin", 0) or 0, cd.get("Speed", 0) or 0)
        return shot_id

    def status(self):
        return {
            "proxy_connected": self.proxy.connected,
            "phones": self.hub.snapshot(),
            "player": self.player,
            "last_shot_id": self.last_shot_id,
            "ffmpeg": bool(self.exporter.ffmpeg),
            "exporting": self.exporter.busy,
            "exports_dir": self.exporter.out_dir,
            "config": dict(self.cfg.data),
        }

    def calibration(self):
        """Schlaegt trigger_delay vor: Abstand Trigger-Zeitpunkt -> markierter Impact."""
        diffs = []
        for s in self.store.list():
            v, n = s["video"], s["notes"]
            if not v or "impact_time" not in n or v.get("trigger_time") is None:
                continue
            # trigger_time = Sekunde im Clip, an der die R10-Daten am Handy ankamen
            # (darf hinter dem Clip-Ende liegen). Differenz = Latenz Impact -> Daten.
            diffs.append(v["trigger_time"] - n["impact_time"])
        if not diffs:
            return {"samples": 0}
        diffs.sort()
        median = diffs[len(diffs) // 2]
        return {"samples": len(diffs), "suggested_delay": round(median, 3),
                "min": round(diffs[0], 3), "max": round(diffs[-1], 3)}

    def reorient_all(self, enqueue=True):
        """
        Alle gespeicherten Clips gemaess clip_orientation drehen (verlustfrei). Gedrehte Schuesse werden neu
        analysiert (Linien haengen von der Bildlage ab) und neu exportiert. Rueckgabe: Liste der IDs.
        """
        from exporter import fix_orientation
        mode = self.cfg.data.get("clip_orientation", "quer_180")
        changed = []
        for s in self.store.list():
            if not s["has_video"]:
                continue
            sid = s["id"]
            try:
                new = fix_orientation(self.exporter.ffmpeg, self.store.path(sid, "clip.mp4"), mode)
            except Exception:
                log.exception("Drehen %s fehlgeschlagen", sid)
                continue
            if not new:
                continue
            video = self.store.read_json(sid, "video.json") or {}
            video["width"], video["height"] = new
            self.store.write_json(sid, "video.json", video)
            notes = self.store.read_json(sid, "notes.json") or {}
            for k in ("analysis", "swing"):
                notes.pop(k, None)
            notes["lines"] = [l for l in notes.get("lines") or [] if not l.get("auto")]
            self.store.write_json(sid, "notes.json", notes)
            changed.append(sid)
            if enqueue:
                self.exporter.enqueue(sid)
        if changed:
            log.info("%d Clip(s) gedreht (%s) - Linien und Videos werden neu erstellt", len(changed), mode)
        return changed

    def run(self):
        self.hub.start()
        self.proxy.start()
        # vorhandene Clips richtig herum drehen (z.B. nach Aenderung von clip_orientation)
        if self.exporter.ffmpeg:
            changed = self.reorient_all(enqueue=False)
        else:
            changed = []
        self.exporter.start()
        for sid in changed:
            if (self.store.read_json(sid, "export.json") or {}).get("file"):
                self.exporter.enqueue(sid)   # ohne Export werden sie von exporter.start() schon eingereiht
        handler = make_handler(self)
        httpd = QuietHTTPServer(("0.0.0.0", self.cfg["http_port"]), handler)
        httpd.daemon_threads = True
        log.info("Analyse-Oberflaeche: http://localhost:%d", self.cfg["http_port"])
        log.info("Shots werden gespeichert in: %s", self.store.root)
        log.info("Videos mit Schlagdaten (km/h) in: %s", self.exporter.out_dir)
        log.info("PC-IP-Adressen fuer die Handy-App: %s", ", ".join(local_ips()) or "?")
        httpd.serve_forever()


class QuietHTTPServer(ThreadingHTTPServer):
    """Browser brechen Video-Verbindungen beim Spulen ständig ab - das ist normal, kein Fehler."""

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


# --------------------------------------------------------------------------- HTTP
def make_handler(app: TracerApp):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "GSProTracer/1.0"

        def log_message(self, fmt, *args):
            log.debug("HTTP %s - %s", self.address_string(), fmt % args)

        # ---- helpers
        def send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_text(self, text, status=200, ctype="text/plain; charset=utf-8", extra=None):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def read_body(self, limit=1_000_000):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("body too large")
            return self.rfile.read(n) if n else b""

        # ---- GET
        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            try:
                if p in ("/", "/index.html"):
                    with open(VIEWER_PATH, "r", encoding="utf-8") as f:
                        return self.send_text(f.read(), ctype="text/html; charset=utf-8")
                if p in ("/live", "/live.html"):
                    with open(LIVE_PATH, "r", encoding="utf-8") as f:
                        return self.send_text(f.read(), ctype="text/html; charset=utf-8")
                if p == "/api/preview.jpg":
                    app.request_preview()
                    with app.preview_lock:
                        jpeg, at = app.preview_jpeg, app.preview_at
                    if not jpeg or time.time() - at > 3.0:
                        return self.send_text("kein Live-Bild", 503)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpeg)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Frame-Age", f"{time.time() - at:.3f}")
                    self.send_header("X-Preview-Fps", str(app.preview_fps()))
                    self.end_headers()
                    self.wfile.write(jpeg)
                    return
                if p == "/api/status":
                    return self.send_json(app.status())
                if p == "/api/shots":
                    return self.send_json(app.store.list())
                if p == "/api/calibration":
                    return self.send_json(app.calibration())
                if p == "/api/export.csv":
                    return self.send_csv()
                m = re.match(r"^/shots/([A-Za-z0-9_-]+)/clip\.mp4$", p)
                if m:
                    return self.send_video(app.store.path(m.group(1), "clip.mp4"))
                m = re.match(r"^/api/shots/([A-Za-z0-9_-]+)/export\.mp4$", p)
                if m:
                    ex = app.store.read_json(m.group(1), "export.json") or {}
                    if not ex.get("file"):
                        return self.send_text("noch kein Export", 404)
                    return self.send_video(os.path.join(app.exporter.out_dir, ex["file"]), download_name=ex["file"])
                self.send_text("not found", 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except ValueError as e:
                self.send_text(str(e), 400)

        def send_video(self, path, download_name=None):
            if not os.path.exists(path):
                return self.send_text("no video", 404)
            size = os.path.getsize(path)
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            status = 200
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                if m:
                    if m.group(1):
                        start = int(m.group(1))
                        if m.group(2):
                            end = min(int(m.group(2)), size - 1)
                    elif m.group(2):
                        start = max(0, size - int(m.group(2)))
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            if download_name:
                self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def send_csv(self):
            out = io.StringIO()
            w = csv.writer(out, delimiter=";")
            cols_ball = ["Speed", "VLA", "HLA", "TotalSpin", "SpinAxis", "Backspin", "SideSpin"]
            cols_club = ["Speed", "AngleOfAttack", "FaceToTarget", "Path", "HorizontalFaceImpact"]
            names = {"Speed": "Speed_kmh"}
            w.writerow(["shot_id", "zeit", "schlaeger", "video", "smash"]
                       + [f"ball_{names.get(c, c)}" for c in cols_ball]
                       + [f"club_{names.get(c, c)}" for c in cols_club] + ["notiz"])

            def val(d, c):
                v = d.get(c, "")
                if c == "Speed" and isinstance(v, (int, float)):
                    v = round(v * MPH_TO_KMH, 1)
                return str(v).replace(".", ",")
            for s in app.store.list():
                rec = s["shot"] or {}
                pl = rec.get("payload") or {}
                bd, cd = pl.get("BallData") or {}, pl.get("ClubData") or {}
                bs, cs = bd.get("Speed"), cd.get("Speed")
                smash = f"{bs / cs:.2f}".replace(".", ",") if isinstance(bs, (int, float)) and isinstance(cs, (int, float)) and cs else ""
                w.writerow([s["id"], rec.get("received_at", ""), rec.get("club") or "",
                            "ja" if s["has_video"] else "nein", smash]
                           + [val(bd, c) for c in cols_ball]
                           + [val(cd, c) for c in cols_club]
                           + [s["notes"].get("text", "")])
            self.send_text("﻿" + out.getvalue(), ctype="text/csv; charset=utf-8",
                           extra={"Content-Disposition": 'attachment; filename="gspro_shots.csv"'})

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/api/upload":
                    return self.handle_upload(q)
                if u.path == "/api/preview":
                    app.store_preview(self.read_body(limit=3_000_000))
                    return self.send_json({"ok": True})
                if u.path == "/api/trigger":
                    self.read_body()
                    sid = app.handle_shot({}, test=True)
                    return self.send_json({"shot_id": sid})
                if u.path == "/api/config":
                    before = app.cfg.data.get("clip_orientation")
                    app.cfg.update(json.loads(self.read_body() or b"{}"))
                    if app.cfg.data.get("clip_orientation") != before:
                        threading.Thread(target=app.reorient_all, args=(True,), daemon=True).start()
                    return self.send_json(app.cfg.data)
                m = re.match(r"^/api/shots/([A-Za-z0-9_-]+)/export$", u.path)
                if m:
                    self.read_body()
                    if not app.exporter.ffmpeg:
                        return self.send_text("FFmpeg fehlt", 503)
                    app.exporter.enqueue(m.group(1))
                    return self.send_json({"queued": True})
                m = re.match(r"^/api/shots/([A-Za-z0-9_-]+)/notes$", u.path)
                if m:
                    sid = m.group(1)
                    if not app.store.get(sid):
                        return self.send_text("unknown shot", 404)
                    notes = app.store.read_json(sid, "notes.json") or {}
                    upd = json.loads(self.read_body() or b"{}")
                    notes.update(upd)
                    notes = {k: v for k, v in notes.items() if v is not None}
                    app.store.write_json(sid, "notes.json", notes)
                    if "lines" in upd:          # Linien geaendert -> Video mit Daten neu erstellen
                        app.exporter.enqueue(sid)
                    return self.send_json(notes)
                self.send_text("not found", 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, json.JSONDecodeError) as e:
                self.send_text(str(e), 400)

        def handle_upload(self, q):
            sid = q.get("shot", "")
            if not SHOT_ID_RE.match(sid):
                return self.send_text("bad shot id", 400)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2_000_000_000:
                return self.send_text("bad length", 400)
            os.makedirs(app.store.path(sid), exist_ok=True)
            tmp = app.store.path(sid, "clip.mp4.part")
            remaining = length
            with open(tmp, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                os.remove(tmp)
                return self.send_text("incomplete", 400)
            os.replace(tmp, app.store.path(sid, "clip.mp4"))

            def f(key, default=None):
                try:
                    return float(q[key])
                except (KeyError, ValueError):
                    return default

            meta = {
                "uploaded_at": datetime.now().isoformat(timespec="milliseconds"),
                "bytes": length,
                "fps": f("fps"),
                "width": int(f("w", 0)),
                "height": int(f("h", 0)),
                "duration": f("dur"),
                "trigger_time": f("trigger"),   # Sekunde im Clip, an der der Trigger ankam
                "delay_used": f("delay", 0.0),
                "device": q.get("device", ""),
            }
            app.store.write_json(sid, "video.json", meta)
            if not app.store.read_json(sid, "shot.json"):
                app.store.write_json(sid, "shot.json", {"received_at": meta["uploaded_at"],
                                                        "test": True, "payload": {}})
            # Handy liegt quer, Clip kam hochkant -> verlustfrei ins Querformat drehen (clip_orientation)
            try:
                from exporter import fix_orientation
                new = fix_orientation(app.exporter.ffmpeg, app.store.path(sid, "clip.mp4"),
                                      app.cfg.data.get("clip_orientation", "quer_180"))
                if new:
                    log.info("Clip %s gedreht (%s, %dx%d)", sid, app.cfg.data.get("clip_orientation"), new[0], new[1])
                    meta["width"], meta["height"] = new
                    app.store.write_json(sid, "video.json", meta)
            except Exception:
                log.exception("Drehen ins Querformat fehlgeschlagen")
            fmt = "hochkant" if meta["height"] > meta["width"] else "quer"
            log.info("Video empfangen: %s (%.1f MB, %s fps, %dx%d %s)", sid, length / 1e6, meta["fps"],
                     meta["width"], meta["height"], fmt)
            if fmt == "hochkant":
                log.warning("Clip ist HOCHKANT - in der App 'Format: quer' waehlen (oder App updaten), "
                            "oder in config.json clip_orientation pruefen")
            app.exporter.enqueue(sid)
            self.send_json({"ok": True})

        # ---- DELETE
        def do_DELETE(self):
            if urlparse(self.path).path == "/api/shots":
                # alle Schuesse loeschen (fertige Videos in exports\ bleiben)
                done, failed = 0, 0
                for s in app.store.list():
                    if s["id"] == app.exporter.busy:
                        failed += 1          # wird gerade exportiert -> Dateien sind offen
                        continue
                    try:
                        app.store.delete(s["id"])
                        done += 1
                    except OSError:
                        failed += 1
                log.info("Alle Schuesse geloescht: %d (nicht geloescht: %d)", done, failed)
                return self.send_json({"ok": True, "deleted": done, "failed": failed})
            m = re.match(r"^/api/shots/([A-Za-z0-9_-]+)$", urlparse(self.path).path)
            if not m:
                return self.send_text("not found", 404)
            app.store.delete(m.group(1))
            self.send_json({"ok": True})

    return Handler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    TracerApp().run()
