# Codex Pulse

A local dashboard for Codex projects, observed activity, plan progress, time, tokens and daily reports. Designed to sit beside a conversation in the Codex desktop app's built-in browser.

本机 Codex 项目运行看板。打开后即可查看项目进度、当天运行的项目数、耗时、token 和日报。源码公开，任务数据保留在自己的设备。

## What works

- **Overview:** observed running projects, daily distinct projects, main tasks and subagents, cumulative turn time and union wall time.
- **Project progress bars:** completed plan steps / total steps. Missing or invalid plans display **进度未知**, without an invented percentage or ETA.
- **Project and task details:** search, drilldown, plan steps, recent turn history and source attribution.
- **Usage:** response-level deduplication, input/cache/output/reasoning breakdown, project and model totals.
- **Account quota:** official remaining percentages and progress bars, actual window lengths, reset time/countdown, and available reset count. All returned quota buckets (including separate model limits) appear in overview and usage.
- **Daily reports:** selectable date, copy and Markdown download.
- **Preferences:** refresh interval, observation freshness window, light/dark/system theme; saved across restarts.
- **Local deployment:** macOS LaunchAgent, automatic start at user login, status/restart/uninstall commands.

No Python packages, npm installation, API key or cloud account is required to run the dashboard. Python **3.9+** and a local Codex installation with readable session logs and a `state_*.sqlite` index are required. The default time zone is `Asia/Shanghai`; use an IANA time zone appropriate for your data.

The optional account quota connection requires a Codex CLI with an existing ChatGPT login and network access to Codex services. A missing CLI, unsupported authentication mode or failed query leaves account quota **unknown**; local task statistics continue working.

## Run

```bash
git clone https://github.com/duyu0218-flash/codex-pulse.git
cd codex-pulse
python3 -B -m pulse --timezone Asia/Shanghai
```

