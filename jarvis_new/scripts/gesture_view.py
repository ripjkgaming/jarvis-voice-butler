"""Live debug window for hold-to-mute (src/gestures.py).

Shows the camera (mirrored), the hand skeleton, per-finger extension
ratios vs. thresholds, the pose verdict, debounce timers and the real
mic-mute state. Sends real mute over wake.sock unless --dry.

    .venv/bin/python scripts/gesture_view.py [--dry]      (q / Esc quits)
"""

import argparse
import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import camera_hub  # noqa: E402
import gestures  # noqa: E402

BONES = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),(5,9),(9,10),(10,11),(11,12),
         (9,13),(13,14),(14,15),(15,16),(13,17),(17,18),(18,19),(19,20),(0,17)]
FINGERS = [("INDEX", 8, 6, True), ("MIDDLE", 12, 10, True), ("RING", 16, 14, False), ("PINKY", 20, 18, False)]
GREEN, RED, AMBER, DIM = (80, 255, 120), (80, 80, 255), (0, 190, 255), (150, 150, 150)
W, H, PANEL = 960, 540, 340


def ratio(lm, tip, pip):
    return gestures._d(lm[0], lm[tip]) / max(gestures._d(lm[0], lm[pip]), 1e-6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="never send mute")
    dry = ap.parse_args().dry
    rec = gestures.make_recognizer()
    if rec is None:
        sys.exit("no gesture_recognizer.task model")
    sent_log: list[str] = []
    state = {"muted": False}

    def sender(msg):
        sent_log.append(f"{time.strftime('%H:%M:%S')} send {msg}")
        if dry:
            state["muted"] = msg.get("mute", state["muted"])
            return {"ok": True}
        reply = gestures.send_wake(msg)
        if reply and reply.get("ok"):
            state["muted"] = msg.get("mute", state["muted"])
        return reply

    mon = gestures.GestureMonitor(recognize=lambda f: None, sender=sender,
                                  speaker=lambda *a: None)
    cv2.namedWindow("JARVIS // HAND-MUTE", cv2.WINDOW_AUTOSIZE)
    while True:
        camera_hub.request("gestureview", 10.0, fps=12.0)
        frame = camera_hub.latest_frame()
        canvas = np.zeros((H, W + PANEL, 3), np.uint8)
        lm, g, mpose = None, None, False
        if frame is not None:
            img = mp.Image(image_format=mp.ImageFormat.SRGB,
                           data=np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            res = rec.recognize(img)
            g = gestures.top_gesture(res)
            if res.hand_landmarks:
                lm = res.hand_landmarks[0]
                mpose = gestures.is_mute_pose(lm)
            name = gestures.MUTE_POSE if mpose else None
            mon._hold_mute(name is not None, time.time())
            view = cv2.flip(cv2.resize(frame, (W, H)), 1)
            if lm:
                pts = [(int((1 - p.x) * W), int(p.y * H)) for p in lm]
                col = GREEN if mpose else AMBER
                for a, b in BONES:
                    cv2.line(view, pts[a], pts[b], col, 2)
                for i, p in enumerate(pts):
                    cv2.circle(view, p, 5 if i in (8, 12, 16, 20) else 3, col, -1)
            canvas[:, :W] = view
        else:
            cv2.putText(canvas, "NO FRAME (camera hub idle?)", (30, 60), 0, 0.8, RED, 2)
            mon._hold_mute(False, time.time())

        x, y = W + 16, 36
        def put(t, c=(220, 220, 220), s=0.55, dy=26):
            nonlocal y
            cv2.putText(canvas, t, (x, y), cv2.FONT_HERSHEY_SIMPLEX, s, c, 1, cv2.LINE_AA)
            y += dy
        put("HAND-MUTE DEBUG" + ("  [DRY]" if dry else ""), AMBER, 0.65, 34)
        put(f"hand seen : {'YES' if lm else 'no'}", GREEN if lm else DIM)
        put(f"mp gesture: {g or '-'}")
        y += 6
        put("finger  ratio  need  state", DIM, 0.5)
        for nm, tip, pip, want_up in FINGERS:
            if lm:
                r = ratio(lm, tip, pip)
                ok = r > 1.15 if want_up else r < 1.0
                put(f"{nm:<7} {r:5.2f}  {'>1.15' if want_up else '<1.00'}  "
                    f"{'UP' if r > 1.15 else 'curl' if r < 1.0 else '??'} {'OK' if ok else 'x'}",
                    GREEN if ok else RED)
            else:
                put(f"{nm:<7}   -", DIM)
        y += 8
        put(f"POSE: {'MUTE SIGN' if mpose else 'none'}", GREEN if mpose else DIM, 0.8, 36)
        now = time.time()
        if mon._pose_since is not None and not mon._holding_mute:
            put(f"arming {now - mon._pose_since:.2f}/{gestures.MUTE_ON_S}s", AMBER)
        if mon._holding_mute and mon._pose_gone is not None:
            put(f"releasing {now - mon._pose_gone:.2f}/{gestures.MUTE_OFF_S}s", AMBER)
        y += 10
        put("MIC: MUTED" if mon._holding_mute else "MIC: LIVE",
            RED if mon._holding_mute else GREEN, 0.9, 40)
        y += 10
        put("log", DIM, 0.5)
        for line in sent_log[-8:]:
            put(line, DIM, 0.45, 20)
        cv2.imshow("JARVIS // HAND-MUTE", canvas)
        if cv2.waitKey(60) & 0xFF in (27, ord("q")):
            break
    t = time.time()
    mon._hold_mute(False, t)
    mon._hold_mute(False, t + 10)  # release: never leave mic muted
    camera_hub.release("gestureview")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
