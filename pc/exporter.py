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
    # Der R10-Connector liefert Face-to-Path nicht (immer 0) -> aus Face to Target - Path berechnen
    ftp = c.get("HorizontalFaceImpact")
    if (not ftp) and isinstance(c.get("FaceToTarget"), (int, float)) and isinstance(c.get("Path"), (int, float)):
        ftp = c["FaceToTarget"] - c["Path"]
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
        ("Face/Path", _deg(ftp)),
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

    def _auto_analyze(self, sid, clip, notes, video):
        try:
            import swing_analysis
        except ImportError:
            return notes
        if not swing_analysis.AVAILABLE:
            log.info("Automatische Linien aus: OpenCV fehlt (pip install opencv-python-headless numpy)")
            return notes
        res = swing_analysis.analyze(clip, (video or {}).get("trigger_time"))
        notes = self.store.read_json(sid, "notes.json") or {}   # frisch lesen (Viewer koennte geschrieben haben)
        notes["analysis"] = {k: v for k, v in res.items() if k != "lines"}
        if "error" in res:
            log.info("Analyse %s: %s", sid, res["error"])
        else:
            # eigene (von Hand gezeichnete) Linien behalten, gleiche Art wird nicht ueberschrieben
            manual = [l for l in notes.get("lines") or [] if not l.get("auto")]
            have = {l.get("label") for l in manual}
            notes["lines"] = manual + [l for l in res.get("lines", []) if l["label"] not in have]
            if notes.get("impact_time") is None:
                notes["impact_time"] = res["impact_time"]
            notes["swing"] = {"start": res["swing_start"], "end": res["swing_end"]}
            log.info("Analyse %s: Treffmoment %.3f s, Linien: %s", sid, res["impact_time"],
                     ", ".join(l["label"] for l in res.get("lines", [])) or "keine")
        self.store.write_json(sid, "notes.json", notes)
        return notes

    def _line_overlays(self, lines, w, h, shot_dir, fs, files, font_bold, t0=0.0):
        """
        Erzeugt pro Linie ein transparentes PNG (per FFmpeg geq) und die Beschriftung.
        Linie: {"x1","y1","x2","y2"} normiert 0..1 (Punkt 1 = Schlaegerkopf, Punkt 2 = Richtung Griff),
        "color" "#rrggbb", "label", "from" (Sekunde im Clip, ab der sie sichtbar ist), "extend" (bis Bildrand).
        Rueckgabe: ({png: from}, [drawtext-Filter fuer Beschriftungen])
        """
        import math
        inputs, filters = {}, []
        fr = _esc(FONT_REGULAR)
        for i, ln in enumerate(lines):
            try:
                X1, Y1 = float(ln["x1"]) * w, float(ln["y1"]) * h
                X2, Y2 = float(ln["x2"]) * w, float(ln["y2"]) * h
            except (KeyError, TypeError, ValueError):
                continue
            if abs(X2 - X1) + abs(Y2 - Y1) < 2:
                continue
            col = str(ln.get("color", "#4aa3ff")).lstrip("#")
            r, g, b = int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16)
            A, B, C = Y2 - Y1, X1 - X2, X2 * Y1 - X1 * Y2
            L = math.hypot(A, B)
            ybot = max(Y1, Y2) + 0.02 * h
            ytop = 0 if ln.get("extend", True) else min(Y1, Y2)
            d = f"abs({A:.3f}*X+{B:.3f}*Y+{C:.3f})/{L:.3f}"
            on = f"between(Y,{ytop:.1f},{ybot:.1f})"
            png = f"_ov_line{i}.png"
            geq = (f"r='if(lt({d},2.2),{r},0)':g='if(lt({d},2.2),{g},0)':b='if(lt({d},2.2),{b},0)'"
                   f":a='255*clip(4-{d},0,1)*{on}'")
            res = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                                  "-i", f"color=c=black@0:s={w}x{h},format=rgba", "-vf", f"geq={geq}",
                                  "-frames:v", "1", png], cwd=shot_dir, capture_output=True, text=True,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if res.returncode != 0:
                log.warning("Linie %d: %s", i, res.stderr.strip()[-300:])
                continue
            start = max(0.0, float(ln.get("from", 0) or 0) - t0)   # Zeit im zugeschnittenen Video
            inputs[png] = start
            # Beschriftung mit Winkel zur Horizontalen
            ang = math.degrees(math.atan2(abs(Y2 - Y1), abs(X2 - X1)))
            label = f"{ln.get('label', '')} {ang:.0f}°".strip().replace(".", ",")
            tf = f"_ov_lbl{i}.txt"
            files[tf] = label
            with open(os.path.join(shot_dir, tf), "w", encoding="utf-8", newline="\n") as f:
                f.write(label)
            top_x, top_y = (X2, Y2) if Y2 < Y1 else (X1, Y1)
            lx = int(min(max(top_x + fs * 0.6, 8), w - fs * 9))
            ly = int(min(max(top_y - fs * 1.8 - i * fs * 1.6, 8), h - fs * 2))
            filters.append(
                f"drawtext=fontfile='{font_bold}':textfile={tf}:fontcolor=0x{col}:fontsize={int(fs * 1.1)}"
                f":box=1:boxcolor=black@0.55:boxborderw={int(fs * 0.3)}:x={lx}:y={ly}:enable='gte(t,{start:.4f})'")
        return inputs, filters

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

        # Automatische Analyse (Treffmoment, Ansprechen, Schaftlinien) - einmal pro Clip
        notes = self.store.read_json(sid, "notes.json") or {}
        if self.cfg.data.get("auto_analysis", True) and "analysis" not in notes:
            notes = self._auto_analyze(sid, clip, notes, video)
        sw = notes.get("swing") or {}
        t0 = float(sw.get("start", 0.0)) if self.cfg.data.get("export_trim", True) else 0.0
        t1 = float(sw["end"]) if self.cfg.data.get("export_trim", True) and sw.get("end") else None
        # Linien (z.B. Schaft beim Ansprechen / im Abschwung) aus notes.json
        line_inputs, line_filters = self._line_overlays(notes.get("lines") or [], w, h, shot_dir, fs, files, fb, t0)

        vf = ",".join(line_filters + [
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
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if t0 > 0:
            cmd += ["-ss", f"{t0:.4f}"]
        if t1:
            cmd += ["-t", f"{t1 - t0:.4f}"]
        cmd += ["-i", "clip.mp4"]
        for png in line_inputs:
            cmd += ["-loop", "1", "-i", png]
        if line_inputs:
            # [0:v] -> Linien-Overlays -> Panel/Texte (Overlay braucht filter_complex)
            chain, cur = [], "[0:v]"
            for i, (png, start) in enumerate(line_inputs.items(), start=1):
                chain.append(f"{cur}[{i}:v]overlay=0:0:shortest=1:enable='gte(t,{start:.4f})'[l{i}]")
                cur = f"[l{i}]"
            chain.append(f"{cur}{vf}[out]")
            cmd += ["-filter_complex", ";".join(chain), "-map", "[out]"]
        else:
            cmd += ["-vf", vf]
        crf = str(int(self.cfg.data.get("export_crf", 28)))
        cmd += ["-r", f"{play_fps:g}", "-c:v", "libx264", "-preset", "slow", "-crf", crf,
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", os.path.basename(tmp)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(cmd, cwd=shot_dir, capture_output=True, text=True, timeout=600, creationflags=flags)
        for name in list(files) + list(line_inputs):
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
