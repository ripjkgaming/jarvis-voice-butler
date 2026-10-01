# STARK OS frontend coverage audit

Audited 1 October 2026 (Asia/Singapore). This report concerns the three active
Next routes and their shared/school surfaces, not external websites displayed
inside a browser. The source of truth is `design/STARK_OS.md` and the current
`frontend/components/stark/`. This companion styling worker did not modify
files in that directory; concurrent HUD optimization belongs to its own chat.

## Delivery status

The original audit patch (`desktop/frontend-styling.patch`) has been applied
to source. `desktop/frontend-styling-baseline.json` retains the pre-application
SHA-256 hashes. Branded error/not-found surfaces were added afterward. The
canonical `components/stark/` design is not changed by this styling work.

Production frontend export: **complete and companion routes verified**. After
the optimization owner completed the integrated export, the verifier passed
once against the actual `frontend/out`. This worker performed no build or
restart. Native compilation/restart and live shell delivery remain owned by
the original runtime chat.

## Surface inventory

| Surface and source | Original coverage | Implemented change |
| --- | --- | --- |
| `/`: `components/stark/stark-hud.tsx`, `stark.module.css`, instruments/reactor/voice/ops/frame | Canonical blueprint, panel cuts, arc reactor, cyan palette and three vendored font faces already implemented. Module-scoped tokens isolate it from legacy globals. Reduced-motion CSS and `HudShell` visibility pause already present. | None; preserve Claude's design exactly. |
| `/projects`: `components/projects/project-archive.module.css` | Holographic two-monitor layout but older `#54f6ff`, Inter/CommitMono, faster reticle, attention text opacity pulses. | Canonical palette and fonts; 40px blueprint grid, 2px selection, 12px/1px instrument brackets; slower 30s reticle; static attention text; narrow-window layout. |
| `/projects`: `components/projects/project-archive.tsx` | Voice-only index/document viewer, voice scroll commands, named sections and progress semantics already present. Companion route does not mount `HudShell`, so lacks its visibility class. Auto-scroll can consume a long resumed frame. | Mount companion visibility hook; discard hidden/long frame deltas so resume does not jump through a document. |
| `/drafts`: `components/drafts/drafts-window.module.css` | Old palette and font pair; clipped reply/index/paper when content exceeds viewport. | Canonical palette/fonts, 40px blueprint grid, 2px selection, narrow-window layout; overflow becomes scrollable rather than permanently clipped; word wrapping for long addresses/content; tighter metadata and paper padding at ≤480px to leave more reply text visible. |
| `/drafts`: `components/drafts/drafts-window.tsx` | Voice-only approval text and read/send/discard phrases; phrase footer hidden from assistive technology despite being instructions. | Expose spoken phrases and name reply text region; mount companion visibility hook. Stabilize the initial clock to prevent static-export hydration mismatch (found by headless QA). No mail/action behavior change. |
| School taskbar: `components/hud/school-strip.tsx`, `.sbar*` in `styles/globals.css` | Already state-aware holographic taskbar with launcher, app indicators, tray and clock. Uses CommitMono/Inter in several places. Voice-state colors intentionally differ from general cyan and are specified in STARK_OS.md. | Scope new Rajdhani labels/Share Tech Mono readouts to `.sbar` selectors; canonical text/line tokens. Preserve state colors and component behavior. |
| School launcher / quick settings / calendar: `components/hud/school-menus.tsx`, `.smenu*` in `styles/globals.css` | Existing custom menus and native bridge actions; font mismatch and no explicit school keyboard focus outline. | Scope canonical type/palette and opaque deep panel background to `.smenu`; cyan focus outline; comprehensive reduced-motion override for menu descendants/pseudo-elements. |
| School entry/exit: `components/hud/school-transition.tsx`, `school-return.tsx` | Custom canvas/native-window choreography. Entry checks reduced-motion and school trace skips hidden frames. | Preserve existing transition implementation and native geometry. |
| Companion hidden/motion contract: new `hooks/hud/use-surface-visibility.ts` | The global `html.hud-hidden` CSS pause already exists but companions never set it. Some project pulse/pseudo-element animations escape narrower reduced-motion rules. | Small route-local visibility hook and full module reduced-motion selectors; no listeners added to canonical `components/stark/`. |
| Notification toasts: `components/ui/sonner.tsx`, app's `Toaster` | Toast styling references generic grayscale popover variables. | Scope STARK variables/type to `.toaster`; semantic cyan/red/amber/green leading border; reduced motion. Native desktop notifications are handled by desktop theme, not this CSS. |
| Root theme overlay: `app/layout.tsx` | Hover-revealed light/dark/system mouse toggle conflicts with documented voice-only routes and can change generic toast theme. | Remove unused overlay import/markup and force dark theme at provider. Main HUD module styling unchanged. |
| Generic `components/ui/*`, `components/agents-ui/*`, old `components/hud/*` | Source contains template/legacy components not all rendered by current active routes. The active main route renders `StarkHud`; startup/welcome template components are not its main design. | Do not rewrite dormant template controls or delete legacy source while preserving existing work. |
| Next error / not-found pages: `app/error.tsx`, `app/not-found.tsx`, `components/app/fallback-surface.*` | Previously generic framework pages. | Passive STARK status panel with canonical palette, locally bundled fonts, blueprint grid, reactor schematic and voice guidance. Error details are not displayed or logged. The error boundary shares the tested 404 surface; an actual runtime exception is not injected into the running app. |

