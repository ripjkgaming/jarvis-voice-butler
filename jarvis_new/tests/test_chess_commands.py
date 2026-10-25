"""Every Appendix A intent (A.8-A.12) maps to the right command, 5+ phrasings each."""

import pytest

from games.chess import commands

# intent name -> spoken phrasings (all from Appendix A, plus polite wrappers)
CASES: dict[str, list[str]] = {
    # A.10 take-over and spectator
    "takeover": [
        "take control",
        "take it over",
        "auto",
        "take over",
        "take the wheel",
        "you play",
        "play for me",
        "play my side",
        "autopilot",
        "you take it",
        "your turn to drive",
        "please take over for me",
        "play this for me",
    ],
    "takeover_finish": [
        "finish this",
        "finish the game",
        "finish it for me",
        "play it out",
        "play the rest",
    ],
    "handback": [
        "I'll take it from here",
        "give it back",
        "my turn",
        "let me play",
        "I've got it",
        "hands off",
        "back to me",
        "I'll drive",
        "stop playing for me",
        "return control",
        "release",
    ],
    "spectate": [
        "play yourself",
        "play against yourself",
        "watch you play",
        "show me a game",
        "Jarvis versus Jarvis",
        "self-play",
        "demo game",
        "watch mode",
        "spectate",
    ],
    # spectator controls
    "ctl_pause": ["pause"],
    "ctl_resume": ["resume", "carry on"],
    "ctl_next": ["next", "step", "next move"],
    "ctl_prev": ["previous", "back one"],
    "ctl_faster": ["faster", "speed up", "double speed"],
    "ctl_slower": ["slower", "slow down"],
    "ctl_normal": ["normal speed"],
    "ctl_half": ["half speed"],
    # A.11 game management
    "start": [
        "let's play chess",
        "play chess",
        "chess",
        "fancy a game",
        "game of chess",
    ],
    "new_game": [
        "go again",
        "restart the game",
        "new game",
        "start a new game",
        "restart",
        "start over",
        "rematch",
        "another game",
        "again",
        "one more",
        "play again",
    ],
    "colour": [
        "play as white",
        "I'll be white",
        "I'll be black",
        "you be white",
        "give me black",
        "random colours",
        "you choose",
        "flip a coin",
    ],
    "difficulty": [
        "novice",
        "beginner",
        "club",
        "club level",
        "intermediate",
        "strong",
        "advanced",
        "expert",
        "master",
        "grandmaster",
        "full strength",
        "maximum",
        "set difficulty to expert",
        "play at fifteen hundred",
        "play at 1800 elo",
    ],
    "difficulty_adjust": [
        "easier",
        "make it easier",
        "harder",
        "make it harder",
        "go easy on me",
        "don't hold back",
    ],
    "time_control": [
        "no limit",
        "five minutes",
        "ten minutes",
        "untimed",
        "no clock",
        "no time limit",
        "blitz",
        "five minute game",
        "ten minute game",
        "fifteen plus ten",
        "rapid",
    ],
    "time_bullet": ["bullet"],
    "add_time": ["add time"],
    "pause_clock": ["pause the clock"],
    "undo": [
        "undo it",
        "undo my move",
        "take back my move",
        "undo",
        "undo that",
        "take that back",
        "takeback",
        "go back",
        "back",
        "undo two moves",
    ],
    "redo": ["redo", "redo that"],
    "flip": [
        "flip it",
        "rotate the board",
        "turn it around",
        "flip",
        "flip the board",
        "turn the board",
        "rotate",
        "show it from black's side",
    ],
    "zoom": ["bigger", "bigger board", "zoom in", "smaller"],
    "fullscreen": ["fullscreen", "exit fullscreen"],
    "labels": ["show labels", "hide labels", "show square names", "hide the numbers"],
    "animations": ["reduce animations", "full animations"],
    "legal_moves": ["show legal moves", "highlight"],
    "clear_highlights": ["clear highlights"],
    "resign": [
        "give up",
        "i surrender",
        "resign",
        "I resign",
        "I give up",
        "I concede",
        "you win",
        "gg",
        "good game",
        "I'm done",
    ],
    "offer_draw": [
        "offer a draw",
        "draw",
        "shall we call it a draw",
        "do you accept a draw",
        "propose a draw",
        "call it a draw",
    ],
    "accept": ["accept the draw", "I accept"],
    "decline": ["decline", "no draw"],
    "claim_draw": ["claim a draw", "claim threefold"],
    "save": [
        "save",
        "save the game",
        "save as my game one",
        "save this as blitz one",
        "save game",
        "save it",
    ],
    "load": [
        "load",
        "load my last game",
        "load blitz one",
        "continue my game",
        "load the last game",
        "open my last game",
    ],
    "list_saves": ["open saved games"],
    "export_pgn": ["export PGN", "copy the PGN"],
    "import_pgn": ["import PGN from clipboard"],
    "delete_save": ["delete save blitz one"],
    "review": [
        "review the game",
        "post-mortem",
        "how did I play",
        "show my mistakes",
        "biggest mistake",
        "key moments",
    ],
    "review_next": ["next moment"],
    "review_prev": ["previous moment"],
    "review_goto": ["go to move five"],
    "review_skip": ["no thanks", "skip the review"],
    "pause_game": ["pause the game", "hold the game"],
    "resume_game": ["resume the game", "back to the game", "back to chess"],
    "quit_offer": [
        "I'll stop here",
        "stop the game",
        "I'm done for now",
        "that's enough chess",
        "leave chess",
    ],
    "quit": [
        "quit chess",
        "exit chess",
        "close chess",
        "end the game",
        "quit",
        "end chess",
    ],
    "back_to_jarvis": ["back to normal", "back to Jarvis"],
    # A.9 analysis and help
    "analyse": [
        "what is the score",
        "who is winning",
        "who is better",
        "how am I doing",
        "analyse",
        "analyze",
        "analyse this",
        "analyse the position",
        "what's happening",
        "what's going on",
        "what do you think",
        "assess the position",
        "evaluate",
        "evaluation",
        "how's the position",
        "who's better",
        "who's winning",
        "am I winning",
        "am I losing",
        "what's the score",
        "score",
        "eval",
        "show the eval",
        "how bad is it",
        "how good is this",
    ],
    "threats": [
        "what are the threats",
        "any threats",
        "threats",
        "what's the threat",
        "what are you threatening",
        "what is he threatening",
        "what am I missing",
        "anything I should know",
        "danger",
        "am I in danger",
        "is my king safe",
        "any tactics",
        "is there a tactic",
        "is there a fork",
        "is there a pin",
    ],
    "plans": ["plans", "what's the plan", "what should my plan be"],
    "explain": [
        "explain the last move",
        "why did you do that",
        "what was the idea",
        "explain",
        "explain that",
        "explain that move",
        "explain your move",
        "explain my move",
        "why",
        "why that",
        "why did you play that",
        "why'd you do that",
        "what was that",
        "what's the idea",
        "what's the point of that",
        "what does that do",
        "was that good",
        "was that bad",
        "was that a mistake",
        "was that a blunder",
        "did I blunder",
        "how was my move",
        "rate my move",
        "grade that",
        "what should I have played",
        "what was better",
        "what was best",
        "best move there",
        "what did I miss",
        "explain move twelve",
    ],
    "explain_piece": ["explain the knight move", "explain the bishop move"],
    "hint": [
        "any suggestions",
        "i need a hint",
        "got a hint",
        "hint",
        "a hint",
        "give me a hint",
        "any hints",
        "help",
        "help me",
        "I'm stuck",
        "what now",
        "what should I do",
        "what should I play",
        "what's good here",
        "suggest something",
        "suggestion",
        "nudge",
        "give me a clue",
        "clue",
    ],
    "hint_more": [
        "another hint",
        "more",
        "bigger hint",
        "tell me more",
        "a bigger hint",
        "give me more",
    ],
    "hint_full": [
        "just tell me",
        "just tell me the move",
        "what's the best move",
        "best move",
        "show me",
        "show me the move",
    ],
    "hint_piece": ["which piece", "where should I look"],
    "continuous": [
        "keep analysing",
        "analyse as we go",
        "analyse every move",
        "commentary on",
        "turn commentary on",
        "talk me through it",
        "coach me",
        "coach mode",
        "teach me as we play",
        "keep the eval on",
        "leave the eval on",
        "live eval",
        "hints as we go",
        "give me hints every move",
        "keep an eye out for me",
        "warn me about blunders",
        "warn me about time",
    ],
    "remember_analysis": ["remember my analysis settings"],
    "info_where": ["where's my queen", "where are my rooks", "where's my king"],
    "info_square": ["what's on e four", "what's on f3", "what's on d five"],
    "info_attackers": ["what's attacking e five", "what's attacking f7"],
    "info_piece_moves": [
        "what can my knight do",
        "show my knight's moves",
        "legal moves for the bishop",
    ],
    "info_material": ["what's my material", "material count", "who's up material"],
    "info_read": ["read the moves"],
    "info_read_last": ["read the last five moves", "read the last 3 moves"],
    "info_last_ai": ["what did you play", "what was your last move"],
    "info_last_mine": ["what was my last move"],
    "info_movenum": ["how many moves", "what move are we on"],
    "info_turn": ["whose turn is it", "whose move is it", "who is to move"],
    "info_time": [
        "how much time do I have",
        "time left",
        "clock",
        "how much time is left",
        "how much time have I got",
    ],
    "info_opening": [
        "what's the opening",
        "what opening is this",
        "name the opening",
        "which opening is this",
    ],
    # A.12 voice settings
    "volume": [
        "volume up",
        "volume down",
        "speak up",
        "louder",
        "quieter",
        "commentary volume up",
        "commentary volume down",
    ],
    "mute": ["mute commentary", "unmute"],
    "announce": ["announce your moves", "just the moves please"],
    "detail": [
        "shorter",
        "detailed",
        "say less",
        "be brief",
        "say more",
        "more detail",
    ],
    "audience": [
        "simpler explanations",
        "explain like I'm a beginner",
        "explain like I'm advanced",
    ],
    "confirm": ["confirm my moves", "say it back to me", "stop confirming"],
    "stop_ack": [
        "silent acknowledgements",
        "don't acknowledge stops",
        "say very good when I stop you",
    ],
    "ai_test": ["test the commentary connection"],
    "ai_model": ["which model are you using"],
    "ai_calls_left": ["how many AI calls are left"],
    "ai_online": ["use offline explanations", "use online explanations"],
    "eval_bar": ["eval bar on", "eval bar off"],
    "theme": ["dark board", "light board"],
    "intro": ["turn the intro off", "short intro", "full intro"],
}

