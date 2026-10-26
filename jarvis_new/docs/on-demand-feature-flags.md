# Owner-requested checks and background collection

On the owner desktop (`JARVIS_LOCAL=1`), requested build status and current health diagnostics are available by default. `JARVIS_BUILD_TOOLS=0` and `JARVIS_HEALTH_TOOLS=0` separately disable those requests. Existing authentication, loopback, private-call and guest guards still apply.

`JARVIS_BUILD_MONITOR=1` remains the explicit background build-observer opt-in. With it off, an owner may register an already-running build and request its status or bounded tail. Registration never launches a build. Status refresh happens only on request; no observer thread or observer owner-lock is created. Stopping monitoring does not terminate the registered process.

`JARVIS_HEALTH_RECORDER=1` remains the explicit volatile history/event-recorder opt-in. With it off, a requested health report reads the five existing aggregate kernel allowlist paths once and retains no history, installs no session sender, and starts no sampling thread. It reports current memory availability and explicitly states that historical voice timing/causal evidence is unavailable. CPU, I/O-wait and pressure rates cannot be calculated from one sample. No process, microphone, speaker, screen or personal-file content is collected.

The authenticated read-only `/health-recorder/incident` route follows this same contract. Event ingestion and clearing remain unavailable while recording is off. The CLI's report request uses the existing bridge credentials; missing credentials produce an unavailable result rather than creating credentials or enabling recording.

This separates functional on-demand access from collection. It adds no job execution, automatic recording, process termination, persistent account grant or laptop WhatsApp enablement.
