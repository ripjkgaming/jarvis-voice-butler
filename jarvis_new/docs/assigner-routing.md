# Dedicated headless Assigner routing

This change is source-ready and inactive until `JARVIS_ASSIGNER_ROUTING=1` is
explicitly configured for the bridge. No running service was restarted or live
production job dispatched. With the switch absent, new coding requests fail
closed; research remains unchanged. There is no opencode or ephemeral fallback.

## Coding workflow and application boundary

An explicit, authenticated, non-guest coding request becomes a durable job. The
voice tool forwards only its coding goal, directory, routing hint and stable
request ID; private-phone detached work is rejected by the existing phone gate.
The dedicated Assigner plans delegation; the adapter validates the plan and
actually starts one or two real workers in sequence. Results and worker/model
identities are stored in the Project Archive. The voice result says the code or
patch is ready for review, rather than claiming files were modified.

**Workers have no tools. The trusted adapter supplies actual source context and
validates returned patch hunks; patch application and test execution remain
separate owner actions.** The project root is registered on the trusted request
path with device/inode identity and cannot be replaced by coordinator data.
Assigner selects only inventory paths. Required filenames named in the request
cannot be omitted. Missing, ambiguous, excluded, oversized or unsafe relevant
context returns blocked; selected files are never silently truncated.

The adapter reads allowlisted UTF-8 source/test/docs and named manifests, without
executing imports or project code. It excludes hidden/private/auth/secret paths,
dependencies/build outputs and nonregular, symlinked or hardlinked files. Reads
use Linux directory-relative descriptors, O_NOFOLLOW and a pinned verified
regular inode. Root/file replacement and changes during reading block collection.
Secret screening is conservative and may reject legitimate code; it cannot detect
every embedded secret. No personal repository is used for live validation.

Bounds: 2,000 inventory entries, depth eight, two-second scan, 24 KiB encoded
inventory; eight source files, 16 KiB each, and 48 KiB encoded total snapshot.
Statically identifiable Python and JS/TS local dependencies are included within
those same bounds; unavailable local dependencies block. Each Python import alias
is checked independently. Existing package initializer chains for selected and
imported modules are included and recursively checked through the same identity,
size, eligibility and secret-screening gates. A safe sibling cannot mask an
excluded/missing alias or an unsafe initializer. Package attributes use the
initializer context; namespace-package aliases require their own local module.
The bounded Python resolver uses conventional project-root and src search roots;
ambiguous local resolution blocks, and runtime sys.path changes are unsupported. Other languages and
dynamic imports have no automatic dependency closure. Coverage is selected files,
not the whole repository. A model must block if further relevant context is
needed; semantic completeness cannot be proven by filename/hunk checks.

Snapshot text and source hashes are persisted before workers receive them, framed
as untrusted data that cannot grant permission or change task authority. A done
worker must return a strict unified diff. Only selected existing source/doc files
may be modified; additive files must be explicitly named in a creation request,
use safe allowlisted paths (maximum four levels/four files), and be absent at
snapshot time. Hidden/control/config/auth/secret edits, renames and deletions are
unsupported. Hunk matching runs in memory and never writes the project. It proves
application to the recorded snapshot only, not correct behavior or passing tests.
An owner must recheck current hashes (and absence for new files) before applying.
Raw replies, context manifests, validation hashes and exact proposal.patch remain
available even if coordinator reporting fails. Coordinator reporting cannot
rewrite the validated worker patch or claim application/test execution.

Read-only filesystem sandboxing alone is not a no-effects guarantee. Shell and
all other model tools are removed before inference; empty environments/dynamic
tools plus pinned, verified isolation configuration enforce that boundary. The
installed-CLI offline capture confirms an empty tools array. Request serialization
is capped at 256 KiB; excess context/output combinations fail closed with retained
raw results rather than silently clipping patches.
An owner reviews the archive and applies an approved patch through their normal
local coding workflow. No automatic application tool, approval-to-execution
bridge, deployment, install, message sender or privilege escalation is added.
A future Jarvis patch-application feature needs its own concrete owner approval,
path/diff validation and guarded execution; a coordinator/worker answer never
supplies that approval. Do not enable writable mode as a shortcut.

## Supported lifecycle and model routing

Uses the installed `/usr/lib/chatgpt/resources/codex`, pinned to verified version
0.159.2, over its supported JSONL stdio app-server protocol. No external listener,
daemon, new credentials, private database writes or desktop locks are involved.
The CLI uses its existing ChatGPT login; API-key/base-URL environment overrides
are removed and custom OpenAI endpoints refused.

The coordinator is GPT-6.1 Sol at low effort. Its ID lives in the **Jarvis-owned**
`$JARVIS_HOME/assigner/jobs.sqlite3` state table; later consumers resume that exact
ID. The desktop Assigner ID is explicitly refused. Only its explicit instruction
to delegate, copied through supported thread/read metadata, was carried over;
old task facts and private histories were excluded.

