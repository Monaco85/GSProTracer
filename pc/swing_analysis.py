"""
Automatische Schwung-Analyse (OpenCV): Treffmoment, Ansprechen und Schaftlinien.

Ablauf:
1. Treffmoment: Der Ball ist der hellste Punkt im unteren Bildbereich. Wird er getroffen,
   faellt die Helligkeit dort schlagartig ab und bleibt unten.
2. Ballposition: hellster Fleck kurz vor dem Treffmoment (waehrend der Ball ruhig liegt).
3. Bewegung um den Ball: ruhig (Ansprechen) -> Bewegung (Rueckschwung beginnt) -> ruhig
   (Schlaeger oben) -> Bewegung (Abschwung) -> Treffmoment. Ansprechen = Bild vor dem Rueckschwung.
4. Schaft: Differenzbild (Bild mit Schlaeger minus Bild ohne Schlaeger in Ballnaehe) + Hough-Linien.
   Gewaehlt wird die laengste Linie, die zum Schlaegerkopf neben dem Ball zeigt.

Benoetigt: pip install opencv-python-headless numpy
"""

import logging
import math

log = logging.getLogger("tracer.analysis")

try:
    import cv2
    import numpy as np
    AVAILABLE = True
except ImportError:  # Analyse ist optional
    AVAILABLE = False


def _read_gray(path, max_frames=2400):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 240.0
    frames = []
    while len(frames) < max_frames:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
    cap.release()
    return frames, fps


def _smooth(a, k):
    k = max(1, int(k))
    return np.convolve(a, np.ones(k) / k, mode="same")


def find_impact(frames, fps, lower=0.5):
    """Index des ersten Bilds nach dem Treffen (Ball weg) und Ballposition."""
    H = frames[0].shape[0]
    y0 = int(H * lower)
    m = np.array([cv2.GaussianBlur(f[y0:], (7, 7), 0).max() for f in frames], dtype=float)
    n = len(m)
    win, hold = int(0.25 * fps), int(0.12 * fps)
    best = None
    for i in range(win, n - hold):
        before = np.median(m[i - win:i])
        if before < 90:                       # vorher kein heller Ball
            continue
        if m[i] < 0.62 * before and np.max(m[i:i + hold]) < 0.75 * before:
            best = i
            break
    if best is None:
        return None, None
    # Ball: hellster Punkt ~0,1 s vor dem Treffen (Schlaeger noch nicht ganz unten)
    ref = frames[max(0, best - int(0.1 * fps))][y0:]
    _, _, _, loc = cv2.minMaxLoc(cv2.GaussianBlur(ref, (9, 9), 0))
    return best, (loc[0], loc[1] + y0)


