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

The HTTP service listens exclusively on loopback. There is no endpoint for arbitrary filesystem reads, shell execution, Codex interruption or model generation. The only persisted mutation is the bounded settings schema. The fixed quota refresh endpoint schedules a read. Frontend output escapes source text before HTML insertion. Hash navigation supplies project/task drilldown and native browser history. Polling preserves form drafts and report selections.

## Account quota

`pulse/quota.py` runs an independent 60-second background poll. Each poll starts the installed Codex CLI with stdio, completes its initialization handshake, checks `account/read` with `refreshToken: false`, and calls only `account/rateLimits/read`. A fresh process reloads the current CLI login for every query. The adapter never starts threads, model turns, login flows, logouts, purchases, reset consumption or notifications. Its 25-second deadline and 1 MiB response ceiling bound the read; the child process is terminated and reaped afterward. Raw stderr and upstream error strings are not logged or sent to the browser.

Normalization prefers the multi-bucket map, falls back to the legacy bucket, and whitelists names, quota windows, credit balances and the available reset count. Emails, account IDs, reset-credit IDs and authentication fields are discarded. Nulls remain unknown; percentages are clamped, and actual window durations determine labels. The reduced response is memory-only and does not modify the log cache or timing aggregation. Failed requests clear prior values, so a failed login/account change cannot retain the previous account's displayed balance. Snapshots older than two polling intervals, or whose reset time has passed, carry a stale status without predicting a new allowance.

`GET /api/quota` and the quota field in `/api/snapshot` read the same in-memory current-account snapshot, independent of the selected date. `POST /api/quota/refresh` requires the existing same-origin protections and custom header; requests coalesce and respect a 15-second minimum interval. The browser's task polling never triggers an upstream request. Historical daily reports omit current quota. CLI authentication is managed by Codex, including any normal credential refresh; Pulse does not parse credential files. Local session collection remains available during quota failures or with `--no-quota`.

The macOS installer copies an immutable source release identified by content hash and registers a user LaunchAgent. Logs and cache live outside both the clone and Codex's data directory. The standard-library implementation has no build artifact or package installation requirement; deployment uses the exact checked source files.

## Adapter limitations

The storage adapter is not an official cross-version API. No authoritative running-instance subscription was available in the verified Desktop setup. “Running” therefore means recent evidence of an unfinished turn; stale observations are explicitly unknown. Approval states that are not persisted cannot be recovered, and a captured input request is not proof that a user is still waiting. There is no reliable ETA, billing total or acceptance status inferred from these records. Account quota has a separate official source and is not inferred from local tokens. If the desktop host uses a different account or authentication store than the CLI, the displayed quota belongs to the CLI login; alternative stores and account-switch scenarios need host-specific verification.
