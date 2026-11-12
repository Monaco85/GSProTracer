"""
Export: Clip + eingeblendete Schlagdaten (km/h) als fertiges MP4 in pc\\exports\\.

Laeuft im Hintergrund, sobald ein Clip vom Handy angekommen ist. Das Video wird als Zeitlupe
gerendert, sodass jedes 240-fps-Bild einzeln sichtbar ist (240 fps -> 30 fps = 1/8 Tempo).
Benoetigt FFmpeg (winget install Gyan.FFmpeg).
"""

import glob
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
from datetime import datetime

log = logging.getLogger("tracer.export")

MPH_TO_KMH = 1.609344
FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"


def find_ffmpeg(configured=""):
    cands = [configured] if configured else []
    w = shutil.which("ffmpeg")
    if w:
        cands.append(w)
    local = os.environ.get("LOCALAPPDATA", "")
    cands += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe"))
    cands += [r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _num(v, digits=1):
    if v is None or v == "":
        return None
    try:
        return f"{float(v):.{digits}f}".replace(".", ",")
    except (TypeError, ValueError):
        return None


def _deg(v, lr=False):
    s = _num(v)
    if s is None:
        return "–"
    if lr and float(v) != 0:
        return f"{_num(abs(float(v)))}° {'R' if float(v) > 0 else 'L'}"
    return s + "°"


def shot_rows(payload):
    """Werte fuer die Einblendung (links/rechts je Liste von (Bezeichnung, Wert))."""
    b = (payload or {}).get("BallData") or {}
    c = (payload or {}).get("ClubData") or {}
    bs, cs = b.get("Speed"), c.get("Speed")
    kmh = lambda v: f"{_num(float(v) * MPH_TO_KMH)} km/h" if isinstance(v, (int, float)) and v else "–"
    smash = _num(bs / cs, 2) if isinstance(bs, (int, float)) and isinstance(cs, (int, float)) and cs else None
    spin = b.get("TotalSpin")
    left = [
        ("Ballgeschw.", kmh(bs)),
        ("Schlägerkopf", kmh(cs)),
        ("Smash", smash or "–"),
        ("Abflugwinkel", _deg(b.get("VLA"))),
        ("Richtung", _deg(b.get("HLA"), lr=True)),
        ("Spin", f"{int(round(spin))} rpm" if isinstance(spin, (int, float)) else "–"),
    ]
    right = [
        ("Spinachse", _deg(b.get("SpinAxis"))),
        ("AoA", _deg(c.get("AngleOfAttack"))),
        ("Club Path", _deg(c.get("Path"))),
        ("Face/Target", _deg(c.get("FaceToTarget"))),
        ("Face/Path", _deg(c.get("HorizontalFaceImpact"))),
    ]
    return left, right


def export_name(shot_id, record):
    try:
        ts = datetime.fromisoformat(record.get("received_at", ""))
    except (TypeError, ValueError):
        ts = datetime.now()
    parts = [ts.strftime("%Y-%m-%d_%H-%M-%S")]
    if record.get("test"):
        parts.append("Testclip")
    else:
        if record.get("club"):
            parts.append(str(record["club"]))
        bs = ((record.get("payload") or {}).get("BallData") or {}).get("Speed")
        if isinstance(bs, (int, float)) and bs:
            parts.append(f"{bs * MPH_TO_KMH:.0f}kmh")
    name = "_".join(parts)
    return "".join(ch for ch in name if ch.isalnum() or ch in "-_") + ".mp4"


def _esc(path):
    # Pfad fuer FFmpeg-Filter: Doppelpunkt maskieren (C\:/Windows/...)
    return path.replace("\\", "/").replace(":", "\\:")


class Exporter:
    def __init__(self, store, cfg):
        self.store = store
        self.cfg = cfg
        self.q = queue.Queue()
        self.ffmpeg = find_ffmpeg(cfg.data.get("ffmpeg_path", ""))
        self.busy = None
        if self.ffmpeg:
            log.info("FFmpeg: %s", self.ffmpeg)
        else:
            log.warning("FFmpeg nicht gefunden - kein Video-Export mit Schlagdaten (winget install Gyan.FFmpeg)")

    @property
    def out_dir(self):
        d = self.cfg.data.get("exports_dir", "exports")
        base = os.path.dirname(os.path.abspath(__file__))
        return d if os.path.isabs(d) else os.path.join(base, d)

    def start(self):
        threading.Thread(target=self._worker, daemon=True, name="exporter").start()
        # Nachholen: Clips ohne Export (z.B. vor dem Update aufgenommen)
        for s in self.store.list():
            if s["has_video"] and not s.get("export"):
                self.enqueue(s["id"])

    def enqueue(self, shot_id):
        if self.ffmpeg:
            self.q.put(shot_id)

    def _worker(self):
        while True:
            sid = self.q.get()
            self.busy = sid
            try:
                self.export(sid)
            except Exception as e:
                log.exception("Export %s fehlgeschlagen", sid)
                try:
                    self.store.write_json(sid, "export.json", {"error": str(e)[:500]})
                except Exception:
                    pass
            finally:
                self.busy = None

    def export(self, sid):
        shot_dir = self.store.path(sid)
        clip = os.path.join(shot_dir, "clip.mp4")
        if not os.path.exists(clip):
            return
        record = self.store.read_json(sid, "shot.json") or {}
        video = self.store.read_json(sid, "video.json") or {}
        fps = float(video.get("fps") or 240)
        w, h = int(video.get("width") or 1280), int(video.get("height") or 720)
        play_fps = float(self.cfg.data.get("export_playback_fps", 30))
        slow = max(1.0, fps / play_fps)

        # Text-Dateien (UTF-8 ohne BOM) - so muss im Filter nichts maskiert werden
        left, right = shot_rows(record.get("payload"))
        try:
            ts = datetime.fromisoformat(record.get("received_at", "")).strftime("%d.%m.%Y  %H:%M:%S")
        except (TypeError, ValueError):
            ts = ""
        head = " · ".join(x for x in [str(record.get("club") or ""), "Testclip" if record.get("test") else "", ts] if x)
        files = {
            "_ov_head.txt": head or "GSPro Tracer",
            "_ov_l1.txt": "\n".join(r[0] for r in left),
            "_ov_v1.txt": "\n".join(r[1] for r in left),
            "_ov_l2.txt": "\n".join(r[0] for r in right),
            "_ov_v2.txt": "\n".join(r[1] for r in right),
            "_ov_tag.txt": f"Zeitlupe 1/{slow:g}" if slow > 1 else "Echtzeit",
        }
        for name, text in files.items():
            with open(os.path.join(shot_dir, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

        # hochkant: Panel darf hoeher sein; quer: kompakter, damit der Schwung frei bleibt
        fs = int(round(w * 0.034)) if h > w else int(round(h * 0.03))
        hs = int(round(fs * 1.25))
        lh = int(round(fs * 0.45))           # Zeilenabstand
        pad = int(round(fs * 0.8))
        rows = max(len(left), len(right))
        panel_h = pad + hs + pad // 2 + rows * (fs + lh) + pad
        x1, x2 = pad, int(w * 0.29)
        x3, x4 = int(w * 0.54), int(w * 0.79)
        top = f"h-{panel_h}+{pad}"
        body = f"h-{panel_h}+{pad}+{hs}+{pad // 2}"
        fr, fb = _esc(FONT_REGULAR), _esc(FONT_BOLD)

        def dt(file, font, size, x, y, color="white"):
            return (f"drawtext=fontfile='{font}':textfile={file}:fontcolor={color}:fontsize={size}"
                    f":x={x}:y={y}:line_spacing={lh}")

        vf = ",".join([
            f"drawbox=x=0:y=ih-{panel_h}:w=iw:h={panel_h}:color=black@0.62:t=fill",
            dt("_ov_head.txt", fb, hs, x1, top, "0xFFEB3B"),
            dt("_ov_l1.txt", fr, fs, x1, body, "0xB0BEC5"),
            dt("_ov_v1.txt", fb, fs, x2, body),
            dt("_ov_l2.txt", fr, fs, x3, body, "0xB0BEC5"),
            dt("_ov_v2.txt", fb, fs, x4, body),
            f"drawbox=x=iw-{int(fs * 7.2)}:y={pad // 2}:w={int(fs * 6.8)}:h={int(fs * 1.6)}:color=black@0.5:t=fill",
            dt("_ov_tag.txt", fr, fs, f"w-{int(fs * 6.8)}", int(pad // 2 + fs * 0.3)),
            f"setpts={slow:g}*PTS",
        ])
        tmp = os.path.join(shot_dir, "_export.tmp.mp4")
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", "clip.mp4",
               "-vf", vf, "-r", f"{play_fps:g}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", os.path.basename(tmp)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(cmd, cwd=shot_dir, capture_output=True, text=True, timeout=600, creationflags=flags)
        for name in files:
            try:
                os.remove(os.path.join(shot_dir, name))
            except OSError:
                pass
        if res.returncode != 0 or not os.path.exists(tmp):
            raise RuntimeError((res.stderr or "ffmpeg-Fehler").strip()[-500:])

        os.makedirs(self.out_dir, exist_ok=True)
        old = (self.store.read_json(sid, "export.json") or {}).get("file")
        if old and os.path.exists(os.path.join(self.out_dir, old)):
            os.remove(os.path.join(self.out_dir, old))
        name = export_name(sid, record)
        os.replace(tmp, os.path.join(self.out_dir, name))
        self.store.write_json(sid, "export.json", {
            "file": name, "created_at": datetime.now().isoformat(timespec="seconds"),
            "slowmo": slow, "fps": play_fps,
        })
        log.info("Export gespeichert: %s", os.path.join(self.out_dir, name))
