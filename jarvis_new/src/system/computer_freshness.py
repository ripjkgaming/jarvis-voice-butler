"""Pure visual freshness checks for a single proposed computer action.

Pixels do not prove widget identity or focus. The surface owns trusted display
and window metadata, keyboard anchoring and temporal caret verification. This
module only accepts unchanged action regions plus bounded unrelated changes.
A returned caret-shaped box is a keyboard-only *candidate*, never permission to inject input:
even a static thin glyph can have the same pixels. The caller must observe the
same pattern revert while metadata and the surrounding region stay stable.

Regions use the full-desktop 0-1000 grid. Pixel boxes are half-open
``(left, top, right, bottom)``; foreground frames use ``(x, y, width, height)``.
No model-provided region can shrink the independently protected point crop.
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 32 * 1024 * 1024
REGION_MARGIN_PX = 12
POINT_HALF_WIDTH_PX = 48
POINT_HALF_HEIGHT_PX = 32
MAX_GLOBAL_CHANGED_FRACTION = 0.15
MAX_FOREGROUND_CHANGED_FRACTION = 0.08
MAX_OCCUPIED_FOREGROUND_CELLS = 12  # 25% of a fixed 8-column, 6-row grid.

PixelBox = tuple[int, int, int, int]


class FreshnessError(Exception):
    """Evidence cannot safely ground the proposed action; observe again."""


def _integer(value: object, low: int, high: int, name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise FreshnessError(f"{name} must be an integer from {low} to {high}.")
    return value


def validate_region(raw: dict) -> dict:
    """Copy a strict, positive, bounded desktop-grid interaction region."""
    if not isinstance(raw, dict) or set(raw) != {"x", "y", "width", "height"}:
        raise FreshnessError("An interaction region needs x, y, width and height.")
    region = {
        key: _integer(raw[key], 1 if key in {"width", "height"} else 0, 1000, key)
        for key in ("x", "y", "width", "height")
    }
    if region["x"] + region["width"] > 1000 or region["y"] + region["height"] > 1000:
        raise FreshnessError("The interaction region exceeds the desktop grid.")
    return region


def _screen_size(width: int, height: int) -> None:
    if (
        type(width) is not int
        or type(height) is not int
        or width <= 0
        or height <= 0
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise FreshnessError("The screenshot dimensions are invalid.")


def _visible_frame(frame: tuple, width: int, height: int) -> PixelBox:
    if (
        not isinstance(frame, tuple)
        or len(frame) != 4
        or any(type(value) is not int for value in frame)
        or frame[2] <= 0
        or frame[3] <= 0
    ):
        raise FreshnessError("Foreground window geometry is invalid.")
    x, y, w, h = frame
    bounds = (max(0, x), max(0, y), min(width, x + w), min(height, y + h))
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise FreshnessError("The foreground window is outside the screenshot.")
    return bounds


def protected_region(action: dict, width: int, height: int, frame: tuple) -> PixelBox:
    """Validate the declared target and enlarge it with independent margins.

    The caller must separately enforce keyboard regions equal its own previous
    left-click anchor. The returned protection may extend beyond the window;
    the declared model region itself must lie wholly within the visible window.
    """
    _screen_size(width, height)
    fx0, fy0, fx1, fy1 = _visible_frame(frame, width, height)
    if (
        not isinstance(action, dict)
        or type(action.get("type")) is not str
        or action["type"] not in {"click", "move", "scroll", "type", "press"}
    ):
        raise FreshnessError("A supported action is required for visual protection.")
    region = validate_region(action.get("region"))
    x, y, w, h = (region[key] for key in ("x", "y", "width", "height"))
    # Round outward so subpixel grid rounding cannot omit target pixels.
    x0, y0 = x * width // 1000, y * height // 1000
    x1, y1 = ((x + w) * width + 999) // 1000, ((y + h) * height + 999) // 1000
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise FreshnessError(
            "The interaction region must cover at least 8 by 8 pixels."
        )
    if not (fx0 <= x0 < x1 <= fx1 and fy0 <= y0 < y1 <= fy1):
        raise FreshnessError("The interaction region is outside the foreground window.")
    if (x1 - x0) * (y1 - y0) * 2 > (fx1 - fx0) * (fy1 - fy0):
        raise FreshnessError(
            "The interaction region exceeds half the foreground window."
        )
    protected = (
        x0 - REGION_MARGIN_PX,
        y0 - REGION_MARGIN_PX,
        x1 + REGION_MARGIN_PX,
        y1 + REGION_MARGIN_PX,
    )
    if action["type"] in {"click", "move", "scroll"}:
        point_x = _integer(action.get("x"), 0, 1000, "x")
        point_y = _integer(action.get("y"), 0, 1000, "y")
        if not (x <= point_x <= x + w and y <= point_y <= y + h):
            raise FreshnessError("The action point is outside its interaction region.")
        px, py = (
            point_x * max(1, width - 1) // 1000,
            point_y * max(1, height - 1) // 1000,
        )
        if not (fx0 <= px < fx1 and fy0 <= py < fy1):
            raise FreshnessError(
                "The physical action point is outside the foreground window."
            )
        protected = (
            min(protected[0], px - POINT_HALF_WIDTH_PX),
            min(protected[1], py - POINT_HALF_HEIGHT_PX),
            max(protected[2], px + POINT_HALF_WIDTH_PX),
            max(protected[3], py + POINT_HALF_HEIGHT_PX),
        )
    return (
        max(0, protected[0]),
        max(0, protected[1]),
        min(width, protected[2]),
        min(height, protected[3]),
    )


def _pixels(data: bytes) -> np.ndarray:
    if type(data) is not bytes or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise FreshnessError("Screenshot evidence is not bounded PNG data.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            _screen_size(*image.size)
            if image.format != "PNG":
                raise FreshnessError("Screenshot evidence must be PNG.")
            image.load()
            return np.asarray(image.convert("RGB"))
    except FreshnessError:
        raise
    except Exception as exc:
        raise FreshnessError("Screenshot evidence could not be decoded.") from exc


def _box(box: tuple, width: int, height: int) -> PixelBox:
    if (
        not isinstance(box, tuple)
        or len(box) != 4
        or any(type(value) is not int for value in box)
        or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)
    ):
        raise FreshnessError("The protected pixel region is invalid.")
    return box


def _caret_candidate(
    before: np.ndarray, after: np.ndarray, mask: np.ndarray, protected: PixelBox
) -> PixelBox | None:
    """Recognize only an isolated solid on/off stripe against uniform ground."""
    px0, py0, px1, py1 = protected
    local = mask[py0:py1, px0:px1]
    ys, xs = np.nonzero(local)
    if not len(xs):
        return None
    x0, y0 = int(xs.min()) + px0, int(ys.min()) + py0
    x1, y1 = int(xs.max()) + px0 + 1, int(ys.max()) + py0 + 1
    if not (1 <= x1 - x0 <= 3 and 8 <= y1 - y0 <= 40):
        raise FreshnessError("The protected target changed; observe again.")
    if len(xs) != (x1 - x0) * (y1 - y0):
        raise FreshnessError(
            "The protected target changed beyond a solid caret stripe."
        )
    # Require unchanged context on all sides, within the protected region.
    if x0 <= px0 or x1 >= px1 or y0 <= py0 or y1 >= py1:
        raise FreshnessError("The possible caret lacks unchanged surrounding evidence.")
    old, new = before[y0:y1, x0:x1], after[y0:y1, x0:x1]
    if not np.all(old == old[0, 0]) or not np.all(new == new[0, 0]):
        raise FreshnessError("The protected change is not a uniform caret stripe.")
    ring = before[y0 - 1 : y1 + 1, x0 - 1 : x1 + 1]
    ring_mask = np.ones(ring.shape[:2], dtype=bool)
    ring_mask[1:-1, 1:-1] = False
    neighbors = ring[ring_mask]
    # Either old or new stripe pixels must be identical to every immediate
    # surrounding pixel. This rejects foreground glyph edits over textured or
    # mixed content; temporal reversion remains the caller's responsibility.
    if not (np.all(neighbors == old[0, 0]) or np.all(neighbors == new[0, 0])):
        raise FreshnessError("The possible caret does not match its field background.")
    return x0, y0, x1, y1


def _reject_remote_focus_changes(mask: np.ndarray) -> None:
    """Reject a second caret or a newly changed rectangular widget outline.

    This intentionally treats ambiguous remote glyph/outline changes as a
    reason to re-observe for keyboard actions. It cannot detect invisible DOM
    focus changes, and is not an accessibility or focused-widget identity API.
    """
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if 1 <= width <= 3 and 8 <= height <= 40 and area >= width * height * 0.8:
            raise FreshnessError("A possible caret moved outside the keyboard target.")
        if width < 8 or height < 8:
            continue
        component = labels[y : y + height, x : x + width] == index
        # A changed focus ring has all four long sides and an almost unchanged
        # interior. Accept 1-3 pixel ring thickness without masking its pixels.
        if (
            np.mean(np.any(component[:3, :], axis=0)) >= 0.8
            and np.mean(np.any(component[-3:, :], axis=0)) >= 0.8
            and np.mean(np.any(component[:, :3], axis=1)) >= 0.8
            and np.mean(np.any(component[:, -3:], axis=1)) >= 0.8
            and np.count_nonzero(component[3:-3, 3:-3])
            <= (width - 6) * (height - 6) * 0.1
        ):
            raise FreshnessError(
                "A possible focus outline changed outside the keyboard target."
            )


def assess_change(
    before: bytes, after: bytes, protected: tuple, frame: tuple, *, keyboard: bool
) -> PixelBox | None:
    """Accept stable targets, or return a keyboard caret requiring later proof.

    Unrelated differences are bounded to 15% of the screenshot, 8% of the
    visible foreground, and 12 of 48 foreground grid cells (4+ changed pixels
    occupies a cell). The protected region is excluded from those allowances:
    every local changed pixel must be part of one tightly bounded caret stripe
    for keyboard actions. Point actions require every protected pixel unchanged.
    """
    old, new = _pixels(before), _pixels(after)
    if old.shape != new.shape:
        raise FreshnessError("Screenshot geometry changed; observe again.")
    height, width = old.shape[:2]
    px0, py0, px1, py1 = protected = _box(protected, width, height)
    fx0, fy0, fx1, fy1 = _visible_frame(frame, width, height)
    if type(keyboard) is not bool:
        raise FreshnessError("Keyboard freshness mode must be explicit.")
    changed = np.any(old != new, axis=2)
    if not keyboard and np.any(changed[py0:py1, px0:px1]):
        raise FreshnessError("The protected point target changed; observe again.")
    candidate = _caret_candidate(old, new, changed, protected)
    remote = changed.copy()
    remote[py0:py1, px0:px1] = False
    if np.count_nonzero(remote) > width * height * MAX_GLOBAL_CHANGED_FRACTION:
        raise FreshnessError("Too much of the surrounding screen changed.")
    foreground = remote[fy0:fy1, fx0:fx1]
    if np.count_nonzero(foreground) > foreground.size * MAX_FOREGROUND_CHANGED_FRACTION:
        raise FreshnessError("Too much of the foreground window changed.")
    occupied = 0
    for row in range(6):
        for column in range(8):
            cell = foreground[
                row * foreground.shape[0] // 6 : (row + 1) * foreground.shape[0] // 6,
                column * foreground.shape[1] // 8 : (column + 1)
                * foreground.shape[1]
                // 8,
            ]
            occupied += np.count_nonzero(cell) >= 4
    if occupied > MAX_OCCUPIED_FOREGROUND_CELLS:
        raise FreshnessError("Changes are spread across too many foreground regions.")
    if keyboard and np.any(foreground):
        _reject_remote_focus_changes(foreground)
    return candidate
