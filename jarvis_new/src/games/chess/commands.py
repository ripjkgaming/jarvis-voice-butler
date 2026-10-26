"""Voice intent catalogue (Appendix A.8-A.12) and the intent priority order.

``classify`` turns an utterance into a named intent; moves are parsed
separately by ``voice_moves``. The controller applies the A.13 priority:
wake > stop > pending answer > moves > take-over > game > analysis > settings.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import chess

from games.chess import text_norm, voice_moves

# ------------------------------------------------------------------- numbers
_UNITS = {
    "zero": 0, "one": 1, "won": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4,
    "for": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}  # fmt: skip


def parse_number(text: str) -> int | None:
    """ "twelve" / "12" / "fifteen hundred" / "twenty one" -> int."""
    words = text.lower().replace("-", " ").split()
    total, cur, seen = 0, 0, False
    for w in words:
        if w.isdigit():
            cur += int(w)
            seen = True
        elif w == "and":
            continue
        elif w == "hundred":
            cur = (cur or 1) * 100
            seen = True
        elif w == "thousand":
            total += (cur or 1) * 1000
            cur = 0
            seen = True
        elif w in _UNITS:
            cur += _UNITS[w]
            seen = True
        else:
            return None
    return total + cur if seen else None


_NUM_RX = r"(?P<n>(?:\d+|[a-z]+(?:[ -][a-z]+){0,2}))"


@dataclass
class Intent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    group: str = ""


@dataclass
class Rule:
    name: str
    group: str
    phrases: frozenset[str] = frozenset()
    patterns: tuple[re.Pattern, ...] = ()
    fixed: dict[str, Any] = field(default_factory=dict)
    post: Callable[[dict, str], dict | None] | None = None


def _norm(p: str) -> str:
    return text_norm.clean(p)


def R(  # noqa: N802 - short on purpose: the catalogue below reads as a table
    name: str,
    group: str,
    phrases: str | list[str] = (),  # type: ignore[assignment]
    rx: list[str] | str = (),  # type: ignore[assignment]
    post: Callable[[dict, str], dict | None] | None = None,
    **fixed: Any,
) -> Rule:
    if isinstance(phrases, str):
        phrases = [p for p in phrases.split("|") if p]
    if isinstance(rx, str):
        rx = [rx]
    return Rule(
        name,
        group,
        frozenset(_norm(p) for p in phrases),
        tuple(re.compile(r"^(?:" + r + r")$") for r in rx),
        fixed,
        post,
    )


# -------------------------------------------------------------------- A.8 stop
STOP_PHRASES = _stop = frozenset(
    _norm(p)
    for p in [
        "stop",
        "stop it",
        "stop that",
        "stop now",
        "stop talking",
        "stop analysing",
        "stop analyzing",
        "stop the analysis",
        "stop commentary",
        "stop commenting",
        "stop explaining",
        "stop thinking",
        "quiet",
        "be quiet",
        "quiet please",
        "silence",
        "hush",
        "shh",
        "shush",
        "enough",
        "that's enough",
        "that'll do",
        "that will do",
        "okay okay",
        "alright alright",
        "cut it",
        "cut it out",
        "cut",
        "halt",
        "hold on",
        "hold it",
        "wait",
        "wait wait",
        "pause commentary",
        "shut up",
        "zip it",
        "leave it",
        "drop it",
        "forget it",
        "nevermind",
        "never mind",
        "cancel",
        "cancel that",
        "no no",
        "not now",
        "later",
        "i get it",
        "got it",
        "i know",
        "thanks that's fine",
        "thank you that's all",
        "stop the commentary",
        "stop analysing please",
        "that's enough thanks",
    ]
)

NARROW_STOPS = [
    R(
        "stop_eval",
        "stop",
        "stop the eval|hide the eval|stop the evaluation|turn the eval off|eval off|hide the evaluation",
    ),
    R(
        "stop_hints",
        "stop",
        "stop the hints|no more hints|stop hints|stop giving hints|hints off",
    ),
    R("stop_review", "stop", "stop the review|end the review|stop reviewing"),
    R(
        "stop_announce",
        "stop",
        "stop announcing|don't announce your moves|do not announce your moves|stop announcing moves|stop announcing your moves",
    ),
    R(
        "stop_time_warn",
        "stop",
        "stop warning me about time|no more time warnings|stop the time warnings",
    ),
    R(
        "stop_confirm",
        "stop",
        "stop confirming|stop confirming moves|stop confirming my moves|don't confirm my moves",
    ),
    R("stop_spectating", "stop", "stop spectating|stop playing yourself|stop watching"),
    R(
        "stop_commentary",
        "stop",
        "stop the commentary|commentary off|turn commentary off",
    ),
]


# --------------------------------------------------------------------- rules
def _n(post_key: str = "n") -> Callable[[dict, str], dict | None]:
    def fn(args: dict, _t: str) -> dict | None:
        v = parse_number(args.get(post_key, ""))
        if v is None:
            return None
        args[post_key] = v
        return args

    return fn


def _sq(args: dict, t: str) -> dict | None:
    sqs = voice_moves.find_squares(t)
    if not sqs:
        return None
    args["square"] = chess.square_name(sqs[-1])
    return args


def _piece(args: dict, t: str) -> dict | None:
    pt = voice_moves.find_piece_type(t)
    if pt is None:
        return None
    args["piece"] = chess.piece_name(pt)
    return args


def _name(args: dict, _t: str) -> dict | None:
    nm = (args.get("name") or "").strip()
    if not nm or nm in {
        "the game",
        "game",
        "it",
        "this",
        "as",
        "my game",
        "my last game",
        "last game",
    }:
        return None
    return args


def _elo(args: dict, _t: str) -> dict | None:
    v = parse_number(args.get("elo", ""))
    if v is None or not 400 <= v <= 3500:
        return None
    return {"elo": v}


def _level(args: dict, _t: str) -> dict | None:
    lv = args.get("level", "").strip()
    table = {
        "easy": "novice", "novice": "novice", "beginner": "novice", "club": "club",
        "club level": "club", "intermediate": "club", "strong": "strong",
        "advanced": "strong", "expert": "expert", "master": "master",
        "grandmaster": "full", "full": "full", "full strength": "full",
        "maximum": "full", "max": "full",
    }  # fmt: skip
    if lv in table:
        return {"level": table[lv]}
    return None


# ------------------------------------------------------- strength levels 1-10
_LV_PRE = (
    r"(?:(?:set|make|change|put|switch|move|go|play|turn)"
    r"(?: it| me| the difficulty| difficulty| the level| the)?(?: (?:to|at|on))? )?"
    r"(?:(?:to|at|on) )?(?:(?:the )?difficulty )?"
)
_LV_HI = "max|maximum|highest|top|hardest"
_LV_LO = "lowest|minimum|min|easiest|bottom"


def _lv_n(args: dict, _t: str) -> dict | None:
    n = parse_number(args.get("n", ""))
    return None if n is None else {"n": n}


def _lv_word(args: dict, _t: str) -> dict | None:
    w = args.get("w", "")
    return {"n": 10 if re.fullmatch(_LV_HI, w) else 1}


def _lv_adjust_phrases(words: list[str]) -> list[str]:
    pre = ["", "make it ", "go ", "play ", "can you make it ", "could you make it "]
    mod = ["", "a bit ", "a little ", "slightly ", "a notch ", "a touch ", "a tad ", "much "]
    return [f"{a}{m}{w}" for a in pre for m in mod for w in words if m or a not in ("", "make it ")]


_LEVEL_RULES = [
    R("set_level", "game", rx=_LV_PRE + r"level(?: (?:to|too|for))? (?P<n>[a-z0-9 -]+?)(?: please)?", post=_lv_n),
    R("set_level", "game", rx=_LV_PRE + r"(?:the )?(?P<w>" + _LV_HI + "|" + _LV_LO + r") (?:difficulty )?level", post=_lv_word),
    R("set_level", "game", rx=_LV_PRE + r"level(?: (?:to|too|for))? (?P<w>" + _LV_HI + "|" + _LV_LO + ")", post=_lv_word),
    R(
        "level_adjust", "game",
        _lv_adjust_phrases(["harder", "tougher", "stronger"])
        + ["ramp it up", "crank it up", "step it up", "turn it up", "level up",
           "up a level", "raise the level", "increase the level", "up a notch"],
        direction=1,
    ),
    R(
        "level_adjust", "game",
        _lv_adjust_phrases(["easier", "weaker", "gentler"])
        + ["dial it down", "tone it down", "turn it down", "ease up", "ease off",
           "level down", "down a level", "lower the level", "decrease the level",
           "down a notch", "too difficult"],
        direction=-1,
    ),
    R(
        "level_query", "game",
        "what level are you|what level are you at|what level are you playing|"
        "what level are you on|what level is this|what level is it|what level is that|"
        "what level am i playing|what level am i playing at|what level do i play|"
        "what's the level|what is the level|which level|what level|current level|"
        "what is your level|what's your level|what's the current level|"
        "what level is the engine|what level is it set to|which level are you",
    ),
]  # fmt: skip



RULES: list[Rule] = []


def add(*rules: Rule) -> None:
    RULES.extend(rules)


# ------------------------------------------- series / history / board analysis
# Placed before every other catalogue rule so "play five games" never reaches
# `start`, "series score" never reaches `analyse`, etc. All anchored, so they
# cannot swallow moves ("night f three", "e four", "go e five" ...).
_COUNT_FIX = {"ate": "eight", "tin": "ten"}
_SERIES_MAX = 100


def parse_count(text: str) -> int | None:
    """Number of games: digits or words, 'a hundred' = 100. No range check."""
    words = [_COUNT_FIX.get(w, w) for w in (text or "").lower().replace("-", " ").split()]
    if len(words) > 1 and words[0] in ("a", "an"):
        words = words[1:]
    return parse_number(" ".join(words)) if words else None


def _num_any(text: str) -> int | None:
    v = parse_count(text)
    return v if v is not None and v >= 0 else None


_NW = "(?:" + "|".join([*_UNITS, "hundred", "thousand", "ate", "tin", "and"]) + ")"
_CNT = r"(?P<n>(?:an? )?(?:\d+|" + _NW + r"(?:[ -]" + _NW + r"){0,3}))"
_COLOUR_WORD = {"white": "white", "black": "black", "alternate": "alternate", "alternating": "alternate"}
_REST_COLOUR = re.compile(
    r"^(?: as (?P<c1>white|black|alternating|alternate)(?: colou?rs?)?"
    r"| (?P<c2>alternating|alternate)(?: colou?rs?)?)"
)
_REST_LEVEL = re.compile(r"^ at level (?P<lv>\d+|[a-z]+)")
_REST_TIME = re.compile(r"^ (?:with |at )?(?P<t>\d+|[a-z]+) seconds?(?: (?:per|a|each) move)?")


def _series_rest(rest: str, args: dict) -> bool:
    """Consume ' as white at level 3 ...' modifiers; False if anything is left."""
    rest = rest.rstrip()
    guard = 0
    while rest and guard < 4:
        guard += 1
        for rx, kind in ((_REST_COLOUR, "c"), (_REST_LEVEL, "l"), (_REST_TIME, "t")):
            m = rx.match(rest)
            if not m:
                continue
            if kind == "c":
                args["colour"] = _COLOUR_WORD[m.group("c1") or m.group("c2")]
            elif kind == "l":
                lv = _num_any(m.group("lv"))
                if lv is None or not 1 <= lv <= 20:
                    return False
                args["level"] = lv
            else:
                t = _num_any(m.group("t"))
                if t is None or t <= 0:
                    return False
                args["move_time"] = float(t)
            rest = rest[m.end() :]
            break
        else:
            return False
    return not rest


def _series_post(want_valid: bool) -> Callable[[dict, str], dict | None]:
    def fn(args: dict, _t: str) -> dict | None:
        n = _num_any(args.get("n", ""))
        if n is None:
            return None
        out: dict[str, Any] = {"colour": "alternate", "total": n}
        if not _series_rest(args.get("rest", ""), out):
            return None
        if (1 <= n <= _SERIES_MAX) != want_valid:
            return None
        if not want_valid:
            return {"total": n}
        return out

    return fn


_SERIES_RX = r"play (?:a series of )?" + _CNT + r" games?(?P<rest>(?: .+)?)"


def _opt(key: str, conv: Callable[[str], Any]) -> Callable[[dict, str], dict | None]:
    def fn(args: dict, _t: str) -> dict | None:
        v = conv(args.get("v", ""))
        return None if v is None else {key: v}

    return fn


def _lvl_conv(v: str) -> int | None:
    n = _num_any(v)
    return n if n is not None and 1 <= n <= 20 else None


def _time_conv(v: str) -> float | None:
    n = _num_any(v)
    return float(n) if n is not None and 0 < n <= 600 else None


def _open_ref(args: dict, _t: str) -> dict | None:
    ref = (args.get("ref") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ref):
        return {"ref": ref}
    if ref in ("latest", "last", "newest", "most recent"):
        return {"ref": "1"}
    n = _num_any(ref)
    if n is None or n < 1:
        return None
    return {"ref": str(n)}


def _goto_n(args: dict, _t: str) -> dict | None:
    n = _num_any(args.get("n", ""))
    return None if n is None or n < 1 else {"n": n}


def _rewind(args: dict, _t: str) -> dict | None:
    if not args.get("n"):
        return {}
    return _goto_n(args, _t)


add(
    R("series_start", "series", rx=_SERIES_RX, post=_series_post(True)),
    R("series_bad_count", "series", rx=_SERIES_RX, post=_series_post(False)),
    R(
        "series_start",
        "series",
        "play until i say stop|keep playing until i say stop|play games until i say stop|"
        "play until stop|play until i tell you to stop|play until i say stop playing|"
        "keep playing games until i say stop",
        total=None,
        colour="alternate",
    ),
    R(
        "series_option",
        "series",
        rx=r"(?:set )?series colou?rs?(?: to)? (?P<v>white|black|alternate|alternating)",
        post=_opt("colour", lambda v: _COLOUR_WORD.get(v)),
    ),
    R(
        "series_option",
        "series",
        rx=r"(?:set )?series level(?: to)? (?P<v>\d+|[a-z]+)",
        post=_opt("level", _lvl_conv),
    ),
    R(
        "series_option",
        "series",
        rx=r"(?:set )?series (?:time per move|move time)(?: to)? (?P<v>\d+|[a-z]+)(?: seconds?)?",
        post=_opt("move_time", _time_conv),
    ),
    R(
        "series_option",
        "series",
        "series speak every move|speak every move|speak every move in the series|"
        "series speak all moves|speak all moves",
        speak="moves",
    ),
    R(
        "series_option",
        "series",
        "series speak only results|speak only results|speak results only|only speak results|"
        "series speak results only|speak the results only|speak only the results|only results",
        speak="results",
    ),
    R(
        "series_left",
        "series",
        "how many games left|how many games are left|games left|games remaining|how many games remain|"
        "how many more games|how many games remaining|how many games to go|how many games are remaining|"
        "how many games do we have left",
    ),
    R(
        "series_score",
        "series",
        "series score|what's the series score|what is the series score|series status|"
        "what's the score in the series|score of the series|how is the series going|how's the series going",
    ),
    R("series_pause", "series", "pause the series|pause series|hold the series"),
    R(
        "series_resume",
        "series",
        "continue the series|resume the series|resume series|continue series|carry on with the series|"
        "restart the series",
    ),
    R("series_cancel", "series", "cancel the series|stop the series|end the series|cancel series|abort the series"),
    R(
        "series_resume_offer",
        "series",
        "resume the old series|continue the old series|resume my last series|continue the last series|"
        "resume my series|pick up the series",
    ),
    R(
        "handback",
        "takeover",
        "release control|revert control|release the control|revert the control|release control to me|"
        "revert control to me|give me control|give me back control",
    ),
    R(
        "list_games",
        "history",
        "list games|list my games|show my games|show games|list the games|game history|show game history|"
        "show my game history|my game history|list all games|list all my games",
    ),
    R(
        "open_game",
        "history",
        rx=r"open (?:the )?game (?P<ref>\d{4}-\d{2}-\d{2}|\d+|latest|last|newest|most recent|[a-z]+(?:[ -][a-z]+)?)",
        post=_open_ref,
    ),
    R("open_game", "history", "open the last game|open the latest game|open the newest game", ref="1"),
    R(
        "review_goto",
        "review",
        rx=r"(?:go (?:to|two|too)|goto|jump (?:to|two|too)) move " + _CNT,
        post=_goto_n,
    ),
    R(
        "hist_branch",
        "history",
        rx=r"(?:rewind|go back|roll back|take it back) (?:to|two|too) move " + _CNT
        + r"(?: and (?:continue|carry on|keep going|play|play on)(?: from here| on)?)?",
        post=_rewind,
    ),
    R(
        "hist_branch",
        "history",
        "continue from here|branch from here|play from here|rewind and continue from here|"
        "rewind and continue|play on from here|carry on from here",
    ),
    R(
        "hist_close",
        "history",
        "close history|exit history|leave history|close the history|back to the live game|"
        "back to the live board|close the game view|exit the history",
    ),
    R("undo", "game", "undo last move|undo the last move|undo my last move", n=1),
    R(
        "analyse_board",
        "analysis",
        "analyse the board|analyze the board|analyse board|analyze board|evaluate the board|"
        "assess the board|what's on the board|what is on the board|analyse the board please",
    ),
    R(
        "explain",
        "analysis",
        "what was my mistake|what was my blunder|what was my worst move|what did i do wrong|"
        "where did i go wrong|what was my biggest mistake",
        which="mistake",
    ),
)

# A.10 take-over and spectator (before game management, after moves)
add(
    R(
        "handback",
        "takeover",
        "i'll take it from here|i'll take over|i will take over|give it back|my turn|let me play|i've got it|i have got it|hands off|back to me|i'll drive|stop playing for me|release|return control|take back control|i'll play",
    ),
    R(
        "takeover",
        "takeover",
        "take over|take the wheel|you play|play for me|play my side|play this for me|autopilot|you take it|your turn to drive|i'm done you play|auto|take it over|take control",
    ),
    R(
        "takeover_finish",
        "takeover",
        "finish this|finish the game|finish it for me|play it out|play the rest|finish it|finish for me",
    ),
    R(
        "takeover",
        "takeover",
        rx=r"take over for " + _NUM_RX + r" moves?",
        post=_n(),
        kind="n",
    ),
    R(
        "takeover",
        "takeover",
        "play my next move|just this move|play one move|take over for one move",
        n=1,
    ),
    R(
        "takeover",
        "takeover",
        rx=r"take over at (?P<level>[a-z ]+?)(?: level)?",
        post=_level,
    ),
    R(
        "takeover",
        "takeover",
        rx=r"play for me like (?:a |an )?(?P<level>[a-z ]+)",
        post=_level,
    ),
    R("takeover", "takeover", "play like me", level="match"),
    R(
        "spectate",
        "takeover",
        "play yourself|play against yourself|watch you play|show me a game|jarvis versus jarvis|jarvis vs jarvis|self play|demo game|watch mode|spectate|self-play",
    ),
)

# spectator / review / game context controls (controller decides the meaning)
add(
    R("ctl_pause", "ctl", "pause|pause it|hold|hold it there"),
    R("ctl_resume", "ctl", "resume|play|carry on|continue|go|keep going|resume it"),
    R("ctl_next", "ctl", "next|step|next move|step forward|forward|next one"),
    R("ctl_prev", "ctl", "previous|back one|step back|previous move|previous one"),
    R("ctl_faster", "ctl", "faster|speed up|double speed|quicker|go faster"),
    R("ctl_slower", "ctl", "slower|slow down|go slower"),
    R("ctl_normal", "ctl", "normal speed|regular speed|speed normal"),
    R("ctl_half", "ctl", "half speed"),
)

# A.11 game management
add(
    R(
        "start",
        "game",
        "play chess|chess|fancy a game|game of chess|let's play chess|let's play|lets play chess|play a game|a game of chess",
    ),
    R(
        "new_game",
        "game",
        "new game|start a new game|restart|start over|rematch|another game|again|one more|play again|new|one more game|go again|restart the game",
    ),
    R(
        "colour",
        "game",
        "play as white|i'll be white|you be black|give me white|i am white",
        colour="white",
    ),
    R(
        "colour",
        "game",
        "play as black|i'll be black|you be white|give me black|i am black",
        colour="black",
    ),
    R(
        "colour",
        "game",
        "random colours|random colors|you choose|flip a coin|random colour",
        colour="random",
    ),
    *_LEVEL_RULES,
    R(
        "difficulty_adjust",
        "game",
        "easier|make it easier|go easy on me|go easier|too hard",
        direction=-1,
    ),
    R(
        "difficulty_adjust",
        "game",
        "harder|make it harder|don't hold back|do not hold back|too easy",
        direction=1,
    ),
    R("difficulty", "game", "novice|beginner|easy|novice level", level="novice"),
    R("difficulty", "game", "club|club level|intermediate", level="club"),
    R("difficulty", "game", "strong|advanced|strong level", level="strong"),
    R("difficulty", "game", "expert|expert level", level="expert"),
    R("difficulty", "game", "master|master level", level="master"),
    R("difficulty", "game", "grandmaster|full strength|maximum|full|max", level="full"),
    R(
        "difficulty",
        "game",
        rx=r"(?:set )?difficulty (?:to )?(?P<level>[a-z ]+)",
        post=_level,
    ),
    R(
        "difficulty",
        "game",
        rx=r"play at (?P<elo>[a-z0-9 -]+?)(?: elo| rating)?",
        post=_elo,
    ),
    R("difficulty", "game", rx=r"elo (?P<elo>[a-z0-9 -]+)", post=_elo),
    R(
        "time_control",
        "game",
        "untimed|no clock|no time limit|no limit",
        spec="untimed",
    ),
    R(
        "time_control",
        "game",
        "blitz|five minute game|five minutes|5 minute game|five minute",
        spec="5+0",
    ),
    R(
        "time_control",
        "game",
        "ten minute game|ten minutes|10 minute game|ten minute",
        spec="10+0",
    ),
    R(
        "time_control",
        "game",
        "fifteen plus ten|rapid|fifteen ten|15 plus 10",
        spec="15+10",
    ),
    R("time_bullet", "game", "bullet"),
    R("add_time", "game", "add time|more time|give me more time|add a minute"),
    R("pause_clock", "game", "pause the clock|stop the clock|freeze the clock"),
    R("resume_clock", "game", "resume the clock|start the clock|unpause the clock"),
    R(
        "undo",
        "game",
        "undo|undo that|take that back|takeback|go back|back|undo it|take back my move|undo my move",
        n=1,
    ),
    R("undo", "game", rx=r"undo " + _NUM_RX + r" moves?", post=_n()),
    R("undo", "game", rx=r"(?:go back|take back) " + _NUM_RX + r" moves?", post=_n()),
    R("redo", "game", "redo|redo that|redo it|put it back"),
    R(
        "flip",
        "game",
        "flip|flip the board|turn the board|rotate|show it from black's side|rotate the board|flip it|show it from white's side|turn it around",
    ),
    R(
        "zoom",
        "game",
        "bigger|bigger board|zoom in|make it bigger|bigger pieces",
        direction=1,
    ),
    R("zoom", "game", "smaller|smaller board|zoom out|make it smaller", direction=-1),
    R("fullscreen", "game", "fullscreen|full screen|go fullscreen", on=True),
    R(
        "fullscreen",
        "game",
        "exit fullscreen|leave fullscreen|exit full screen",
        on=False,
    ),
    R(
        "labels",
        "game",
        "show labels|show square names|show the labels|show the square labels|labels on|square labels on|show the square names|show square labels",
        on=True,
    ),
    R(
        "labels",
        "game",
        "hide labels|hide the numbers|hide the labels|hide square names|labels off|square labels off|hide the square labels|hide the square names|hide square labels",
        on=False,
    ),
    R(
        "animations",
        "game",
        "reduce animations|fewer animations|less animation",
        reduce=True,
    ),
    R(
        "animations",
        "game",
        "full animations|all animations|more animation",
        reduce=False,
    ),
    R(
        "legal_moves",
        "game",
        "show legal moves|highlight|highlight moves|show moves|highlight legal moves",
    ),
    R(
        "clear_highlights",
        "game",
        "clear highlights|clear the highlights|clear the board|remove highlights",
    ),
    R(
        "resign",
        "game",
        "resign|i resign|i give up|i concede|you win|gg|good game|i'm done|i am done|give up|i surrender",
    ),
    R(
        "offer_draw",
        "game",
        "offer a draw|draw|shall we call it a draw|do you accept a draw|a draw|call it a draw|propose a draw",
    ),
    R("accept", "game", "accept the draw|i accept|accept|yes accept"),
    R("decline", "game", "decline|no draw|i decline|decline the draw"),
    R(
        "claim_draw",
        "game",
        "claim a draw|claim threefold|claim the draw|claim repetition|claim fifty moves",
    ),
    R("save", "game", "save|save the game|save it|save game"),
    R("save", "game", rx=r"save (?:this )?as (?P<name>.+)", post=_name),
    R(
        "load",
        "game",
        "load|load my last game|load last game|load the last game|continue my game|open my last game",
        last=True,
    ),
    R("load", "game", rx=r"load (?:the )?(?:game )?(?P<name>.+)", post=_name),
    R(
        "list_saves",
        "game",
        "open saved games|saved games|list saves|show saved games|show my saves|list saved games",
    ),
    R(
        "export_pgn",
        "game",
        "export pgn|copy the pgn|copy pgn|export the pgn|export the game|copy the game",
    ),
    R(
        "import_pgn",
        "game",
        "import pgn from clipboard|import pgn|paste the pgn|import the pgn|import from clipboard",
    ),
    R(
        "delete_save",
        "game",
        rx=r"delete save (?P<name>.+)|delete the save (?P<name2>.+)|delete saved game (?P<name3>.+)",
        post=None,
    ),
    R(
        "review",
        "game",
        "review the game|post mortem|post-mortem|how did i play|show my mistakes|biggest mistake|key moments|review it|review|start the review|review my game",
    ),
    R("review_next", "review", "next moment|next key moment"),
    R("review_prev", "review", "previous moment|last moment|previous key moment"),
    R("review_goto", "review", rx=r"go to move " + _NUM_RX, post=_n()),
    R(
        "review_skip",
        "review",
        "no thanks|skip the review|skip review|no review|not now thanks|skip it",
    ),
    R(
        "yes",
        "answer",
        "yes|yeah|yep|sure|go on|please do|yes please|of course|ok|okay|do it|affirmative|go ahead|yes sir|please",
    ),
    R("no", "answer", "no|nope|no thanks|negative|nah|don't|do not"),
    R(
        "pause_game",
        "exit",
        "pause the game|hold the game|pause chess|pause game|pause the match",
    ),
    R(
        "resume_game",
        "exit",
        "resume the game|resume chess|back to chess|back to the game|continue the game|carry on with the game|unpause|unpause the game|resume game",
    ),
    R(
        "quit_offer",
        "exit",
        "i'll stop here|i will stop here|stop the game|i'm done for now|i am done for now|that's enough chess|leave chess|stop playing|that's all for now",
    ),
    R(
        "quit",
        "exit",
        "quit chess|exit chess|close chess|end the game|quit|exit|close the game|shut down chess|end chess|end game",
    ),
    R(
        "back_to_jarvis",
        "exit",
        "back to normal|back to jarvis|hand back to jarvis|return to jarvis",
    ),
)

# A.9 analysis and help (once unless "keep ...")
_ANALYSE = (
    "analyse|analyze|analyse this|analyze this|analyse the position|analyze the position|"
    "what's happening|what's going on|what do you think|assess the position|evaluate|"
    "evaluation|how's the position|who's better|who's winning|am i winning|am i losing|"
    "what's the score|score|eval|show the eval|how bad is it|how good is this|what is the score|"
    "who is winning|who is better|how am i doing|how do things stand|how's it looking|show me the eval|"
    "give me the eval|the eval|what's the eval"
)
add(
    R("analyse", "analysis", _ANALYSE),
    R(
        "threats",
        "analysis",
        "threats|what's the threat|what are you threatening|what is he threatening|what am i missing|anything i should know|danger|am i in danger|is my king safe|any tactics|is there a tactic|is there a fork|is there a pin|what's the threat here|what are the threats|any threats",
    ),
    R(
        "plans",
        "analysis",
        "plans|what's the plan|what should my plan be|what is the plan|any plans|what's my plan",
    ),
    R(
        "explain",
        "analysis",
        "explain|explain that|explain that move|explain your move|explain my move|why|why that|why did you play that|why'd you do that|what was that|what's the idea|what's the point of that|what does that do|was that good|was that bad|was that a mistake|was that a blunder|did i blunder|how was my move|rate my move|grade that|what should i have played|what was better|what was best|best move there|what did i miss|explain the last move|why did you do that|what was the idea",
    ),
    R("explain", "analysis", rx=r"explain move " + _NUM_RX, post=_n(), which="number"),
    R(
        "explain_piece",
        "analysis",
        rx=r"explain the (?P<piece>[a-z]+) move",
        post=_piece,
    ),
    R(
        "hint",
        "analysis",
        "hint|a hint|give me a hint|any hints|help|help me|i'm stuck|what now|what should i do|what should i play|what's good here|suggest something|suggestion|nudge|give me a clue|clue|any suggestions|i need a hint|got a hint",
    ),
    R(
        "hint_more",
        "analysis",
        "another hint|more|bigger hint|tell me more|more hint|a bigger hint|another one|give me more",
    ),
    R(
        "hint_full",
        "analysis",
        "just tell me|just tell me the move|what's the best move|best move|show me|show me the move|the best move|what is the best move|tell me the move",
    ),
    R("hint_piece", "analysis", "which piece|where should i look"),
    R(
        "continuous",
        "analysis",
        "keep analysing|keep analyzing|analyse as we go|analyze as we go|analyse every move|analyze every move|keep the analysis on|analysis on",
        mode="analysis",
    ),
    R(
        "continuous",
        "analysis",
        "commentary on|turn commentary on|talk me through it|coach me|coach mode|teach me as we play|commentary please|start commentary|give me commentary|switch commentary on",
        mode="commentary",
    ),
    R(
        "continuous",
        "analysis",
        "keep the eval on|leave the eval on|live eval|eval on|show the live eval|turn the eval on|keep the evaluation on",
        mode="eval",
    ),
    R(
        "continuous",
        "analysis",
        "hints as we go|give me hints every move|hints on|hints every move",
        mode="hints",
    ),
    R(
        "continuous",
        "analysis",
        "keep an eye out for me|warn me about blunders|warn me of blunders",
        mode="blunders",
    ),
    R(
        "continuous",
        "analysis",
        "warn me about time|warn me about the clock",
        mode="time",
    ),
    R(
        "remember_analysis",
        "analysis",
        "remember my analysis settings|remember my settings|remember analysis settings",
    ),
    R(
        "info_where",
        "info",
        rx=r"where(?:'s| is| are) (?:my |the )(?P<piece>[a-z]+?)s?",
        post=_piece,
    ),
    R(
        "info_square",
        "info",
        rx=r"what(?:'s| is) (?:on|at) (?P<sq>[a-z0-9 -]+)",
        post=lambda a, t: _sqpost(a, t),
    ),
    R(
        "info_attackers",
        "info",
        rx=r"what(?:'s| is) attacking (?P<sq>[a-z0-9 -]+)|who(?:'s| is) attacking (?P<sq2>[a-z0-9 -]+)",
        post=lambda a, t: _sqpost(a, t),
    ),
    R(
        "info_piece_moves",
        "info",
        rx=r"what can my (?P<piece>[a-z]+?) do|show my (?P<piece2>[a-z]+?)'?s? moves|legal moves for the (?P<piece3>[a-z]+)|show me my (?P<piece4>[a-z]+?)'?s? moves",
        post=lambda a, t: _piece2(a, t),
    ),
    R(
        "info_material",
        "info",
        "what's my material|material count|who's up material|material|what is my material|who is up material|count the material|material balance",
    ),
    R(
        "info_read",
        "info",
        "read the moves|read me the moves|read the game|read the moves back",
    ),
    R("info_read_last", "info", rx=r"read the last " + _NUM_RX + r" moves?", post=_n()),
    R(
        "info_last_ai",
        "info",
        "what did you play|what was your last move|your last move|what was your move",
    ),
    R("info_last_mine", "info", "what was my last move|my last move|what did i play"),
    R(
        "info_movenum",
        "info",
        "how many moves|what move are we on|move number|what move is it|which move are we on",
    ),
    R(
        "info_turn",
        "info",
        "whose turn is it|whose move is it|who's turn is it|who's move is it|whose turn|who is to move",
    ),
    R(
        "info_time",
        "info",
        "how much time do i have|time left|clock|how much time is left|what's the time|what time is left|what's the clock|how much time have i got",
    ),
    R(
        "info_opening",
        "info",
        "what's the opening|what opening is this|name the opening|which opening is this|what opening are we in|what's this opening",
    ),
)


def _sqpost(args: dict, t: str) -> dict | None:
    rest = args.get("sq") or args.get("sq2") or ""
    sqs = voice_moves.find_squares(rest)
    if not sqs:
        return None
    return {"square": chess.square_name(sqs[-1])}


def _piece2(args: dict, t: str) -> dict | None:
    for k in ("piece", "piece2", "piece3", "piece4"):
        if args.get(k):
            pt = voice_moves.find_piece_type(args[k])
            if pt:
                return {"piece": chess.piece_name(pt)}
    return None


# A.12 settings
add(
    R(
        "volume",
        "settings",
        "louder|commentary volume up|volume up|turn it up|speak up",
        direction=1,
    ),
    R(
        "volume",
        "settings",
        "quieter|commentary volume down|volume down|turn it down|softer",
        direction=-1,
    ),
    R(
        "mute",
        "settings",
        "mute commentary|mute|mute yourself|mute the commentary",
        on=True,
    ),
    R("mute", "settings", "unmute|unmute commentary|unmute the commentary", on=False),
    R(
        "announce",
        "settings",
        "announce your moves|announce moves|announce the moves|do announce your moves",
        on=True,
    ),
    R("announce", "settings", "just the moves please|just the moves", on=False),
    R("detail", "settings", "say less|be brief|brief|shorter", level="brief"),
    R(
        "detail",
        "settings",
        "say more|more detail|in more detail|detailed",
        level="detailed",
    ),
    R(
        "audience",
        "settings",
        "simpler explanations|explain like i'm a beginner|explain like i'm five|explain it simply|explain simply",
        audience="novice",
    ),
    R(
        "audience",
        "settings",
        "explain like i'm advanced|advanced explanations|explain like i'm an expert|technical explanations",
        audience="advanced",
    ),
    R(
        "confirm",
        "settings",
        "confirm my moves|say it back to me|confirm moves",
        on=True,
    ),
    R("confirm", "settings", "stop confirming|don't confirm|no confirmation", on=False),
    R(
        "stop_ack",
        "settings",
        "silent acknowledgements|don't acknowledge stops|silent stops|no acknowledgement|silent acknowledgments",
        mode="silent",
    ),
    R(
        "stop_ack",
        "settings",
        "say very good when i stop you|spoken acknowledgements|acknowledge stops|say very good",
        mode="spoken",
    ),
    R(
        "ai_test",
        "settings",
        "test the commentary connection|test commentary|test the connection|test the ai",
    ),
    R(
        "ai_model",
        "settings",
        "which model are you using|what model are you using|which model|what model is this",
    ),
    R(
        "ai_calls_left",
        "settings",
        "how many ai calls are left|how many calls are left|ai calls left|how many explanations are left",
    ),
    R(
        "ai_online",
        "settings",
        "use online explanations|go online|online explanations",
        on=True,
    ),
    R(
        "ai_online",
        "settings",
        "use offline explanations|go offline|offline explanations",
        on=False,
    ),
    R("eval_bar", "settings", "eval bar on|show the eval bar", on=True),
    R("eval_bar", "settings", "eval bar off|hide the eval bar", on=False),
    R("theme", "settings", "dark board|dark mode|dark theme", theme="dark"),
    R("theme", "settings", "light board|light mode|light theme", theme="light"),
    R(
        "intro",
        "settings",
        "turn the intro off|intro off|no intro|skip the intro",
        mode="off",
    ),
    R("intro", "settings", "short intro|shorter intro", mode="short"),
    R("intro", "settings", "full intro|turn the intro on|intro on", mode="full"),
)

# Filler words that may sit before a question and that never change its meaning
_LEAD = re.compile(r"^(?:and |so |ok |okay |well |um |uh |hmm )+")


def _try(rule: Rule, t: str) -> Intent | None:
    if t in rule.phrases:
        return Intent(rule.name, dict(rule.fixed), rule.group)
    for rx in rule.patterns:
        m = rx.match(t)
        if m:
            args = {k: v for k, v in m.groupdict().items() if v is not None}
            args.update(rule.fixed)
            # normalise numbered alternates (name2 -> name)
            for k in list(args):
                base = k.rstrip("0123456789")
                if base != k and base not in args:
                    args[base] = args.pop(k)
            if rule.post:
                out = rule.post(args, t)
                if out is None:
                    continue
                args = out
            return Intent(rule.name, args, rule.group)
    return None


def is_stop(text: str) -> bool:
    return any(v in STOP_PHRASES for v in text_norm.variants(text))


def match_narrow_stop(text: str) -> Intent | None:
    for t in text_norm.variants(text):
        for r in NARROW_STOPS:
            hit = _try(r, t)
            if hit:
                return hit
    return None


def classify(text: str, groups: set[str] | None = None) -> Intent | None:
    """First matching non-move, non-stop intent, in catalogue order."""
    cands = []
    raw = text_norm.clean(text)
    for t in (*text_norm.variants(text), _LEAD.sub("", raw)):
        if t and t not in cands:
            cands.append(t)
    for t in cands:
        for rule in RULES:
            if groups is not None and rule.group not in groups:
                continue
            hit = _try(rule, t)
            if hit:
                return hit
    return None


INTERROGATIVE = re.compile(
    r"^(?:what|where|which|who|whom|whose|how|why|when|is|are|am|does|do|did|can|could|should|"
    r"was|were|will|would|tell me|show me|read)\b"
)


def looks_like_question(text: str) -> bool:
    return bool(INTERROGATIVE.match(text_norm.strip_wrappers(text)))
