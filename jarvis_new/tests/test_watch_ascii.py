import types

import pytest

import watch_ascii as wa
import watch_mode


def _frame(**kw):
    kw.setdefault("clock", "14:02")
    kw.setdefault("status", "A R M E D   ·   3   E V E N T S")
    return wa.compose(170, 50, 5.0, **kw)


def test_compose_is_pure_ascii_and_sized():
    g = _frame(recording=True, rec_on=True, live=1.0)
    assert (g.cols, g.rows) == (170, 50)
    assert all(len(r) == 170 for r in g.ch) and len(g.ch) == 50
    text = g.text()
    assert text.isascii()
    assert "JARVIS :: WATCH" in text and "14:02" in text
    assert "R E C" in text and "E V E N T S" in text
    assert (
        wa.compose(170, 50, 5.0, clock="14:02", status="x").text()
        == wa.compose(170, 50, 5.0, clock="14:02", status="x").text()
    )


def test_eye_opens_closes_and_glows_on_motion():
    open_ = _frame()
    shut = _frame(openness=0.0)
    alert = _frame(alert=1.0, tracking=(0.3, -0.1), pupil=0.12)
    assert "@" in open_.text()
    eye_row = int(50 * 0.38)
    assert "=" * 10 in "".join(shut.ch[eye_row])
    assert any(wa.ACCENT in row for row in alert.lv)
    assert "TRK +0.30 -0.10" in alert.text()


def test_title_falls_back_when_narrow_and_tiny_grid_is_safe():
    assert "#" in "".join("".join(r) for r in _frame().ch[30:45])
    narrow = wa.compose(60, 30, 5.0, title="WORK ONGOING")
    assert narrow.text().isascii()
    tiny = wa.compose(10, 4, 1.0, title="SAVING")
    assert "SAVING"[:8] in tiny.text()


@pytest.mark.parametrize(
    "target", [None, (1,), ("x", 1), (float("nan"), 0), (0.5, 0.5)]
)
def test_norm_target_is_bounded(target):
    out = wa.norm_target(target)
    if out is not None:
        assert all(-1 <= v <= 1 for v in out)


def test_dive_zoom_skips_text_layers():
    g = wa.compose(120, 36, 3.0, zoom=8, pupil=1.0, title="WATCH MODE")
    assert "#####" not in "".join("".join(r) for r in g.ch[25:32])


class _Canvas:
    def __init__(self):
        self.items = {}
        self.n = 0
        self.mapped = True

    def create_text(self, *a, **k):
        self.n += 1
        self.items[self.n] = dict(k)
        return self.n

    def itemconfigure(self, item, **k):
        if isinstance(item, int):
            self.items[item].update(k)

    def winfo_ismapped(self):
        return self.mapped

    def delete(self, tag):
        self.items.clear()


class _Font:
    def __init__(self, **k):
        pass

    def measure(self, s):
        return 10

    def metrics(self, k):
        return 20


def test_card_layers_rows_and_saving(monkeypatch):
    import tkinter.font as tkfont

    monkeypatch.setattr(tkfont, "Font", _Font)
    c = _Canvas()
    card = wa.AsciiCard(c, 1600, 1000, "Mono")
    assert (card.cols, card.rows) == (160, 50)
    assert c.n == (wa.LEVELS) * 50
    card.paint(0.0, 1.0, "14:02", "A R M E D", False, False, 0.5)
    for i in range(30):
        card.update(i * 0.1)
    drawn = "".join(v.get("text", "") for v in c.items.values())
    assert drawn.isascii() and "JARVIS" in drawn
    card.saving("SAVING", "3   E V E N T S")
    card.update(3.01)  # forced past the frame throttle
    assert card.saving_at is not None
    c.mapped = False
    card.update(5.0)
    assert card.last is None
    card.destroy()
    assert card.dead and not c.items


def test_panel_paint_failure_keeps_cover():
    panel = object.__new__(watch_mode._Panel)
    panel.c = types.SimpleNamespace(
        winfo_width=lambda: 800,
        winfo_height=lambda: 600,
        create_text=lambda *a, **k: 7,
        itemconfigure=lambda *a, **k: None,
    )
    panel.plain = None
    calls = []

    def fail(*a, **k):
        raise RuntimeError("renderer")

    panel.eyes = types.SimpleNamespace(paint=fail, destroy=lambda: calls.append("x"))
    panel.paint(0, 1, "", "", False, False, 0)
    assert panel.eyes is None and calls == ["x"] and panel.plain == 7
    panel.saving("SAVED", "0")
    assert panel.status_text == "0"
