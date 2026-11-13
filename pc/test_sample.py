"""
Prueft die automatische Schwung-Analyse am Beispiel-Schlag in samples/ gegen die von Hand gesetzte Referenz.

    python pc\\test_sample.py

Erwartung: Treffmoment und Schaftwinkel liegen nah an der Referenz (samples/<id>/reference.json).
Benoetigt: pip install opencv-python-headless numpy
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import swing_analysis  # noqa: E402

SAMPLES = os.path.join(HERE, "..", "samples")


def angle(l, w, h):
    return math.degrees(math.atan2(abs((l["y2"] - l["y1"]) * h), abs((l["x2"] - l["x1"]) * w)))


def main():
    if not swing_analysis.AVAILABLE:
        print("OpenCV fehlt: pip install opencv-python-headless numpy")
        return 2
    failed = 0
    for sid in sorted(os.listdir(SAMPLES)):
        d = os.path.join(SAMPLES, sid)
        ref_path = os.path.join(d, "reference.json")
        if not os.path.isfile(ref_path):
            continue
        ref = json.load(open(ref_path, encoding="utf-8"))
        video = json.load(open(os.path.join(d, "video.json"), encoding="utf-8"))
        w, h = video["width"], video["height"]
        res = swing_analysis.analyze(os.path.join(d, "clip.mp4"), video.get("trigger_time"))
        print(f"== {sid}")
        if "error" in res:
            print("  FEHLER:", res["error"]); failed += 1; continue
        di = abs(res["impact_time"] - ref["impact_time"])
        ok = di <= 0.02
        failed += not ok
        print(f"  Treffmoment  auto {res['impact_time']:.3f} s  Referenz {ref['impact_time']:.3f} s  "
              f"Abweichung {di * 1000:.0f} ms  {'OK' if ok else 'ZU GROSS'}")
        auto = {l["label"]: l for l in res["lines"]}
        for rl in ref["lines"]:
            al = auto.get(rl["label"])
            if not al:
                print(f"  {rl['label']:<11} nicht erkannt"); failed += 1; continue
            da = abs(angle(al, w, h) - angle(rl, w, h))
            ok = da <= 5
            failed += not ok
            print(f"  {rl['label']:<11} auto {angle(al, w, h):.1f}°  Referenz {angle(rl, w, h):.1f}°  "
                  f"Abweichung {da:.1f}°  {'OK' if ok else 'ZU GROSS'}")
    print("Ergebnis:", "alles OK" if not failed else f"{failed} Abweichung(en)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
