"""Pure freshness fixtures: no desktop, browser, subprocess, or model calls."""

import io

import pytest
from PIL import Image, ImageDraw

from system.computer_freshness import (
    FreshnessError,
    assess_change,
    protected_region,
    validate_region,
)

SIZE = (1200, 800)
FRAME = (180, 100, 800, 600)
REGION = {"x": 250, "y": 300, "width": 240, "height": 80}
PROTECTED = (288, 228, 600, 316)
BACKGROUND = (244, 246, 248)
INK = (24, 30, 36)


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def scene():
    image = Image.new("RGB", SIZE, (9, 22, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 100, 979, 699), fill=(220, 225, 230))
    draw.rectangle((180, 100, 979, 138), fill=(34, 40, 49))
    draw.text((210, 112), "Research browser", fill="white")
    draw.rectangle((300, 240, 587, 303), fill=BACKGROUND, outline=(76, 88, 98))
    draw.text((314, 265), "Find sources", fill=INK)
    draw.rectangle((300, 390, 587, 453), fill=BACKGROUND, outline=(76, 88, 98))
    draw.text((314, 415), "Second field", fill=INK)
    draw.text((1070, 36), "10:24", fill=(140, 220, 240))
    return image


def check(before, after, *, keyboard=False, protected=PROTECTED, frame=FRAME):
    return assess_change(png(before), png(after), protected, frame, keyboard=keyboard)


def test_validate_region_returns_independent_strict_copy():
    region = validate_region(REGION)
    assert region == REGION and region is not REGION
    assert validate_region({"x": 0, "y": 0, "width": 1000, "height": 1000})


@pytest.mark.parametrize(
    "region",
    [
        None,
        [250, 300, 240, 80],
        {**REGION, "x": True},
        {**REGION, "y": 300.0},
        {**REGION, "x": -1},
        {**REGION, "width": 0},
        {**REGION, "height": -1},
        {**REGION, "width": 751},
        {**REGION, "height": 701},
        {**REGION, "extra": 1},
        {"x": 250, "y": 300, "width": 240},
    ],
)
def test_invalid_region_rejected(region):
    with pytest.raises(FreshnessError):
        validate_region(region)


@pytest.mark.parametrize("kind", ["click", "move", "scroll"])
def test_point_region_contains_action_and_gets_independent_margin(kind):
    action = {"type": kind, "x": 260, "y": 310, "region": REGION}
    assert protected_region(action, *SIZE, FRAME) == (263, 215, 600, 316)


def test_keyboard_region_gets_margin_without_model_point():
    assert (
        protected_region({"type": "type", "region": REGION}, *SIZE, FRAME) == PROTECTED
    )


@pytest.mark.parametrize(
    "action",
    [
        {"type": "click", "x": 900, "y": 310, "region": REGION},
        {"type": "click", "x": 260, "region": REGION},
        {"type": "scroll", "region": REGION},
        {"type": "move", "x": True, "y": 310, "region": REGION},
        {"type": "type"},
        {
            "type": "click",
            "x": 260,
            "y": 310,
            "region": {"x": 250, "y": 300, "width": 5, "height": 5},
        },
        {"type": "type", "region": {"x": 100, "y": 300, "width": 240, "height": 80}},
        {"type": "type", "region": {"x": 150, "y": 125, "width": 665, "height": 748}},
    ],
)
def test_unrelated_tiny_missing_outside_or_huge_region_rejected(action):
    with pytest.raises(FreshnessError):
        protected_region(action, *SIZE, FRAME)


def test_independent_point_crop_cannot_be_shrunk_and_clips_only_screen():
    action = {
        "type": "click",
        "x": 0,
        "y": 0,
        "region": {"x": 0, "y": 0, "width": 10, "height": 10},
    }
    assert protected_region(action, *SIZE, (0, 0, *SIZE)) == (0, 0, 48, 32)
    action["region"] = {"x": 990, "y": 990, "width": 10, "height": 10}
    action.update(x=1000, y=1000)
    assert protected_region(action, *SIZE, (0, 0, *SIZE)) == (1151, 767, 1200, 800)


@pytest.mark.parametrize("action", [None, [], {"type": []}, {"type": "shell"}])
def test_invalid_action_container_or_type_fails_cleanly(action):
    with pytest.raises(FreshnessError):
        protected_region(action, *SIZE, FRAME)


def test_point_rounding_cannot_escape_foreground_by_one_pixel():
    # Point injection uses (dimension - 1), whereas region bounds round outward
    # over the screenshot width. A boundary grid point must still be on-window.
    action = {"type": "click", "x": 250, "y": 350, "region": REGION}
    with pytest.raises(FreshnessError):
        protected_region(action, *SIZE, (300, 100, 680, 600))


