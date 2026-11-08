# Portable scene handoff MVP

> Historical record. Original path: `docs/superpowers/specs/2026-11-03-portable-handoff-design.md`.
> Findings, commands and validation counts describe the recorded revision and environment,
> not current acceptance. Historical command paths may refer to the original layout.
> See the [current guide](../../portable-projects.md) and [archive index](../README.md).

Approved goal: a receiver without chat history can inspect an exported project,
check its environment, render it, change one named scene, and return evidence.
Use ordinary Manim sources and existing revision manifests/timelines, not a DSL.

An explicit project JSON selects relative files with roles and licence/permission
notes, an existing revision manifest, and required Python/package/tool/font versions.
Export creates a bounded ZIP with a SHA256 inventory and relative command recipes.
Inspection validates the archive without importing project code. Unpack requires a
new destination. Preflight checks declared environment and static revision mapping;
it neither installs dependencies nor runs project code. Rendering remains an
explicit trusted-code action using scene_production; revision capture/locate/apply/
audit/restore remain the existing APIs. Hashes prove internal consistency, not trust.

Keep source/assets/subtitles selected explicitly; reject traversal, aliases, links,
unknown entries, oversize payloads, known secret/cache paths and private-key markers.
This is not a comprehensive secret scanner or sandbox. Authors must review rights
and contents. Fonts are named, never bundled automatically. Missing fonts/packages
block ready status with an actionable message; different producer platforms warn.

Deliver product source patch/wheel plus a small project kit with receiving brief.
Independent receiver gets only kit and a concrete edit request, not conversation.
Real same-machine render and non-target audit; no cross-machine claim. Retain
rollback/version receipts locally and return an updated export plus evidence.
Show actual transfer and change in a silent Chinese dynamic demo. No external
publishing, audio, paid providers or automatic execution/install on import.
