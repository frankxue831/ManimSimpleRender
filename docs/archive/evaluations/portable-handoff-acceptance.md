# Portable handoff MVP acceptance (2026-11-03)

> Historical record. Original path: `docs/portable-handoff-acceptance.md`.
> Findings, commands and validation counts describe the recorded revision and environment,
> not current acceptance. Historical command paths may refer to the original layout.
> See the [current guide](../../portable-projects.md) and [archive index](../README.md).

This page records the original MVP experiment based on master
f697ffa89f8bf995ac20324bc639dc6ba62e37e0. No renderer or revision implementation
changed during that original experiment. The later safety follow-up in PR #21
changes traced-render subprocess startup to an isolated trusted launcher; its
separate final-wheel render and CI validation are described in the PR. The
17-to-19-second edit/replay experiment below was not repeated by that follow-up.

A fresh-context receiving
agent received only a local wheel, project ZIP, commands and a concrete request:
make scene `distinction` explain content edits versus inspection-time edits with
two visible paths, preserving setup/tail and the blue dot.

## Observed experiment

Environment: same Mac, fresh directory; Python 3.13, Manim 0.20.1, installed
Arial Unicode MS, existing FFmpeg. The wheel was installed into an isolated target
using `--no-deps --no-index`; no dependency/font installation or network service.
This is a clean-directory and context-transfer test, NOT a second-machine test.

The receiver inspected, unpacked and preflighted the bundle, reviewed source,
rendered a 17-second baseline, captured a version, located the requested scene,
authored a replacement and applied it through revision constraints. Its first
candidate exceeded the old frame deadline. It diagnosed that failure, extended
the target endpoint from frame 360 to 420, updated the inspection timeline and
rendered a successful 19-second candidate. No coordinator supplied the edit or
rescued the render; the receiver made those semantic/timing decisions itself.

The target grew from 210 to 270 frames. Setup remained frames [0,150); tail moved
from [360,510) to [420,570). Existing audit reported both non-target sections equal
in source, dependencies, frame count, raw RGBA and decoded frame layers. Verify
passed. Guarded restore passed; replay reproduced exact edited source bytes.
The updated project was exported, unpacked and preflighted successfully.

Baseline video SHA256:
`b6f82e1ebcaf2ba91ef81bfc7f9662a93e01ac113a5c0169e8853ec960cf19d6`

Candidate video SHA256:
`48778d77c1d0ae0934560973bd2405f0c821975806f7d7aab5c20a2054ad3707`

After independent review hardened archive/provenance/JSON bounds, a new wheel
was validated by the receiver in a NEW runtime. Ten inspect/unpack/preflight/
export commands all exited zero for original, edited and re-exported inputs.
The renderer/revision modules were byte-identical between builds, so these guard
changes did not require rerendering the same source. Both original receiving
records and the first failed candidate were retained locally.

## Product validation

941 current tests passed, 2 skipped; black and flake8 passed; a wheel built with
existing dependencies (`--no-deps --no-build-isolation --no-index`). Sixteen new
portable tests include malformed/tampered archives, traversal, links, Unicode and
case aliases, secret/cache paths, aggregate budgets, bounded JSON, missing declared
inputs and actionable dependency/font errors. Tests verify project code is not
executed by inspection/preflight. Negative guards were not all exercised by the
fresh receiver; they are automated unit tests, not an adversarial security audit.

## Limits

Machine equality does not judge whether the changed explanation is correct or
understood. No user comprehension study, speedup or cost saving was measured.
Model identity/token use/cost are unknown. Cross-machine, Windows/Linux, Python
3.12, font substitution and audio were not tested. Exact package requirements are
not a complete native dependency lock. Input selection/licence assertions still
need author review. The unsigned inventory is neither authentication nor a
sandbox. No claim of automatic LLM feedback interpretation is made.
