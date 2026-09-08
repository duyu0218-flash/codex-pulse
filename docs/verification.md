# Acceptance scope

Use synthetic data for automated regression tests. Keep actual task names, local paths, raw logs, tokens from personal sessions, browser screenshots and machine-specific reports outside the public repository.

## Pages, entrances and states

| Entrance | Expected checks |
|---|---|
| Overview | Live connection, daily runtime union versus parallel sum, incomplete-turn warning, project bars, all/running/attention filters, empty state, account quota bars and details entry |
| Projects | Search, empty search, project details, project → task → project → list |
| Task | Known/unknown plan, turn history with start/observed endpoint and incomplete labels, source details, missing record |
| Usage | Input/cache/output/reasoning, project/model totals; current account quota buckets, remaining/unknown values, window labels, reset times/countdown, refresh button/cooldown, extra credits and available reset count |
| Report | Correct selected date, copy, Markdown download |
| Settings | Input validation, save, navigate away/back, process restart persistence, themes |
| Date | Current/historical date, invalid and out-of-range rejection |
| Connection | Server unavailable, retained observation warning, recovery |
| Quota connection | Loading, missing CLI, logged out, unsupported authentication, timeout, upstream failure, stale data/reset pending, recovery; local statistics remain available |
| Layout | Wide and narrow widths, keyboard focus, no horizontal page overflow |

Actual business flow: **overview → project search → project → task → return → usage → report → settings**. Additional flow: **overview quota → quota details → refresh → historical date → overview**. Store/room, cart, order and payment flows are not part of this product. Pulse has no login/logout actions; unavailable Codex authentication is verified with synthetic responses without signing the user out.

## Automated coverage

The regression suite covers token subsets, response deduplication, legacy-counter resets, copied/forked history, equal-step plan aggregation, missing/changed plans, incomplete writes, truncation, child attribution, midnight clipping, concurrent interval union, stale-turn accounting, post-completion metadata, cache persistence and prompt omission. HTTP tests cover empty data, static pages, settings reload, cross-origin/Host rejection, CSRF protection, traversal/private-path denial, invalid parameters and report downloads.

Timing regressions additionally cover missing terminal events followed by hours-later settings, user input and context records; unchanged usage and repeated response records; superseded unfinished turns; fork exports with rebased envelope timestamps and whole-second lifecycle precision; project interval union; and invalidation of the old duration cache. Raw-log replay audits and screenshots remain private.

Quota regressions cover the official initialization/read-only RPC sequence, fragmented lines and notifications, multi-bucket precedence, legacy fallback, dynamic windows, percentage limits, nulls, reset counts, privacy-field omission, missing CLI/login, unsupported authentication/CLI, sanitized upstream errors, timeout/oversize/EOF cleanup, independent polling, cooldown, stale reset data, clearing prior values on failure, recovery, date independence, CSRF rejection and absence of a generic RPC proxy. Fake CLIs use synthetic data; no CI account credentials are needed.

## Release boundaries

Static checks and the regression suite are distinct from browser functional testing. Local service installation and restart are distinct from a full macOS logout/reboot test. Desktop browser responsive checks are distinct from physical phone tests. Public GitHub source publication is distinct from internet hosting of private telemetry.

Before broader distribution: verify each supported Codex storage version, malformed/large histories, OS sleep/crash behavior, cold login/reboot, installer upgrade/rollback, and sustained collection. Public multi-user deployment additionally requires a separate authenticated architecture; this loopback service is not intended for that use.