Model/effort choices are filtered against the live model catalog and the
allowlist GPT-6.1 Sol, GPT-6 Luna, GPT-6 Astra. The coordinator chooses worker
models/efforts and fixed roles: generate, then optionally review. Free-form
subtasks are rejected; only adapter-authored role instructions and the unchanged
original request reach workers. Worker replies remain untrusted proposal data. Each worker's effective response must
confirm model, provider, effort, cwd, read-only sandbox, disabled network access,
on-request approval and user approval reviewer. Settings/reroute changes fail
closed. Apps, plugins, MCP connectors, hooks and native nested multi-agent tools
are disabled, including shell/unified execution, code mode, filesystem tools,
web search, images and automatic project/skill instructions. Unknown or prohibited
item events fail closed and hold the queue; this is defense in depth, not the
pre-execution boundary. Delegation is performed by the adapter from a validated plan;
it is not inferred from prose claiming a subagent was used.

## Queue, cancellation and recovery

SQLite transactions deduplicate a caller request ID and reject ID reuse with a
different goal/effort/directory. The same request maps to one archive record;
file locks serialize archive allocation, coding archive updates and consumer
ownership across processes. Archive writes use unique temporary files and atomic
replacement. Each raw worker response is committed to the Jarvis ledger before
validation or coordinator reporting, then exposed as worker-N.md in the archive.
Malformed replies or reporting failure cannot discard those original proposals.
The stable voice ID derives from the realtime model's tool-call ID (`call_id`); callers retrying HTTP
must reuse the original ID. Independent new requests use new IDs.

Only one job and one worker turn run at a time. Up to two workers are planned;
there are no native nested agent descendants. Cancellation interrupts only the
adapter's recorded thread/turn and requires a terminal notification before
claiming remote work stopped. Later workers and coordinator reporting are
suppressed. Whole-job deadline is 45 minutes from submission, including queue
wait; protocol requests and interruption confirmation are bounded separately.
Cleanup signals and checks the adapter-owned server process group even after its
leader exits, escalates to SIGKILL for surviving descendants, and confirms group
termination within bounded waits. Unconfirmed cleanup holds the queue as
delivery_uncertain, including after a model result was already received. Desktop
processes are outside the owned group.

Write-ahead dispatch state precedes turn/start. A lost acknowledgement is not
retried. After a consumer crash, interrupted jobs are marked blocked or
`delivery_uncertain`; uncertain remote work holds the queue for supported owner
review. No automatic reconciliation, clearing uncertain state, or replay is
provided. Do not delete the ledger or resubmit a new ID to bypass that hold.
Pending jobs resume when an enabled archive read or new submission wakes the
consumer. Large history/protocol output fails bounded checks rather than growing
without limit.

Completion speech uses a durable atomic at-most-once claim. Archive results remain
available when a voice session ends. A crash after the speech claim can omit the
spoken report; exactly-once audible delivery would require an idempotent delivery
acknowledgement. All terminals remain in the archive. Existing handoff recovery
was not modified.

## Validation and deployment

Benign task-workspace proof created a separate coordinator
`01a1161f-e7a7-70a2-9a76-84fdb5f50eca`, dispatched real GPT-6 Luna/low workers,
returned correlated JSON twice, and resumed the same coordinator. That earlier
proof predates the review fixes and does not prove the final no-tool contract.
The revision-2 installed-CLI localhost fake-provider capture confirms zero tools
without inference or authentication; revised mocks exercise the lifecycle fixes. Final read-only
metadata verification reconfirmed policies and catalog; no inference was sent in
that last check. These are test histories, not production ledger initialization.

Before activation, independently review the frozen patch/manifest and run the
focused repository tests. Ensure bridge and voice processes share JARVIS_HOME.
Apply all source hunks together, especially bridge request-ID forwarding. Configure
`JARVIS_ASSIGNER_ROUTING=1` only after approval, then coordinate the normal bridge
and voice rollout. The first production request will create its own dedicated
coordinator unless an explicitly reviewed migration seeds the coordinator ID via
Jobs.state; do not copy test jobs or touch Codex's private database. No such
migration, activation or rollout occurred in this task.

Rollback begins by removing the opt-in switch through the normal approved service
configuration and coordinated rollout, so coding requests fail closed. Drain or
confirm interruption of any owned active turn first; retain the Jarvis ledger
for dedupe and review. Reverse only this change's frozen patch with a clean dry
run, preserving pre-existing bridge/phone/calendar/UI edits. Do not reset files
to Git HEAD or delete the dedicated session history. Reverting to the old coding
engine is a separate explicit routing decision, not an automatic fallback.

Sources: https://learn.chatgpt.com/docs/app-server,
https://learn.chatgpt.com/docs/non-interactive-mode,
https://learn.chatgpt.com/docs/agent-configuration/subagents.