# phrases that belong to a *narrower* rule listed earlier in the catalogue
ALIASES = {
    ("stop_ack", "stop confirming"): "stop_confirm",
    ("confirm", "stop confirming"): "stop_confirm",
    ("explain_piece", "explain the knight move"): "explain_piece",
    ("labels", "hide the numbers"): "labels",
    ("continuous", "keep the eval on"): "continuous",
    ("announce", "just the moves please"): "announce",
    ("info_where", "where's my king"): "info_where",
    ("hint_piece", "where should I look"): "hint_piece",
    ("review_skip", "no thanks"): "review_skip",
}


def _cases():
    out = []
    for name, phrases in CASES.items():
        for phrase in phrases:
            out.append(pytest.param(name, phrase, id=f"{name}::{phrase}"))
    return out


@pytest.mark.parametrize(("name", "phrase"), _cases())
def test_phrase_maps_to_intent(name, phrase):
    want = ALIASES.get((name, phrase), name)
    # the controller tries narrow stops before the catalogue
    hit = commands.match_narrow_stop(phrase) or commands.classify(phrase)
    assert hit is not None, f"{phrase!r} matched nothing"
    assert hit.name == want, f"{phrase!r} -> {hit.name}, wanted {want}"


RICH = [
    "takeover", "takeover_finish", "handback", "spectate", "start", "new_game", "colour", "difficulty",
    "difficulty_adjust", "time_control", "undo", "flip", "resign", "offer_draw", "save", "load", "review",
    "quit_offer", "quit", "analyse", "threats", "explain", "hint", "hint_more", "hint_full", "continuous",
]  # fmt: skip


