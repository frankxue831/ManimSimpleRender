# Editorial review of a delivered video

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md)

This small trusted-local helper records an **explicit editorial judgment** about
exact delivered video bytes. It does not render, discover the authoring project,
verify media, watch or listen, determine readiness, authenticate a reviewer, or
prove audience comprehension. First accept the completed render using the
[agent completion rule](scene-agent.md), then find the actual delivered video.
For a narrated preview use the `preview.path` in the
[audio preview receipt](scene-audio.md), not the silent run video. Retain the
receipt, the original run and its source snapshot. Use
[caption-linked samples](scene-frame-samples.md) or existing inspection frames to
find a time and source navigation; the samples do not cover continuous playback.

The caller selects evidence and checks its relationship to the delivered video.
For an audio preview, check its receipt identifies the preview and its verified
silent source, then check the frame manifest identifies that same silent source.
Frame PNGs and manifests are separate files: include both if each matters. This
helper hashes the supplied files but does **not** validate their contents,
relationships, render completion, or the PNGs referenced inside a manifest.
Evidence links may point to unrelated files. A missing or changed evidence file
is reported separately; it does not alter the byte-comparison result.

## Record, revise, and assess again

Create an assessment JSON, for example `assessment.json` (replace all paths):

```json
{
  "reviewer": "reviewer-agent-name",
  "scope": "Frame at 3 seconds only; audio not listened to",
  "decision": "changes_requested",
  "findings": [{"id": "F1", "time_seconds": 3, "section": "lookup",
    "observed": "The result appears without showing the lookup.",
    "acceptance_question": "Can the viewer see which row was read?"}],
  "evidence": ["/absolute/path/to/frame-manifest.json", "/absolute/path/to/frame.png"]
}
```

`reviewer` and `scope` are required nonempty text; explicitly state what was
actually inspected. `decision` is `changes_requested`, `needs_review`, or
`approved`. Findings are outstanding issues with unique IDs, observed confusion,
and an acceptance question. A `changes_requested` assessment requires at least
one; an `approved` assessment cannot contain outstanding findings. Optional
`section` and `time_seconds` are reviewer-supplied navigation hints, not verified
source mappings. A scoped approval is only the reviewer's judgment of that scope.

```sh
python -m scene_production.editorial record --video /absolute/delivered.mp4 \
  --assessment assessment.json --output /absolute/review-1.json
python -m scene_production.editorial compare --review /absolute/review-1.json \
  --candidate /absolute/candidate.mp4
```

The JSON comparison includes the recorded finding, evidence paths and hashes,
review scope, and a byte-current flag. When candidate bytes differ, the old
assessment is non-current and `candidate_requires_fresh_assessment` is true,
even for an audio-only change. The candidate may be compared before it receives
a new assessment. Exit 0 means the comparison ran, **not** that review passed;
clients must read the JSON. `readiness` is always `not_determined`. A current
`needs_review` assessment is still not approval. Check evidence and previous
review integrity flags independently of the byte-current flag.

A second agent can use the finding and original source snapshot to locate and
make a targeted edit, then render the full candidate using existing tools. A
reviewer creates a new assessment after inspecting that candidate:

```sh
python -m scene_production.editorial record --video /absolute/candidate.mp4 \
  --assessment candidate-assessment.json --output /absolute/review-2.json \
  --previous /absolute/review-1.json --addresses F1
python -m scene_production.editorial compare --review /absolute/review-2.json \
  --candidate /absolute/candidate.mp4
```

The new record links the previous review's hash and named finding IDs. Omit
`--addresses` to link a previous review without naming findings, including a
prior approval or finding-free assessment. Naming a finding is
an author assertion that the candidate addresses those findings, not proof they
are resolved. `compare` exposes the link and reports if the previous record is
missing or changed. Records are published exclusively through a temporary file
and a hard link; the output filesystem must support hard links. Keep earlier
files and artifacts rather than replacing them. If publication succeeds but
temporary-file cleanup fails, `record` succeeds with a `publication_warning`
containing the temporary path; the warning is in the command result, not the
record. On publication failure, a cleanup failure is reported separately. The
helper does not follow or verify an
entire history chain, prevent a user from editing records, or provide signatures.
The caller must retain the record and inspect the reported integrity flags.
Avoid concurrent modifications; hashing is not an authentication or locking
mechanism. The output record and linked files may contain private information.

The existing revision audit is **silent-only**. Linking a narrated preview to an
editorial review does not extend the audit to audio or imply audio was heard.
