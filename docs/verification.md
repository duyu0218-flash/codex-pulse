# Acceptance scope

Use synthetic data for automated regression tests. Keep actual task names, local paths, raw logs, tokens from personal sessions, browser screenshots and machine-specific reports outside the public repository.

## Pages, entrances and states

| Entrance | Expected checks |
|---|---|
| Overview | Live connection, daily metrics, project bars, all/running/attention filters, empty state |
| Projects | Search, empty search, project details, project → task → project → list |
| Task | Known/unknown plan, turn history, source details, missing record |
| Usage | Input/cache/output/reasoning, project/model totals, unavailable quota |
| Report | Correct selected date, copy, Markdown download |
| Settings | Input validation, save, navigate away/back, process restart persistence, themes |
| Date | Current/historical date, invalid and out-of-range rejection |
| Connection | Server unavailable, retained observation warning, recovery |
| Layout | Wide and narrow widths, keyboard focus, no horizontal page overflow |

Actual business flow: **overview → project search → project → task → return → usage → report → settings**. Store/room, cart, order and payment flows are not part of this product. Login/logout is not applicable to the loopback-only personal deployment.

## Automated coverage

The regression suite covers token subsets, response deduplication, legacy-counter resets, copied/forked history, equal-step plan aggregation, missing/changed plans, incomplete writes, truncation, child attribution, midnight clipping, concurrent interval union, stale-turn accounting, post-completion metadata, cache persistence and prompt omission. HTTP tests cover empty data, static pages, settings reload, cross-origin/Host rejection, CSRF protection, traversal/private-path denial, invalid parameters and report downloads.

## Release boundaries

Static checks and the regression suite are distinct from browser functional testing. Local service installation and restart are distinct from a full macOS logout/reboot test. Desktop browser responsive checks are distinct from physical phone tests. Public GitHub source publication is distinct from internet hosting of private telemetry.

Before broader distribution: verify each supported Codex storage version, malformed/large histories, OS sleep/crash behavior, cold login/reboot, installer upgrade/rollback, and sustained collection. Public multi-user deployment additionally requires a separate authenticated architecture; this loopback service is not intended for that use.