@pytest.mark.parametrize("name", RICH)
def test_main_intents_have_five_phrasings(name):
    assert len(CASES[name]) >= 5, name


def test_catalogue_is_large():
    assert sum(len(v) for v in CASES.values()) >= 400


# --------------------------------------------------------------- A.8 stop
STOP = [
    "stop", "stop it", "stop that", "stop now", "stop talking", "stop analysing", "stop analyzing",
    "stop the analysis", "stop commentary", "stop commenting", "stop explaining", "stop thinking",
    "quiet", "be quiet", "quiet please", "silence", "hush", "shh", "shush", "enough", "that's enough",
    "that'll do", "that will do", "okay okay", "alright alright", "cut it", "cut it out", "cut", "halt",
    "hold on", "hold it", "wait", "wait wait", "pause commentary", "shut up", "zip it", "leave it",
    "drop it", "forget it", "nevermind", "never mind", "cancel", "cancel that", "no no", "not now",
    "later", "I get it", "got it", "I know", "thanks that's fine", "thank you that's all",
]  # fmt: skip


@pytest.mark.parametrize("phrase", STOP)
def test_global_stop_phrases(phrase):
    assert commands.is_stop(phrase)
    assert commands.is_stop(phrase.upper() + "!")
    assert commands.is_stop(f"please {phrase}")