def test_partially_offscreen_foreground_uses_visible_intersection():
    action = {"type": "type", "region": {"x": 0, "y": 200, "width": 80, "height": 80}}
    assert protected_region(action, *SIZE, (-200, 100, 700, 600)) == (0, 148, 108, 236)


@pytest.mark.parametrize("width,height", [(True, 800), (1200, 0), (1200.0, 800)])
def test_invalid_screen_dimensions_rejected(width, height):
    with pytest.raises(FreshnessError):
        protected_region({"type": "type", "region": REGION}, width, height, FRAME)


def test_static_frame_and_png_encoding_change_are_allowed():
    before = scene()
    stream = io.BytesIO()
    before.save(stream, format="PNG", compress_level=0)
    assert (
        assess_change(png(before), stream.getvalue(), PROTECTED, FRAME, keyboard=True)
        is None
    )


def test_unrelated_clock_and_scattered_jarvis_particles_outside_app_are_allowed():
    before = scene()
    after = before.copy()
    draw = ImageDraw.Draw(after)
    draw.rectangle((1065, 32, 1140, 54), fill=(9, 22, 30))
    draw.text((1070, 36), "10:25", fill=(140, 220, 240))
    for x, y in [(40, 80), (92, 270), (70, 640), (1050, 210), (1130, 500), (590, 750)]:
        draw.ellipse((x, y, x + 9, y + 9), fill=(90, 185, 205))
    assert check(before, after, keyboard=True) is None


def test_small_unrelated_content_change_inside_foreground_is_allowed():
    before = scene()
    after = before.copy()
    ImageDraw.Draw(after).text((850, 170), "12 updates", fill=INK)
    assert check(before, after) is None


@pytest.mark.parametrize(
    "change", ["label", "move", "replace", "checkbox", "focus_ring", "two_carets"]
)
def test_relevant_target_changes_are_rejected(change):
    before = scene()
    after = before.copy()
    draw = ImageDraw.Draw(after)
    if change == "label":
        draw.text((430, 265), "DELETE", fill=INK)
    elif change == "move":
        draw.rectangle((300, 240, 587, 303), fill=(220, 225, 230))
        draw.rectangle((314, 252, 601, 315), fill=BACKGROUND, outline=INK)
    elif change == "replace":
        draw.rectangle((300, 240, 587, 303), fill=(210, 100, 100))
    elif change == "checkbox":
        draw.rectangle((470, 262, 483, 275), fill=INK)
    elif change == "focus_ring":
        draw.rectangle((300, 240, 587, 303), outline=(0, 100, 255), width=2)
    else:
        draw.rectangle((460, 260, 461, 279), fill=INK)
        draw.rectangle((480, 260, 481, 279), fill=INK)
    with pytest.raises(FreshnessError):
        check(before, after, keyboard=True)


@pytest.mark.parametrize("reverse", [False, True])
def test_caret_candidate_requires_caller_temporal_verification(reverse):
    before = scene()
    after = before.copy()
    ImageDraw.Draw(after).rectangle((460, 260, 461, 279), fill=INK)
    if reverse:
        before, after = after, before
    assert check(before, after, keyboard=True) == (460, 260, 462, 280)


@pytest.mark.parametrize("stripe_width,stripe_height", [(1, 8), (3, 40)])
def test_caret_exact_size_bounds(stripe_width, stripe_height):
    before = scene()
    after = before.copy()
    ImageDraw.Draw(after).rectangle(
        (460, 250, 459 + stripe_width, 249 + stripe_height), fill=INK
    )
    assert check(before, after, keyboard=True) == (
        460,
        250,
        460 + stripe_width,
        250 + stripe_height,
    )


def test_caret_touching_protected_boundary_cannot_get_masked():
    before = scene()
    after = before.copy()
    ImageDraw.Draw(after).rectangle((460, 260, 461, 279), fill=INK)
    with pytest.raises(FreshnessError):
        check(before, after, keyboard=True, protected=(460, 248, 490, 292))


def test_static_thin_glyph_is_only_candidate_never_authorized():
    before = scene()
    after = before.copy()
    # A plain sans-serif lowercase l can be pixel-identical to a caret. The
    # caller must reject it unless subsequent samples revert to the old pixels.
    ImageDraw.Draw(after).rectangle((460, 260, 461, 279), fill=INK)
    assert check(before, after, keyboard=True) is not None


