# On-demand suit diagnostics

Say **“bring up the suit diagnostics”** to show the local 3D armor visualization. **“Close suit diagnostics”**, the Close control, and Escape dismiss it. The panel starts hidden, including after a frontend reload. It opens only for a fresh explicit command. The regional damage and reactor/fuel/coolant readings are fictional simulation presets, prominently labelled in the interface.

The original procedural red/gold armor is actual Three.js geometry with local studio lighting, segmented plates, mechanical joints, and a selectable reactor. Drag to orbit, scroll to zoom, use arrow/+/- keys on the model, or use the front/three-quarter/back controls. Selecting a region shows its integrity and highlights its armor. Nominal, post-flight, and critical presets update the model and telemetry together. No model or texture is downloaded at runtime.

## Lifecycle and mode integration

- The existing shared `/sys` snapshot carries a volatile `{op, revision, issued_at, session}` command. No additional polling subscription or timer is created for diagnostics.
- UI and Three.js modules load on demand. A closed panel has no suit canvas, WebGL context, listeners, or animation loop. The renderer draws on demand, then stops after interaction settles; it preserves device pixel density and releases its geometry, materials, environment maps, shadow map, controls, observers, and context on dismissal.
- Covered HUD particle/CSS animation pauses while its voice and state hooks remain mounted. Keyboard focus stays in the modal; Escape closes the panel rather than hiding the whole shell.
- School mode uses the existing primary-monitor `school_menu` surface. The taskbar remains visible at the bottom, and modal controls are above it. Existing taskbar menus suspend while diagnostics owns that space. Dismissal restores bar height. A fresh webview reload also collapses any orphaned expanded school surface once the canonical mode arrives. A mode-switch signal closes diagnostics without competing with the transition's window positioning.
- The authenticated `/suit` endpoint is the only writer of canonical command state. Fast voice and model paths target it, use local-only guards, and share action deduplication. A native show-only command presents the existing running overlay without starting a voice session or toggling it closed. If no desktop shell owns its single-instance D-Bus name, the command fails without launching a duplicate process. School keyboard focus uses a native lease so delayed replies and old close operations cannot alter a newer modal.
- Missing/lost WebGL shows a retry control while region and telemetry controls remain usable.

## Verification

All automated browser evidence uses isolated bridge/native mocks. It does not demonstrate compositor performance on a physical monitor.

```
cd frontend
node --test tests/suit-command.test.cjs tests/suit-controller.test.cjs
pnpm build
cd ..
uv run --no-sync pytest tests/test_suit_diagnostics.py
uv run --no-sync python frontend/tests/suit-diagnostics-smoke.py --output /tmp/jarvis-suit-review
```

The owner browser matrix passed normal, school, narrow, and DPR2 layouts. A separate reload regression covers school-mode recovery after reloading an expanded diagnostics panel. See the final evidence manifest for exact source/export hashes, test results, and screenshots. Live native activation and a watched school-mode round trip are coordinated separately after the source/export review.
