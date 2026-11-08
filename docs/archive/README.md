# Documentation archive

These records describe past decisions, experiments and reviews. They retain their
original findings, failures and validation limits. They are evidence about recorded
work, not current instructions, permission to execute, or a current test report.
The [documentation index](../README.md) lists current guides.

Files were reorganized from the documentation present at `fb80059`. Original paths
are given below; command paths inside the records may retain that historical layout.
Videos, logs and other locally retained evidence are not made available by archiving
their descriptions here.

| Record | Original path | Recorded context | Current guide |
| --- | --- | --- | --- |
| [Portable design](designs/2026-11-03-portable-handoff-design.md) | `docs/superpowers/specs/2026-11-03-portable-handoff-design.md` | November 3 MVP goals and constraints | [Portable projects](../portable-projects.md) |
| [Portable implementation plan](plans/2026-11-03-portable-handoff.md) | `docs/superpowers/plans/2026-11-03-portable-handoff.md` | Plan starting from `f697ffa` | [Portable projects](../portable-projects.md) |
| [Portable acceptance](evaluations/portable-handoff-acceptance.md) | `docs/portable-handoff-acceptance.md` | November 3 experiment on `f697ffa`, later PR 21 notes | [Portable projects](../portable-projects.md) |
| [Two-case revision evaluation](evaluations/scene-revision-evaluation.md) | `docs/scene-revision-evaluation.md` | Base `ac2bf85`; one passing and one failing case | [Scene revision](../scene-revision.md) |
| [Revision experiments](evaluations/scene-revision-experiments.md) | `docs/scene-revision-experiments.md` | Later controlled experiments; earlier failure remains recorded | [Scene revision](../scene-revision.md) |
| [Selective migration](migrations/migration-public-baseline.md) | `docs/migration-public-baseline.md` | Public base `80891ab`; selected prototype contracts | [Current capabilities](../README.md) |
| [Frozen logging handoff](reviews/2026-11-08-logging-audit-handoff.md) | `docs/reviews/2026-11-08-logging-audit-handoff.md` | Implementation `6837039`, based on `c449fd2` | [Logging](../logging-and-diagnostics.md) |
| [PR 26 review follow-up](reviews/2026-11-08-pr26-review-followup.md) | `docs/reviews/2026-11-08-pr26-review-followup.md` | Review of `6837039` and subsequent fixes | [Logging](../logging-and-diagnostics.md) |
| [Logging verification](reviews/2026-11-08-logging-verification.md) | Verification section of `docs/logging-and-diagnostics.md` | November 8 runs recorded before this reorganization | [Logging](../logging-and-diagnostics.md) |

In particular, the migration's "not migrated" list records the scope of that change.
Later [local handoff](../agent-handoff.md) added leases and task coordination separately.
Read the current guides to determine today's capabilities.
