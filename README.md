# Codex Pulse

A local dashboard for Codex projects, observed activity, plan progress, time, tokens and daily reports. Designed to sit beside a conversation in the Codex desktop app's built-in browser.

本机 Codex 项目运行看板。打开后即可查看项目进度、当天运行的项目数、耗时、token 和日报。源码公开，任务数据保留在自己的设备。

## What works

- **Overview:** observed running projects, daily distinct projects, main tasks and subagents, cumulative turn time and union wall time.
- **Project progress bars:** completed plan steps / total steps. Missing or invalid plans display **进度未知**, without an invented percentage or ETA.
- **Project and task details:** search, drilldown, plan steps, recent turn history and source attribution.
- **Usage:** response-level deduplication, input/cache/output/reasoning breakdown, project and model totals.
- **Daily reports:** selectable date, copy and Markdown download.
- **Preferences:** refresh interval, observation freshness window, light/dark/system theme; saved across restarts.
- **Local deployment:** macOS LaunchAgent, automatic start at user login, status/restart/uninstall commands.

No Python packages, npm installation, API key or cloud account is required to run the dashboard. Python **3.9+** and a local Codex installation with readable session logs and a `state_*.sqlite` index are required. The default time zone is `Asia/Shanghai`; use an IANA time zone appropriate for your data.

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

Task completion means a Codex turn ended; it does not mean the project passed acceptance. Plan changes can move progress backward. A quiet task can still be doing work: after the freshness window it becomes **状态未确认**, not “stuck” or “completed.”

Account quota, billing, cloud-only tasks, system notifications, menu-bar UI and task control are **not connected in this release**. Opening a new independent App Server is not used to pretend to observe Desktop's existing runtime. A future adapter can use the official [App Server protocol](https://learn.chatgpt.com/docs/app-server) when the actual running instance exposes a supported endpoint.

The read-only adapter was exercised against **codex-cli 0.153.4**, JSONL session records and the local `state_5.sqlite` schema. These local storage formats are version-dependent. Other Codex versions and alternative clients require compatibility verification. Formal process-crash/sleep recovery and whole-account reconciliation are not guaranteed by log observation.

## Privacy and security

- Binds only `127.0.0.1`; no public listening option.
- Opens Codex databases with `mode=ro` and `query_only`; source logs are opened read-only.
- No telemetry, CDN, outgoing API calls or access to `auth.json`.
- Does not store prompts, replies, commands or tool outputs in the reduced cache. Task titles, project names, directory paths and plan steps are private metadata and stay local.
- Only fixed static assets and explicit dashboard API routes are served. Host/Origin validation and a required request header protect preference writes from browser cross-origin requests.
- A local process running as your user can access the dashboard. This is not a multi-user authenticated service. Do not expose it through a public tunnel or remote reverse proxy.
- The public repository excludes local caches, logs, databases, credentials and real task snapshots.

## Development and checks

```bash
python3 -m compileall -q pulse scripts tests
node --check pulse/static/app.js
python3 -B -m unittest discover -s tests -v
```

Node is needed only for the JavaScript syntax check. Tests use synthetic temporary files and local HTTP ports. CI runs the parser and HTTP tests on Linux/macOS with Python 3.9 and 3.12.

See [architecture](docs/architecture.md) and [acceptance scope](docs/verification.md) for implementation boundaries and validation details.

## References

Product research considered [CodexBar](https://github.com/steipete/CodexBar), [ccusage](https://github.com/ccusage/ccusage), [CodexMonitor](https://github.com/Dimillian/CodexMonitor), [Vibe Kanban](https://github.com/BloopAI/vibe-kanban) and [Tokscale](https://github.com/junhoyeo/tokscale). No source code from these projects is bundled.

MIT licensed. Independent community project; not an official OpenAI product.
