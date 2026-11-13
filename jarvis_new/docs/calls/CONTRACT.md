# Jarvis Calls: shared contract (parent-owned; workers must not change it, report if wrong)

Package: `src/calls/` (new). Tests: `tests/calls/`. Data dir: `~/.jarvis/calls/` (env `JARVIS_CALLS_DIR` overrides; tests MUST use tmp dir). SQLite db `calls.db`.
Research: ~/.jarvis/agents/calls-research/REPORT.md (route = WhatSie CDP + PipeWire null-sinks; unverified, no real call possible now: mock the WhatsApp layer behind an interface).

## Module ownership (one worker each, non-overlapping files)
- store (W1): `src/calls/store.py`, `contacts.py`, `rules.py`, `calllog.py`, `memory_bridge.py`
- audio+wa (W2): `src/calls/audio_route.py`, `wa_call.py`, `call_bridge.py`
- agent (W3): `src/calls/agent.py`, `policy.py`, `events.py`
- sched+voice (W4): `src/calls/scheduler.py`, `voice_commands.py`, `rules_parser.py`
- UI (Opus): `frontend/components/calls/*`, route `frontend/app/calls-demo/` (mock data), nothing in src/calls.

## Python interfaces (W1 implements, others import)
```python
# contacts.py
@dataclass Contact: id:int; name:str; number:str(E.164); relationship:str; notes:str; trust:Literal["family","work","unknown","blocked"]; policy:dict  # {"answer":"auto|message|owner|reject","persona":"jarvis|owner","greeting":str|None,"allow_topics":[...]}
add_contact(**kw)->Contact; get_by_number(n)->Contact|None; find(name)->list[Contact]; update_contact(id,**kw); delete_contact(id); list_contacts(); import_whatsapp(rows:list[dict])->int
# rules.py  (auto-reply windows)
@dataclass Rule: id; kind:Literal["answer_all","answer_unknown","reject","message_only"]; start:float(epoch)|None; end:float|None; daily_from:"HH:MM"|None; daily_to:"HH:MM"|None; message:str; allow:list[str]; deny:list[str]; created_by:str; active:bool
add_rule(**kw)->Rule; cancel_rule(id=None)->int  # None=cancel all (stop command); active_rules(now)->list[Rule]; decide(number, now)->Decision(action,reason,rule_id,contact)  # action: answer|message|owner|reject ; deny list wins, then allow, then contact policy, then rules
# calllog.py  (append-only, auditable)
start_call(direction:"in"|"out", number, contact_id, goal)->call_id; add_turn(call_id, speaker:"caller"|"jarvis"|"owner", text, ts=None); end_call(call_id, outcome, summary); get_call(call_id); recent(n); audit(kind, detail:dict)  # every rule change/decision/takeover logged; list_audit(n)
# memory_bridge.py
remember_person(contact_id, fact); recall_for_call(contact)->str  # facts + last 3 call summaries + standing instructions; write call summary into src/memory.py vault Threads
```
## Call event stream (W3 emits, UI consumes). JSON over existing HUD event channel as `{"type":"call", ...}`
`{type:"call", event:"state", call_id, state:"ringing|handling|hold|owner|ended", direction, caller:{name,number,trust,relationship,notes}|null, goal, started_at, mode:"jarvis|owner_voice", ai_disclosed:bool, recording:bool}`
`{type:"call", event:"transcript", call_id, speaker:"caller|jarvis|owner", text, partial:bool, ts}`
`{type:"call", event:"speaking", call_id, level:0..1}`   # jarvis output level for visualiser
`{type:"call", event:"escalation", call_id, reason}`      # needs owner approval
`{type:"call", event:"summary", call_id, summary, outcome}`
Voice control commands accepted by the agent (`events.py: handle_command(name)`): `takeover`, `hold`, `resume`, `hangup`, `accept`, `decline`, `take_message`, `stop_all` (cancel rules).

## Safety (all workers)
AI disclosure at call start default ON (setting `disclose_ai`); recording/transcription gated by setting `record_calls` with spoken notice; never share sensitive data / pay / commit owner: escalate with `escalation` event; takeover instant; all rule/decision/call logged to audit.
## Repo rules
No git checkout/stash/reset. No prettier --write on dirs (only files you create). Don't edit existing files unless essential: if so, minimal edit and list it in your result. Python tests with `uv run pytest tests/calls -q` (check pyproject). No UI work outside the UI worker.