## Readability and accessibility

- Computed sRGB contrast on `deep` (`#061826`): primary `#d9f4ff` **15.72:1**,
  secondary `#7fa6b8` **6.90:1**, cyan `#5fe3ff` **11.93:1**. This is token
  arithmetic, not a claim that every composited pixel or font size passes an
  accessibility audit.
- Canonical faint `#46677a` is **2.98:1** on deep. Keep it for disabled or
  nonessential decoration, not critical prose. No canonical HUD tokens changed.
- School bar text scales from panel height and some existing labels can reach
  7–9px. Native panel scaling/readability should be reviewed with the user at
  their actual display scale; this patch does not resize their taskbar.
- The native school window deliberately declines keyboard focus. CSS focus
  outlines help browser development/accessibility contexts but cannot make
  native taskbar menus keyboard navigable. That requires a coordinated native
  focus-policy/interaction change, outside this visual patch.
- The drafts source supports spoken “read it” and next-draft actions. CSS makes
  long reply content scrollable and exposes its accessible region, but does
  **not** add new spoken draft-scrolling commands or change the cursor/voice-only
  contract. Reading the whole reply visually by voice is an outstanding product
  improvement if arbitrary long drafts need voice paging; do not claim solved.
- School `LiveTrace` samples reduced-motion when mounted; a setting changed
  while mounted takes effect on next mount. Native/JS motion is not universally
  controlled by CSS. The patch preserves that behavior instead of claiming a
  new complete animation engine audit.
- No sending, approval, authentication, account-linking or native security
  behavior is changed. External browser pages and native credential dialogs
  are outside these frontend selectors.

## Verification and application

The initial patch passed Prettier parsing, TypeScript syntax transpilation and
`git apply --check`; source application is now complete. These initial checks
were not a production build or rendered QA.

`desktop/verify_frontend.py` provides repeatable headless rendered verification:

```sh
python desktop/verify_frontend.py --export-root frontend/out
```

It starts an ephemeral localhost static-file server, uses cached headless
Chromium, fulfills only synthetic GET fixtures for the bridge, aborts all
other non-static HTTP requests, blocks WebSocket connections and service
workers, and records any attempted action/write as a failure. No test request
reaches the real bridge, shell, mail service or agent. No visible browser,
KCalc, app or KWin restart is involved.

Coverage includes Projects, Drafts and the branded 404 at 1440×900, 640×900,
and 390×844; normal and reduced motion; long reports/replies; a populated
archive/inbox; reachable last content lines; horizontal overflow; font loading;
and browser runtime errors. Generated screenshots and an export-hashed JSON
report are saved in `desktop/previews/`. All displayed records are synthetic.

Rendered verification status: **12/12 cases passed against the actual
production `frontend/out` export** on 1 October 2026. The definitive run was
performed once after the optimization owner finished its integrated build;
there was no temporary replacement of production routes. The earlier isolated
companion build was used to find the Drafts issues while other chats were
editing the HUD. All owned frontend files separately passed scoped ESLint,
and the final Drafts CSS/TSX passed Prettier checks. The Python verifier passes
`uv run ruff format` and `uv run ruff check`.

The definitive run made **20 locally fulfilled fixture reads, zero action
writes, zero external HTTP requests and zero WebSocket attempts**. All 12
cases loaded their local fonts and had zero browser runtime errors. Reduced
motion cases had zero running CSS/Web Animations. Both long document types
reached their final synthetic content line after scrolling at all three sizes;
no companion monitor or document overflowed horizontally.

Rendered desktop and narrow screenshots were opened and inspected, including
Projects, Drafts, the final reply line, and the 404. QA found and fixed a Drafts
clock hydration mismatch and reduced the narrow Drafts metadata/padding so
more reply content fits. The final 390×844 reply text viewport has 156px of usable height,
so the greeting and start of the first paragraph fit together. No canonical reactor/HUD file was edited.

Evidence: `desktop/previews/frontend-verification.json` records exact export
hashes and metrics. Twenty screenshots named `frontend-*.png` show initial and
end-of-content states (all fixture-only data). Representative files are
`frontend-projects-1440x900-normal.png`,
`frontend-drafts-390x844-reduced.png`, and
`frontend-404-390x844-reduced.png`.

This verifies Projects, Drafts and the 404 from the **actual production static
export**, served locally with isolated synthetic fixtures. It does not exercise
the main HUD/school route, live bridge data or native WebKit rendering. Native
build/restart and any full HUD verification are owned by the other coordinating
chats. The runtime error boundary was source/lint checked and shares the
verified fallback component; no real application error was induced.
