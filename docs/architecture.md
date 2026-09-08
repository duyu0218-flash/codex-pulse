# Architecture

```text
Codex state index + Desktop project catalog (read only)
                        ↓ eligible user/subagent sessions
                JSONL incremental readers
                        ↓ reduced metadata
                 local private cache
                        ↓ response dedup + daily aggregation
           loopback HTTP API + static dashboard
                        ↓
              Codex built-in browser
```

`pulse/collector.py` discovers eligible sessions using a read-only SQLite connection. Project IDs and roots come from the current index, with a read-only compatibility fallback for Desktop's earlier project catalog. Explicit project IDs take precedence over longest matching roots. Unknown roots receive a stable path hash. Explicit unassigned tasks have a separate bucket. Subagent relationships come from spawn edges or session metadata; user forks remain main tasks.

Each log has an independent byte cursor. A partial final line is retried. A replaced or truncated file is rebuilt. The cache contains only turn timing, model names, plan steps, reduced response records and counters. It is versioned and replaced atomically. Startup restores it and reconciles with the current index; deleted/archived paths are rediscovered. Source files are never modified.

Response IDs deduplicate the modern token ledger. Copied records attributed to a different thread and records predating the current session are excluded. Fork exports can rewrite envelope timestamps, so lifecycle `started_at` is also checked against session creation at whole-second precision. Rejected turn IDs also exclude their contexts and legacy records. Legacy snapshots are a separate fallback, suppressed for turns with response records. Reset boundaries are retained as diagnostic counts rather than treated as new consumption.

Daily boundaries use `zoneinfo` and calendar-day arithmetic. Turn intervals are clipped to those boundaries. Completed intervals use recorded start/end timestamps. Fresh unfinished intervals have a provisional end of now. Unconfirmed intervals end at the last recorded execution activity. An allowlist of assistant output, tool events and new token usage updates this evidence; settings, user input, contexts and unchanged usage do not. A new turn makes its unfinished predecessors ineligible for live extrapolation without inventing an end timestamp. Missing starts/ends are surfaced as incomplete timing, alongside each interval's observed endpoint.

Sum and union are computed separately; both include main tasks and subagents. The main daily tile and project runtime use interval union; parallel cumulative time remains a separate figure. Version 0.1.1 increments the cache schema from 2 to 3 to force a full replay and repair previously overstated durations.

The HTTP service listens exclusively on loopback. There is no endpoint for arbitrary filesystem reads, shell execution, Codex interruption or model generation. The only mutation is the bounded settings schema. Frontend output escapes source text before HTML insertion. Hash navigation supplies project/task drilldown and native browser history. Polling preserves form drafts and report selections.

The macOS installer copies an immutable source release identified by content hash and registers a user LaunchAgent. Logs and cache live outside both the clone and Codex's data directory. The standard-library implementation has no build artifact or package installation requirement; deployment uses the exact checked source files.

## Adapter limitations

The storage adapter is not an official cross-version API. No authoritative running-instance subscription was available in the verified Desktop setup. “Running” therefore means recent evidence of an unfinished turn; stale observations are explicitly unknown. Approval states that are not persisted cannot be recovered, and a captured input request is not proof that a user is still waiting. There is no reliable ETA, account quota, billing total or acceptance status inferred from these records.
