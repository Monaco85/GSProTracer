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
5. Schwungbahn: Schlaegerkopf Bild fuer Bild verfolgen (Schaft per Hough, Kopf = kompakter Fleck am
   Schaftende), beim Ausholen vorwaerts ab dem Ansprechen, im Abschwung rueckwaerts ab dem Treffmoment.

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


def _club_mask(frame, ref_blur):
    f = cv2.GaussianBlur(frame, (5, 5), 0)
    m = (cv2.absdiff(f, ref_blur) > 16).astype(np.uint8) * 255
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def locate_club_head(frame, ref_blur, prev, max_jump, prev_angle=None, reach=0.08):
    """
    Schlaegerkopf in einem Bild finden: Schaft = Hough-Linie im Differenzbild (gegen ein Bild ohne
    Schlaeger), Kopf = kompakter Fleck (bleibt nach Erosion uebrig) am unteren Ende des Schafts,
    sonst das untere Ende der Linie. Gewaehlt wird der Kandidat, der zur vorherigen Position passt.
    Rueckgabe (kopf_x, kopf_y, schaftwinkel, schaft_oben_x, schaft_oben_y) oder None.
    """
    H, W = frame.shape
    D = max(W, H)
    m = _club_mask(frame, ref_blur)
    segs = cv2.HoughLinesP(m, 1, np.pi / 180, threshold=30,
                           minLineLength=int(0.06 * D), maxLineGap=int(0.03 * D))
    if segs is None:
        return None
    k = max(5, int(round(D * 0.0055)) | 1)
    er = cv2.erode(m, np.ones((k, k), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(er)
    blobs = [(cents[j][0], cents[j][1]) for j in range(1, n)
             if stats[j, cv2.CC_STAT_AREA] >= (D * 0.004) ** 2]
    best, best_score = None, 0.0
    for x1, y1, x2, y2 in segs.reshape(-1, 4).tolist():
        if y1 < y2:                                   # Punkt 1 = unten (Kopf), Punkt 2 = oben
            x1, y1, x2, y2 = x2, y2, x1, y1
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 1:
            continue
        ang = math.degrees(math.atan2(y1 - y2, x2 - x1))   # 0..180, Neigung des Schafts
        if prev_angle is not None and abs(ang - prev_angle) > 35:
            continue
        ux, uy = (x1 - x2) / L, (y1 - y2) / L             # Richtung Kopf
        hx, hy = x1, y1
        far = -1.0
        for bx, by in blobs:
            px, py = bx - x2, by - y2
            proj, perp = px * ux + py * uy, abs(-px * uy + py * ux)
            if perp < 0.025 * D and L * 0.75 < proj < L + reach * D and proj > far:
                hx, hy, far = bx, by, proj
        if not (0.015 * W < hx < 0.985 * W and 0.015 * H < hy < 0.985 * H):
            continue                                      # Kopf am Bildrand = schon draussen
        d = math.hypot(hx - prev[0], hy - prev[1])
        if d > max_jump:
            continue
        score = L * (1.6 if far >= 0 else 1.0) / (1.0 + d / (0.03 * D))
        if score > best_score:
            best, best_score = (float(hx), float(hy), ang, float(x2), float(y2)), score
    return best


def _smooth_path(pts, max_dev):
    """Ausreisser entfernen (Abweichung vom Median der Nachbarn) und leicht glaetten."""
    if len(pts) < 3:
        return pts
    keep = []
    for i, p in enumerate(pts):
        nb = pts[max(0, i - 2):i] + pts[i + 1:i + 3]
        mx = float(np.median([q[1] for q in nb]))
        my = float(np.median([q[2] for q in nb]))
        if math.hypot(p[1] - mx, p[2] - my) <= max_dev:
            keep.append(p)
    out = []
    for i, p in enumerate(keep):
        nb = keep[max(0, i - 1):i + 2]
        out.append((p[0], sum(q[1] for q in nb) / len(nb), sum(q[2] for q in nb) / len(nb)) + tuple(p[3:]))
    if keep:                                    # Endpunkte nicht verschieben
        out[0], out[-1] = keep[0], keep[-1]
    return out


def track_club_head(frames, ref, start_pos, indices, fps, max_jump, min_step=0.0, reach=0.08):
    """
    Schlaegerkopf ueber die Bilder 'indices' verfolgen, bis er das Bild verlaesst.
    min_step: Mindestweg pro Bild (Anteil der Bildgroesse) - im Abschwung ist der Kopf schnell,
    langsame Kandidaten sind Koerper/Arme. reach: wie weit hinter dem Schaftende der Kopf liegen darf
    (im Abschwung ist der Schaft unscharf und nur teilweise erkannt).
    Rueckgabe: [(bild, kopf_x, kopf_y, schaft_oben_x, schaft_oben_y), ...]
    """
    D = max(frames[0].shape)
    ref_blur = cv2.GaussianBlur(frames[ref], (5, 5), 0)
    pos, ang, out, miss = start_pos, None, [], 0
    far = 0.0
    for i in indices:
        c = locate_club_head(frames[i], ref_blur, pos, max_jump * (1 + miss), ang, reach)
        if c and out and math.hypot(c[0] - pos[0], c[1] - pos[1]) < min_step * D * (1 + miss):
            c = None
        if c:
            # der Kopf entfernt sich (vom Ball aus gesehen) stetig - sonst ist es Koerper/Rauschen
            dist = math.hypot(c[0] - start_pos[0], c[1] - start_pos[1])
            if (not out and dist > 0.1 * D) or dist < far - 0.025 * D:
                c = None
            else:
                far = max(far, dist)
        if c:
            pos, ang = (c[0], c[1]), c[2]
            out.append((i, c[0], c[1], c[3], c[4]))
            miss = 0
        else:
            miss += 1
            if out and miss > max(2, int(0.03 * fps)):
                break
    return _smooth_path(out, 0.05 * D)


def _head_vector(pts, D):
    """
    Kopfbahn beim Ausholen als ruhige Linie: Zittern beim Ansprechen weglassen (erst ab der ersten echten
    Bewegung) und staerker glaetten. Start- und Endpunkt bleiben, wo sie sind.
    """
    if len(pts) < 3:
        return pts
    x0, y0 = pts[0][1], pts[0][2]
    i = 1
    while i < len(pts) and math.hypot(pts[i][1] - x0, pts[i][2] - y0) < 0.015 * D:
        i += 1
    pts = [pts[0]] + pts[max(1, i - 1):]
    if len(pts) < 5:
        return pts
    out = [pts[0]]
    for k in range(1, len(pts) - 1):
        nb = pts[max(0, k - 2):k + 3]
        out.append((pts[k][0], sum(q[1] for q in nb) / len(nb), sum(q[2] for q in nb) / len(nb)) + tuple(pts[k][3:]))
    out.append(pts[-1])
    return out


def swing_paths(frames, fps, ball, impact, address, e, head):
    """
    Bahn des Schlaegerkopfs beim Ausholen und im Abschwung, soweit er im Bild ist.
    Rueckgabe: Liste von {"label", "color", "points": [[t, kopf_x, kopf_y, schaft_x, schaft_y], ...]}
    mit normierten Koordinaten (0..1) und t in Sekunden im Clip.
    """
    if address is None:
        return []
    ref = find_takeaway_quiet(e, address, impact, fps)
    if ref is None:
        return []
    H, W = frames[0].shape
    D = max(W, H)
    out = []
    bw = track_club_head(frames, ref, head, range(address, ref), fps, 0.06 * D)
    # Treffmoment wird erst erkannt, wenn der Ball schon fliegt -> ein paar Bilder frueher anfangen
    start = impact - max(1, int(round(0.0125 * fps)))
    dw = track_club_head(frames, ref, ball, range(start, ref, -1), fps, 0.18 * D,
                         min_step=0.012, reach=0.35)[::-1]
    if dw and math.hypot(dw[-1][1] - ball[0], dw[-1][2] - ball[1]) < 0.1 * D:
        last = dw[-1]
        dw.append((min(impact, last[0] + 1), float(ball[0]), float(ball[1]), last[3], last[4]))
    bw = _head_vector(bw, D)
    # Ausholen: nur der Schlaegerkopf als Vektor (keine Schaftlinien), Abschwung mit Schaft-Faecher
    for label, color, pts, shafts in (("Ausholen", "#29b6f6", bw, False), ("Abschwung", "#ff7043", dw, True)):
        if len(pts) >= 3:
            out.append({"label": label, "color": color, "shafts": shafts, "points": [
                [round(i / fps, 4), round(x / W, 4), round(y / H, 4), round(sx / W, 4), round(sy / H, 4)]
                for i, x, y, sx, sy in pts]})
    return out


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
    # Schwungbahn: Schlaegerkopf beim Ausholen (ab Ansprechen) und im Abschwung (rueckwaerts vom Treffen)
    try:
        res["paths"] = swing_paths(frames, fps, ball, impact, address, e, head)
    except Exception as ex:  # Bahn ist ein Zusatz - Analyse nicht daran scheitern lassen
        log.warning("Schwungbahn nicht berechnet: %s", ex)
    # Schnittfenster fuer den Export: kurz vor dem Rueckschwung bis kurz nach dem Treffen
    start = (address / fps - 0.25) if address is not None else (impact / fps - 1.6)
    res["swing_start"] = round(max(0.0, start), 4)
    res["swing_end"] = round(min(len(frames) / fps, impact / fps + 0.6), 4)
    return res