def test_point_action_rejects_even_a_single_caret_shaped_protected_change():
    before = scene()
    after = before.copy()
    ImageDraw.Draw(after).rectangle((460, 260, 461, 279), fill=INK)
    with pytest.raises(FreshnessError):
        check(before, after, keyboard=False)


@pytest.mark.parametrize(
    "change", ["not_uniform", "background_mismatch", "too_short", "too_wide"]
)
def test_caret_like_but_invalid_stripes_are_rejected(change):
    before = scene()
    after = before.copy()
    draw = ImageDraw.Draw(after)
    if change == "too_short":
        draw.rectangle((460, 260, 461, 264), fill=INK)
    elif change == "too_wide":
        draw.rectangle((460, 260, 464, 279), fill=INK)
    else:
        draw.rectangle((460, 260, 461, 279), fill=INK)
        if change == "not_uniform":
            draw.point((460, 270), fill=(30, 40, 50))
        else:
            ImageDraw.Draw(before).rectangle((459, 260, 459, 279), fill=(100, 110, 120))
            draw.rectangle((459, 260, 459, 279), fill=(100, 110, 120))
    with pytest.raises(FreshnessError):
        check(before, after, keyboard=True)


@pytest.mark.parametrize("replacement", ["caret", "focus_ring"])
def test_keyboard_focus_moves_to_another_field_is_rejected(replacement):
    before = scene()
    ImageDraw.Draw(before).rectangle((460, 260, 461, 279), fill=INK)
    after = scene()
    draw = ImageDraw.Draw(after)
    if replacement == "caret":
        draw.rectangle((460, 410, 461, 429), fill=INK)
    else:
        draw.rectangle((300, 390, 587, 453), outline=(0, 100, 255), width=2)
    with pytest.raises(FreshnessError):
        check(before, after, keyboard=True)


def test_keyboard_new_remote_caret_rejected_even_when_local_pixels_unchanged():
    before = scene()
    after = before.copy()
    ImageDraw.Draw(after).rectangle((460, 410, 461, 429), fill=INK)
    with pytest.raises(FreshnessError):
        check(before, after, keyboard=True)
    assert check(before, after, keyboard=False) is None


@pytest.mark.parametrize("thickness", [1, 2, 3])
def test_remote_focus_outline_thicknesses_rejected(thickness):
    before = scene()
    after = before.copy()
    ImageDraw.Draw(after).rectangle(
        (300, 390, 587, 453), outline=(0, 100, 255), width=thickness
    )
    with pytest.raises(FreshnessError):
        check(before, after, keyboard=True)


def test_twelve_occupied_foreground_cells_allowed_thirteen_rejected():
    before = scene()
    after = before.copy()
    draw = ImageDraw.Draw(after)
    points = [
        (190 + col * 100, 520 + row * 100) for row in range(2) for col in range(8)
    ]
    for x, y in points[:12]:
        draw.rectangle((x, y, x + 1, y + 1), fill=(1, 2, 3))
    assert check(before, after) is None
    x, y = points[12]
    draw.rectangle((x, y, x + 1, y + 1), fill=(1, 2, 3))
    with pytest.raises(FreshnessError):
        check(before, after)


@pytest.mark.parametrize("change", ["global", "foreground", "sparse_layout"])
def test_large_or_sparse_widespread_scene_changes_rejected(change):
    before = scene()
    after = before.copy()
    draw = ImageDraw.Draw(after)
    if change == "global":
        draw.rectangle((0, 0, 1199, 99), fill="white")
        draw.rectangle((0, 700, 1199, 799), fill="white")
    elif change == "foreground":
        draw.rectangle((650, 200, 950, 500), fill=(150, 160, 170))
    else:
        for row in range(6):
            for col in range(8):
                x, y = 185 + col * 100, 145 + row * 90
                if not (
                    PROTECTED[0] <= x < PROTECTED[2]
                    and PROTECTED[1] <= y < PROTECTED[3]
                ):
                    draw.rectangle((x, y, x + 1, y + 1), fill=(1, 2, 3))
    with pytest.raises(FreshnessError):
        check(before, after)


@pytest.mark.parametrize("invalid", ["bad_png", "size", "protected", "frame"])
def test_invalid_evidence_and_geometry_rejected(invalid):
    before = png(scene())
    after = before
    protected, frame = PROTECTED, FRAME
    if invalid == "bad_png":
        after = b"not an image"
    elif invalid == "size":
        after = png(Image.new("RGB", (100, 100)))
    elif invalid == "protected":
        protected = (0, 0, 1201, 800)
    else:
        frame = (180, 100, -1, 600)
    with pytest.raises(FreshnessError):
        assess_change(before, after, protected, frame, keyboard=False)
