"""Permissioned configured-provider smoke on generated synthetic images only."""

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-calls", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--timeout-s", type=int, default=20, choices=(20, 45))
    options = parser.parse_args()
    from dotenv import load_dotenv
    from PIL import Image, ImageDraw, ImageFont

    from computer_watch import parse_observation
    from watch_visual import grounded_state, infer_gemini

    load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")
    phases = [
        ("running", "Build running"),
        ("input_needed", "Input needed: confirm build"),
        ("completed", "Build finished successfully"),
    ]
    output = []
    font = ImageFont.truetype("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", 24)
    for expected, text in phases[: options.max_calls]:
        frame = Image.new("RGB", (640, 300), "white")
        draw = ImageDraw.Draw(frame)
        draw.text((25, 30), "JARVIS OWNED SYNTHETIC TEST", fill="black", font=font)
        draw.text((25, 95), text, fill="black", font=font)
        draw.text((25, 155), "This is synthetic test content.", fill="black", font=font)
        payload = io.BytesIO()
        frame.save(payload, format="PNG")
        try:
            result = parse_observation(
                infer_gemini(
                    payload.getvalue(),
                    text,
                    "Observe only this synthetic build task. Report the current visible state.",
                    lambda: None,
                    timeout_s=options.timeout_s,
                )
            )
            grounded = result[
                "evidence"
            ].casefold() in text.casefold() and grounded_state(
                result["state"], result["evidence"]
            )
            output.append(
                {
                    "expected": expected,
                    "observed": result["state"],
                    "grounded": grounded,
                }
            )
            print(output[-1], flush=True)
        except Exception as error:
            print(
                {
                    "expected": expected,
                    "error_type": type(error).__name__,
                    "code": getattr(error, "code", None),
                },
                flush=True,
            )
            return 1
        finally:
            frame = payload = result = None
    return (
        0
        if all(
            item["expected"] == item["observed"] and item["grounded"] for item in output
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