def motion_curve(frames, ball, fps, box=(220, 180)):
    """Bewegungsenergie in einem Fenster um den Ball (Differenz zu 4 Bilder vorher)."""
    H, W = frames[0].shape
    bx, by = ball
    x0, x1 = max(0, bx - box[0] // 2), min(W, bx + box[0] // 2)
    y0, y1 = max(0, by - box[1]), min(H, by + box[1] // 3)
    d = int(max(1, round(fps / 60)))
    e = np.zeros(len(frames))
    for i in range(d, len(frames)):
        a = frames[i][y0:y1, x0:x1].astype(np.int16)
        b = frames[i - d][y0:y1, x0:x1].astype(np.int16)
        e[i] = float(np.mean(np.abs(a - b) > 18))
    return _smooth(e, fps / 40)


def find_address(e, impact, fps):
    """Letztes ruhiges Bild vor dem Rueckschwung (vom Treffmoment rueckwaerts gesucht)."""
    quiet = max(0.004, float(np.percentile(e[:impact], 30)) * 2.5)
    i = impact - int(0.05 * fps)
    # 1) Abschwung-Bewegung rueckwaerts ueberspringen
    while i > 0 and e[i] > quiet:
        i -= 1
    # 2) ruhige Phase (Schlaeger oben / ausserhalb)
    while i > 0 and e[i] <= quiet:
        i -= 1
    # 3) Rueckschwung-Bewegung (Takeaway)
    if i <= 0:
        return None
    while i > 0 and e[i] > quiet:
        i -= 1
    if i <= int(0.05 * fps):
        return None  # Clip beginnt erst waehrend des Rueckschwungs
    return max(0, i - int(0.04 * fps))


def find_takeaway_quiet(e, address, impact, fps):
    """Ein Bild aus der ruhigen Phase zwischen Rueckschwung und Abschwung (Referenz ohne Schlaeger)."""
    q = max(0.004, float(np.percentile(e[:impact], 30)) * 2.5)
    seg = [i for i in range(address + int(0.15 * fps), impact - int(0.08 * fps)) if e[i] <= q]
    return seg[len(seg) // 2] if seg else None


def detect_shaft(frame, ref, ball, head_hint=None):
    """Schaftlinie im Bild 'frame' (mit Schlaeger) gegen 'ref' (ohne Schlaeger in Ballnaehe)."""
    H, W = frame.shape
    bx, by = ball
    diff = cv2.absdiff(cv2.GaussianBlur(frame, (3, 3), 0), cv2.GaussianBlur(ref, (3, 3), 0))
    _, mask = cv2.threshold(diff, 14, 255, cv2.THRESH_BINARY)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    edges = cv2.Canny(cv2.GaussianBlur(frame, (3, 3), 0), 25, 75)
    edges = cv2.bitwise_and(edges, mask)
    edges[int(by + 0.06 * H):, :] = 0                      # unterhalb des Balls kein Schaft
    segs = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=40,
                           minLineLength=int(0.07 * H), maxLineGap=int(0.03 * H))
    if segs is None:
        return None
    hx, hy = head_hint or (bx, by)
    best, best_score = None, 0.0
    for x1, y1, x2, y2 in segs.reshape(-1, 4).tolist():
        if y1 < y2:                                          # Punkt 1 = unten (Kopf), Punkt 2 = oben
            x1, y1, x2, y2 = x2, y2, x1, y1
        length = math.hypot(x2 - x1, y2 - y1)
        ang = math.degrees(math.atan2(y1 - y2, abs(x2 - x1)))
        if not 25 <= ang <= 88:
            continue
        # Abstand der verlaengerten Linie zum Schlaegerkopf (neben dem Ball)
        A, B, C = y2 - y1, x1 - x2, x2 * y1 - x1 * y2
        dist = abs(A * hx + B * hy + C) / max(1e-6, math.hypot(A, B))
        # Schlaegerkopf liegt neben/hinter dem Ball, nicht exakt auf dem Ballmittelpunkt
        if dist > 0.10 * W or y1 > hy + 0.05 * H:
            continue
        score = length / (1.0 + dist / 25.0)
        if score > best_score:
            best, best_score = (x1, y1, x2, y2), score
    if not best:
        return None
    x1, y1, x2, y2 = best
    # unteres Ende bis auf Hoehe des Schlaegerkopfs verlaengern
    if y1 != y2:
        t = (hy - y1) / (y2 - y1)
        x1, y1 = x1 + (x2 - x1) * t, hy
    return float(x1), float(y1), float(x2), float(y2)


def analyze(path, trigger_time=None):
    """
    Liefert dict mit impact_time, address_time, swing_start, lines (normiert, wie notes.json) oder
    {"error": ...}. Zeiten in Sekunden im Clip.
    """
    if not AVAILABLE:
        return {"error": "OpenCV fehlt (pip install opencv-python-headless numpy)"}
    frames, fps = _read_gray(path)
    if len(frames) < fps * 0.8:
        return {"error": "Clip zu kurz"}
    H, W = frames[0].shape
    impact, ball = find_impact(frames, fps)
    if impact is None:
        return {"error": "Treffmoment nicht erkannt (Ball nicht sichtbar?)"}
    res = {"impact_time": round(impact / fps, 4), "ball": [ball[0] / W, ball[1] / H], "lines": []}
    e = motion_curve(frames, ball, fps)
    address = find_address(e, impact, fps)
    head = (ball[0], ball[1])
    if address is not None:
        res["address_time"] = round(address / fps, 4)
        ref = find_takeaway_quiet(e, address, impact, fps)
        if ref is not None:
            # mehrere ruhige Bilder vor dem Rueckschwung probieren
            shaft = None
            for back in (0.0, 0.1, 0.25, 0.5):
                fi = address - int(back * fps)
                if fi < 0:
                    break
                shaft = detect_shaft(frames[fi], frames[ref], ball, head)
                if shaft:
                    address = fi
                    break
            if shaft:
                x1, y1, x2, y2 = shaft
                res["lines"].append({"x1": x1 / W, "y1": y1 / H, "x2": x2 / W, "y2": y2 / H,
                                     "color": "#4aa3ff", "label": "Ansprechen", "from": 0.0,
                                     "extend": True, "auto": True, "frame_time": round(address / fps, 4)})
                head = (x1, y1)
    # Abschwung: einige Bilder vor dem Treffen, Referenz ~0,2 s frueher
    for k in (4, 3, 5, 6, 2, 8):
        d = impact - k
        r = impact - int(0.2 * fps)
        if r < 0:
            break
        shaft = detect_shaft(frames[d], frames[r], ball, head)
        if shaft:
            x1, y1, x2, y2 = shaft
            res["lines"].append({"x1": x1 / W, "y1": y1 / H, "x2": x2 / W, "y2": y2 / H,
                                 "color": "#ff9800", "label": "Abschwung", "from": round(d / fps, 4),
                                 "extend": True, "auto": True, "frame_time": round(d / fps, 4)})
            break
    # Schnittfenster fuer den Export: kurz vor dem Rueckschwung bis kurz nach dem Treffen
    start = (address / fps - 0.25) if address is not None else (impact / fps - 1.6)
    res["swing_start"] = round(max(0.0, start), 4)
    res["swing_end"] = round(min(len(frames) / fps, impact / fps + 0.6), 4)
    return res