Open [http://127.0.0.1:43189](http://127.0.0.1:43189). In Codex desktop, open this address in the built-in browser beside your conversation. This is a local browser panel, not a modification of Codex's native sidebar.

Optional arguments:

```bash
python3 -B -m pulse --port 43189 --days 30 --timezone Asia/Shanghai \
  --codex-home "$HOME/.codex" \
  --data-dir "$HOME/.local/share/codex-pulse"
```

History is limited to the selected rolling window (1–365 days, default 30). Startup replays the relevant files, including older session files updated during that window. Subsequent scans read only appended lines. The initial import of a large history may take some time.

### Account quota connection

Quota queries run independently every 60 seconds through the official [Codex App Server account interface](https://learn.chatgpt.com/docs/app-server). The dashboard starts a short-lived stdio connection, performs `initialize` / `initialized`, checks `account/read` without forcing a token refresh, and calls `account/rateLimits/read`. It does not create a task or start model generation. Codex manages its own existing credentials and normal authentication refresh; Pulse does not parse or copy `auth.json`.

The CLI is discovered from `PATH` or standard macOS app/Homebrew locations. Use `--codex-cli /absolute/path/to/codex` (or `CODEX_PULSE_CLI`) to override it. The macOS installer records the discovered executable path so quota also works with launchd's minimal environment. These flags are accepted by both `python3 -m pulse` and `scripts/service.py install`:

```bash
python3 -B -m pulse --codex-cli /absolute/path/to/codex
python3 -B -m pulse --no-quota
```

**刷新额度** requests an immediate background query, with a minimum 15-second interval and one query at a time. Each query has a 25-second deadline. Responses are reduced to display fields and kept only in memory. Failed queries clear previous percentages; stale observations and windows awaiting reset are labeled explicitly. Changing the selected statistics date never changes the current account quota. No reset credit is consumed by this integration.

## Install on macOS

Stop the foreground server with Ctrl+C before installing on the same port.

```bash
python3 -B scripts/service.py plan
python3 -B scripts/service.py install --timezone Asia/Shanghai
python3 -B scripts/service.py status
```

The installer copies the application into `~/Library/Application Support/Codex Pulse/releases/` and registers `~/Library/LaunchAgents/io.codex-pulse.dashboard.plist`. You can move or remove the cloned repository after installation. Dashboard preferences, reduced cache and service logs stay under `~/Library/Application Support/Codex Pulse/`.

```bash
python3 -B scripts/service.py restart
python3 -B scripts/service.py uninstall
```

Uninstall stops the service and removes its LaunchAgent; it preserves private data and installed releases. Re-running `install` after an update installs the new source. The service runs while the user is logged in; it is not a system-wide daemon.

## Metric definitions and limits

| Metric | Definition |
|---|---|
| Running | An unfinished turn with recent log evidence, within the configurable freshness window. It is **observed activity**, not an authoritative Desktop runtime status. |
| Progress | Sum of completed steps / total steps across currently relevant main-task plans. One missing plan makes project progress unknown. Steps are equal in weight, not in effort. |
| Daily projects | Distinct assigned projects with turn intervals or usage overlapping the selected local day. Unassigned tasks remain visible but do not create a fictitious project. |
| Runtime | The main daily tile and each project's runtime use the union of clipped turn intervals: simultaneous work counts once. Project runtimes cannot be added because projects can overlap. Model/tool/wait time inside a turn is included; this is observed elapsed time, not CPU or model generation time. |
| Parallel cumulative time | Sum of clipped intervals across main tasks and subagents, shown separately. Unfinished turns stop at their last execution evidence when stale or superseded; settings changes, user messages and copied history do not extend them. Incomplete intervals are explicitly marked. |
| Tokens | Deduplicated `token_usage_record.usage` by response ID. Cached input is a subset of input; reasoning output is a subset of output. The source total is preserved. |
| Legacy tokens | Cumulative snapshot deltas only for turns without response records. Counter resets rebase the fallback and are reported; legacy totals can be incomplete. |
| Scope | This device's readable user tasks and subagents. Internal guardian/review/memory work and other devices are excluded. |
| Account quota | Shared across the current CLI account's devices/tasks, at query time. Prefer `rateLimitsByLimitId` over the legacy single bucket. Remaining = `clamp(100 - usedPercent, 0, 100)`. Window duration and reset timestamp come from the service; primary does not necessarily mean five hours. Missing values stay unknown. |
| Extra credits / reset count | Separate official fields, not percentages or dollar estimates. The reset count uses `availableCount`; no credential or opaque reset-credit ID is exposed. |

Task completion means a Codex turn ended; it does not mean the project passed acceptance. Plan changes can move progress backward. A quiet task can still be doing work: after the freshness window it becomes **状态未确认**, not “stuck” or “completed.”

Billing, cloud-only task activity, system notifications, menu-bar UI and task control are **not connected in this release**. The quota adapter reads account-wide service data; its independent App Server does not observe Desktop's existing runtime or replace the local activity adapter.

The read-only adapter was exercised against **codex-cli 0.153.4**, JSONL session records and the local `state_5.sqlite` schema. These local storage formats are version-dependent. Other Codex versions and alternative clients require compatibility verification. Formal process-crash/sleep recovery and whole-account reconciliation are not guaranteed by log observation.

## Privacy and security

- Binds only `127.0.0.1`; no public listening option.
- Opens Codex databases with `mode=ro` and `query_only`; source logs are opened read-only.
- No Pulse telemetry or CDN. Account quota is the only outbound data connection: the installed Codex CLI contacts official Codex services using its existing login. Use `--no-quota` for local-only operation. Pulse does not read credential files, store authentication tokens, or proxy arbitrary RPC methods.
- Does not store prompts, replies, commands or tool outputs in the reduced cache. Task titles, project names, directory paths and plan steps are private metadata and stay local.
- Only fixed static assets and explicit dashboard API routes are served. Host/Origin validation and a required request header protect preference writes and quota refresh requests from browser cross-origin requests. `/api/quota` exposes only reduced quota fields, without email, account ID or reset-credit IDs.
- A local process running as your user can access the dashboard. This is not a multi-user authenticated service. Do not expose it through a public tunnel or remote reverse proxy.
- The public repository excludes local caches, logs, databases, credentials and real task snapshots.

## Development and checks

```bash
python3 -m compileall -q pulse scripts tests
node --check pulse/static/app.js
python3 -B -m unittest discover -s tests -v
```

Node is needed only for the JavaScript syntax check. Tests use synthetic temporary files, fake CLI processes and local HTTP ports; CI never uses a real account or contacts Codex services. CI runs the parser, quota and HTTP tests on Linux/macOS with Python 3.9 and 3.12.

See [architecture](docs/architecture.md) and [acceptance scope](docs/verification.md) for implementation boundaries and validation details.

## References

Product research considered [CodexBar](https://github.com/steipete/CodexBar), [ccusage](https://github.com/ccusage/ccusage), [CodexMonitor](https://github.com/Dimillian/CodexMonitor), [Vibe Kanban](https://github.com/BloopAI/vibe-kanban) and [Tokscale](https://github.com/junhoyeo/tokscale). No source code from these projects is bundled.

MIT licensed. Independent community project; not an official OpenAI product.
