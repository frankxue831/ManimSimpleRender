# Receiving this project

This is an ordinary trusted Manim example, not an automatically understood edit.
The tool packages explicit inputs and checks declared prerequisites; YOU decide
how to interpret feedback and author the replacement code. Inspect sources first.

Use the supplied local product wheel or a checkout with scene_production.portable.
The tool does not install its own dependencies. Python3.12/3.13, Manim0.20.1 and
FFmpeg must already exist. The example requests Arial Unicode MS; use no font
downloads. If absent, report the missing font. An explicit font substitution
requires a new baseline and must be reported as a visual change.

Resolve `RECEIVER_PY` and `RECEIVER_LAUNCHER` from the already trusted environment
and product before entering imported code; never use a launcher from the bundle.
Use quoted absolute paths. The launcher requires Python `-I` so CWD/PYTHONPATH and
user-site startup paths cannot replace the tool before inspection. This is not a
sandbox for subsequently trusted rendering.

1. `"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable inspect project.zip`
2. `"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable unpack project.zip received`
3. `"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable preflight received`

No project module runs in those steps. `ready: true` is not a code-safety verdict.
Review `handoff.json` command recipes and source files; render only after trusting
them. The bundle is unsigned. The selected asset is original; no fonts, secret
keys, caches, original videos or old machine-bound revision receipts are included.

From the received directory (using the existing render environment):

```sh
SCENE_FONT='Arial Unicode MS' "$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" render --scene-file scene.py --scene HandoffExample --timeline timeline.json --output-dir runs --quality qm --record-scene-frames
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" revision capture --manifest scene.manifest.json --run runs/scene-EXACT/result.json --store versions
```

Replace the receipt path with the exact returned run directory. Keep baseline
run, version snapshot and all dependencies intact. Use `revision locate` with a
specific scene ID and feedback, and acknowledge dependent scenes listed by the
tool. Write the replacement region yourself, then `revision apply`. Re-render
after content edits. If a section gets longer, update the inspection timeline
accordingly; it is NOT an animation driver. The tail duration stays five seconds.

```sh
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" revision locate --baseline versions/version-EXACT/snapshot.json --target distinction --feedback 'the concrete request' --acknowledge tail
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" revision apply --plan plan.json --edits edits.json
# render again, retaining the baseline
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" revision audit --baseline versions/version-EXACT/snapshot.json --run runs/scene-CANDIDATE/result.json --target distinction
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" revision verify --baseline versions/version-EXACT/snapshot.json --run runs/scene-CANDIDATE/result.json --target distinction
```

`plan.json` must contain the actual locate output; edits.json maps only
`distinction` to source between its markers. Do not widen the plan by hand.
The media audit compares mapped scene-relative frames, not matching absolute
times after a duration change. Acknowledgement does not authorise editing tail.
Read reports; a successful machine check is not proof of semantic correctness.

Use `restore --baseline SNAPSHOT --run CANDIDATE_RECEIPT` for guarded rollback,
then replay the same recorded plan/edits and revised inspection timeline. Keep
baseline and candidate runs intact; restore is not crash-atomic recovery.

After editing, old handoff inventory hashes are intentionally stale. Export
`received/project.json` to a NEW ZIP, inspect/unpack/preflight that version, and
return it with original/new bundle hashes, edit reason, scene ID, exact baseline/
candidate receipt paths and audit/verify/rollback/replay outcomes. Do not export
runs, caches or absolute-path receipts as project inputs. Include a small separate
relative evidence summary, disclosing any intervention and platform limits.
