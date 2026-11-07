# Bridge memory repair and automatic recovery

Installed 29 October 2026 (Singapore).

The bridge's Second Brain incremental builder copied duplicate `contains` edges from its previous graph, including across overlapping roots. Its derived graph reached 2.1 GB and the bridge consumed roughly 13 GB of RAM plus swap. A live py-spy stack identified `second_brain.reuse_subtree` as the active allocation path.

The builder now deduplicates edges, indexes old edges by source instead of repeatedly scanning the entire graph, checks both endpoints against the retained node set, and caps edge growth. Previously reused paths are marked visited. Graphs larger than 32 MiB are rejected before JSON decoding so the next build regenerates the derived index. The old graph is retained at `~/.jarvis/brain/graph.oversized-20261029.json`.

Memray 1.20.0 is installed in the project environment and declared in the development dependencies. py-spy 0.4.2 is installed as a user tool. Memray supports allocation profiling; the independent guard uses process memory counters for detection and py-spy for stack evidence. Neither profiler stays attached during normal operation.

## Active protection

`jarvis-memory-guard.service` is enabled in the user systemd manager and starts at login. Its source is `src/bridge_memory_guard.py`. Every ten seconds it checks RAM **plus swap** for this repository's exact bridge command, owned by this user and parented by `jarvis-shell`.

After three consecutive readings above 2,048 MiB, it saves status and stack diagnostics, terminates the bridge using a PID file descriptor, and lets the existing shell supervisor relaunch it. If termination does not complete within five seconds, it sends SIGKILL to that same process. Recovery has a ten-minute cooldown and a maximum of three attempts per hour, persisted across monitor restarts. Exhausted recovery budgets are logged rather than creating an endless restart loop.

This is automatic memory recovery, not a general system that rewrites faulty application code. The duplicate-edge defect was repaired directly. Other persistent leaks will still need diagnosis from the saved reports.

Diagnostics: `~/.jarvis/memory-guard/bridge-*.json` (latest five retained).
Logs: `~/.jarvis/memory-guard/guard.log` (1 MiB, two backups).
Service configuration: `~/.config/systemd/user/jarvis-memory-guard.service`.
The guard itself has a 256 MiB memory limit and a 10% CPU quota.

## Validation

- 38 focused tests passed, covering duplicate edges, overlapping roots, node limits, oversized graph rejection, swap accounting, sustained thresholds, cooldown and restart budgets, process matching, PID-safe termination, and bridge authentication/polling.
- Ruff checks passed for the changed Python source and tests.
- Six rebuilds of an isolated copy of the real index retained exactly 1,899 nodes and 2,081 edges; the index stayed around 652 KB and peak process RAM stayed below 61 MiB while profiled.
- After bridge restart and its production index rebuild, the bridge used about 190 MiB RAM and zero swap; approximately 9.3 GiB system memory was available. Short-term memory stall rate fell to 0.03%.
- The guard is active, enabled, and successfully reads the live bridge's memory usage.

## Controls

```sh
systemctl --user status jarvis-memory-guard.service
journalctl --user -u jarvis-memory-guard.service -n 30
systemctl --user disable --now jarvis-memory-guard.service
```

Change `--limit-mib` or `--interval` in the service file, then run:

```sh
systemctl --user daemon-reload
systemctl --user restart jarvis-memory-guard.service
```

To profile code without attaching to the running bridge:

```sh
uv run --no-sync python -m memray run --aggregate -o work/capture.bin path/to/reproduction.py
uv run --no-sync python -m memray summary work/capture.bin
```

Memray reference: https://bloomberg.github.io/memray/
