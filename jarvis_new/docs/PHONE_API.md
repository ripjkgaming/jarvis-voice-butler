JarvisLink 3.2 (versionCode 14) uses the laptop's authenticated `/phone/v1` API on the existing bridge port.

The phone sends text and a bounded local history snapshot to `/command`. Laptop policy chooses instant routing, explicit no-route fallback to chat, history roles and context window. `/session` chooses a new dispatched room or the existing laptop room and returns join credentials. `/capabilities` negotiates v1 and declares supported operations. No downloaded code, scripts, endpoint redirects or new permissions are accepted.

Laptop `config/phone_policy.json` accepts only `instant_routing` (boolean) and `history_messages` (1–20). It is read on each command, so editing that file changes behavior without rebuilding the APK or restarting. Missing/malformed/oversized config safely uses existing defaults. The file can be selected with `JARVIS_PHONE_POLICY`; no network setting or credential changes are needed.

Native responsibilities remain Android audio routing, microphone capture, permissions, assistant role, biometric unlock confirmation, transport, local display/history and call lifecycle. The v3.1 earpiece/headset default, speaker toggle and assistant picker are preserved. Future native OS/permission/UI changes still require APK updates.

Compatibility: all existing bridge endpoints remain intact for old APKs. New APKs fall back to legacy behavior only when capabilities returns 404 or an operation is explicitly absent. Unsupported API versions, auth failures, offline mode and command failures surface errors; POSTs are not replayed. Existing watch/guest enforcement applies on the laptop. Session joining retains the existing `/token` watch restrictions.

Deployment: install the new laptop files/wiring and restart the bridge only after the parallel backend work is ready and the parent coordinates. No agent restart has been performed by this task. A phone using an older running bridge remains in legacy mode until the new API is active (capabilities cache refreshes within 60 seconds).
