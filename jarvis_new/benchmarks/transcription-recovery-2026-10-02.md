# Transcription recovery — 2 October 2026

## Confirmed defects and changes

- Normal wake/PTT discarded the existing microphone queue and opened another stream after joining. Speech during connection could be lost before the agent subscribed. These calls now retain the original capture stream, a short onset tail, and bounded join audio, then replay once after readiness. School's already-transcribed opening question keeps its existing backlog-discard policy.
- A completed greeting with fractional timestamps could cover a later user caption stored in the same whole second. An unfinished greeting could also override every newer user line. All three caption surfaces now share chronological selection with a user-preserving tie rule.
- A follow-up watcher started after greeting ignored speech already in progress. Agent state changes could also arm an idle deadline during user speech. The watcher now tracks both states, gives a continuous-audio pause prompt, and uses a bounded 60-second hold without forcing possible noise into a model/tool turn.
- Failed LiveKit SpeechHandles resolve their awaitable without raising. Presence speech now checks the stored error; interrupted greetings neither append a false full caption nor retry over an active conversation.

The capture queue aborts with a retry caption on overflow, rather than forwarding a truncated command. Muting invalidates buffered audio. Catch-up removes only confirmed digital zero; nonzero ambient audio can retain join delay because quiet speech must not be discarded speculatively. Microphone gain, AEC settings, voice model chain, and speech thresholds are unchanged.

## Validation

- 311 focused Python tests passed; 3 opt-in live-model tests skipped.
- After independent review added mute-race and configured-endpoint cases, the final capture suite passed all 83 tests.
- 178 frontend Node tests passed.
- Production frontend build and native debug/release builds passed.
- Final production-export headless caption checks passed across normal, school, and return, including an in-flight poll; no page errors.
- Final joining-animation headless check passed 18 lifecycle scenarios at DPR 2.
- Direct synthetic provider and roomless framework probes both transcribed the known phrase.
- Integrated capture-buffer/framework probe began speech two seconds before readiness. All words survived and the provider/framework produced the exact intended transcription in one bounded provider connection. No physical microphone, speaker, real room, or tools were used in this probe.

The integrated probe's reply repeated its greeting instead of returning only the requested word. This is recorded separately from transcription success; it does not establish general answer correctness. The event trace showed no duplicate trailing empty client turn. No provider-protocol change was inferred from that one response.

Live microphone amplitude/VAD diagnosis retained no recordings. The observed continuous VAD activity is evidence of a possible missing endpoint, not proof that background noise caused every reported call failure. Physical microphone-to-screen confirmation after activation remains a separate check.

The updated native app was restarted on 2 October at 10:59 Singapore time. The bridge was healthy after 50 seconds; the worker registered successfully and wake listening resumed with the microphone unmuted and normal mode preserved.

## Related requested changes

The separate GPT-6 Astra High joining-animation chat delivered stage-specific reactor and school-bar motion, truthful slow-join explanations, reduced-motion behavior, visibility suspension, and failure/retry states. The unified export passed its 18-scenario headless lifecycle check.

The computer-use worker now defaults to GPT-6 Luna, retaining low effort and all existing action/permission controls. No persisted Terra override was found. Its 44 focused tests passed, with one optional CLI smoke skipped; no desktop action or live navigation-model turn was performed.

## Follow-up: delayed speech endpoint correction

The subsequent live attempt exposed a separate backend stall: the first local speech endpoint arrived roughly 44 seconds after connection, with a provider first-audio metric exceeding 52 seconds. A synthetic capture audit through the real `rtc.AudioSource` found a stable 2.301-second join-buffer delay, not accumulating drift or double pacing. That buffer delay cannot explain the much longer endpoint stall.

Gemini's native activity detection was rechecked successfully. Realtime sessions now use native activity detection and `turn_detection="realtime_llm"`, with no local Silero VAD or realtime Silero warmup. Native sessions also bypass the old manual-turn suppression and delayed tool-response nudge. Local speech pipelines keep their existing detector settings. The model chain and UI visuals are unchanged by this correction.

A bounded actual `AgentSession` probe used the full static prompt and 169 inert tool schemas in one provider connection. It completed the greeting and transcribed both synthetic phrases exactly: “Hello Jarvis. Please say the word ready.” and “Now say the word finished.” Replies were “Ready, Sir.” and “Finished, Sir.” Response audio began approximately 1.025 and 1.024 seconds after synthetic speech ended. No manual activity markers, extra empty turns, tool calls, or session errors occurred. This excludes physical microphone capture, room transport, and speaker playback; it is not a measured end-to-end laptop latency guarantee. A separate full-prompt manual probe also answered correctly, so no prompt/standby change was justified.

Backend validation passed 172 tests, with three opt-in live tests skipped. Fourteen new regressions cover native configuration, actual SDK speech-state events without network access, and native bypass of the manual-turn workaround. The app was restarted at 11:13 Singapore time; bridge health passed, the worker registered, wake listening resumed, and the microphone remained unmuted. Physical microphone-to-screen confirmation is pending.

A separate native `AgentSession` probe verified tool continuation in one bounded connection. Its sole callable was an inert in-memory `synthetic_check` tool; the model called it once and spoke its returned check word correctly. The probe completed in 12.593 seconds, including greeting and synthetic audio delivery, without an extra empty client turn or session error. No desktop actions were available in this probe.

## Independent GPT-6.1 Sol evaluation

The evaluator found a follow-up lifecycle edge case: Gemini emits its SDK speech-start event after the utterance, so speech beginning near the 15-second listening deadline could be cut off. A local, content-free energy observer now guards follow-up, idle/away, and greeting-retry timers independently of model endpointing. It forwards identical audio frames, retains only timestamps, preserves automatic gain control, and caps continuous-input holds at 60 seconds. It cannot commit a turn or execute a tool. Very quiet input below its energy threshold remains a limitation of this conservative timer guard.

The regression reproduces speech beginning at 14 seconds while the SDK still reports listening, verifies that the call survives the 15-second deadline, and checks a fresh listening window after the input stops. An actual native RTC stream test verifies that the observer receives frames and forwards identical PCM without a room, microphone, speaker, or provider connection.

Independent evaluation also passed all 178 frontend tests, five production caption scenarios, and 18 joining-animation scenarios at DPR 2. All 60 frontend export files matched the running shell's assets byte for byte. The final runtime was restarted at 11:24 Singapore time, registered its worker, and resumed wake listening with the microphone unmuted. A fresh physical microphone-to-screen attempt has not been confirmed by the user.

The final backend regression run passed 181 tests with three opt-in live tests skipped. Sol independently passed 196 broader Python checks before the lifecycle addition and reran all 40 focused voice/lifecycle checks afterward. These suites overlap and must not be added together. The evaluator approved the final source with no remaining actionable blockers found in the reviewed scope. Primary-model synthetic speech/tool continuation and offline fallback configuration were checked; live fallback behavior and real desktop navigation were not exercised.
