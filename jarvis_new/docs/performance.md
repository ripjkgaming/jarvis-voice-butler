# Performance checks — 2026-10-02

The microphone cleaner now defaults to the installed GTCRN model instead of
DPDFNet. This processes every captured audio block; wakeword audio, joining
audio and live-call audio still share the cleaned stream. Explicit
`JARVIS_DENOISE=dpdfnet` remains supported. An older installation with only
DPDFNet falls back to that model when no model is explicitly selected.

A controlled benchmark on this laptop fed both models the same three seconds
of seeded audio at 48 kHz in 1,536-sample blocks, using the wake client's
Python environment and one ONNX worker. Both produced 93 complete blocks.

| Model | CPU seconds | CPU as percentage of one core |
| --- | ---: | ---: |
| DPDFNet | 2.374 | 79.1% |
| GTCRN | 1.005 | 33.5% |

That is about 58% less denoiser CPU for this input. This measures processing
cost, not recognition accuracy in every room. Complete-frame, resampling,
delay alignment and fallback behavior remain covered by tests.

The HUD performance changes cache decorative dust, retain animated foreground
particles, cap ambient rendering at 30 FPS and DPR 1.5, and schedule baked
films at their own 12–24 FPS instead of the monitor refresh rate. Hidden or
offscreen films pause. Orb films share their cache across remounts. This avoids
a large multi-frame full-screen cache that could trade CPU load for memory
pressure.

Live process samples are taken while the normal HUD is visible, before locking
the desktop. Percentages are relative to one CPU core, so totals can exceed
100%. Browser video decoding and other applications contribute additional
load; these changes do not promise a particular whole-system CPU percentage.

After the full test run stopped, 15-second process samples before and after
the native restart showed:

| Component | Before | After |
| --- | ---: | ---: |
| Native shell | 26.1% | 13.6% |
| Visible normal HUD renderer | 73.1% | 20.6% |
| Wake client | 54.0% | 19.6% |

The shell plus HUD renderer used about 66% less CPU in these samples. The wake
client used about 64% less. These are live observations across a restart,
not a controlled whole-system benchmark. The separate denoiser benchmark
above isolates the model change. The restarted native webview was verified
to load `app/page-55c979970bac8589.js`, report normal mode and remain visible;
the wake log confirmed `noise suppression on (gtcrn)`.
A second 15-second sample confirmed 31.5% combined shell/HUD CPU and 18.0%
wake-client CPU, with stable resident memory and no swap in these processes.

Validation: 3,073 Python tests passed (13 skipped), 186 frontend unit tests
passed, and 79 native Rust tests passed with one test thread because existing
tests mutate process-global environment variables. Production export and
native build passed. Browser checks covered 22 voice/join scenarios, warning
lighting, and ambient lifecycle behavior at DPR 1 and 2. The dust loop repeats
both position and velocity; hidden/offscreen/reduced-motion checks verify
that ongoing playback stops.