NARROW = {
    "stop_eval": ["stop the eval", "hide the eval"],
    "stop_hints": ["stop the hints", "no more hints"],
    "stop_review": ["stop the review"],
    "stop_announce": ["stop announcing", "don't announce your moves"],
    "stop_time_warn": ["stop warning me about time"],
    "stop_confirm": ["stop confirming"],
    "stop_spectating": ["stop spectating", "stop playing yourself"],
}


@pytest.mark.parametrize(
    ("name", "phrase"), [(n, p) for n, ps in NARROW.items() for p in ps]
)
def test_narrow_stops(name, phrase):
    assert not commands.is_stop(phrase)
    hit = commands.match_narrow_stop(phrase)
    assert hit is not None and hit.name == name


def test_stop_the_game_is_pause_not_stop():
    assert not commands.is_stop("stop the game")
    assert commands.classify("stop the game").name == "quit_offer"
    assert commands.classify("pause the game").name == "pause_game"


# ------------------------------------------------------------- wake / numbers
@pytest.mark.parametrize(
    "text",
    [
        "hey Jarvis what's the weather",
        "Hey, Jarvis",
        "OK Jarvis",
        "okay jarvis",
        "Jarvis, what time is it",
        "jarvis",
    ],
)
def test_wake_phrase_at_start(text):
    from games.chess.text_norm import is_wake_phrase

    assert is_wake_phrase(text)


@pytest.mark.parametrize(
    "text", ["knight f3, Jarvis", "e4 sir", "play e4 please Jarvis", "thanks Jarvis"]
)
def test_jarvis_as_suffix_is_fine(text):
    from games.chess.text_norm import is_wake_phrase

    assert not is_wake_phrase(text)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("twelve", 12),
        ("five", 5),
        ("fifteen hundred", 1500),
        ("twenty one", 21),
        ("1500", 1500),
        ("two hundred", 200),
    ],
)
def test_parse_number(text, value):
    assert commands.parse_number(text) == value


def test_arguments_are_extracted():
    assert commands.classify("undo two moves").args["n"] == 2
    assert commands.classify("take over for five moves").args["n"] == 5
    assert commands.classify("save as blitz one").args["name"] == "blitz one"
    assert commands.classify("play at fifteen hundred").args["elo"] == 1500
    assert commands.classify("what's on e four").args["square"] == "e4"
    assert commands.classify("where's my queen").args["piece"] == "queen"
    assert commands.classify("explain move twelve").args["n"] == 12
    assert commands.classify("take over at expert level").args["level"] == "expert"
    assert commands.classify("play for me like a master").args["level"] == "master"


def test_questions_are_not_moves():
    assert commands.looks_like_question("what's on e4")
    assert commands.looks_like_question("why is my bishop bad here")
    assert not commands.looks_like_question("knight to f3")
