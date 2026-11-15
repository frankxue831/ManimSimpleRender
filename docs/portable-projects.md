# Transfer an editable ordinary Scene project

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md) | [Scene production](scene-production.md) | [Scene revision](scene-revision.md)

`python -m scene_production.portable` adds `export`, `inspect`, `unpack`, and
`preflight` subcommands for editable Python Manim `Scene` projects used with
[scene production](scene-production.md) and
[scene revision](scene-revision.md). It packages and checks explicitly declared
source files, assets, and manifests without executing project code or
interpreting review feedback during transfer checks. Authors still write the
animation; a receiving agent still inspects received code and makes edit
decisions.

## Trusted launcher and CLI workflow

Receivers and callers working with or inside an unpacked bundle (or any
untrusted working directory) must isolate Python's startup paths before
importing ManimSimpleRender (MSR). Invoking the trusted launcher
(`scene_production/launcher.py`) with `python -I` excludes the current working
directory (`CWD`), `PYTHONPATH`, and user-site directories from `sys.path` before
loading any MSR module. Ordinary `python -m scene_production.portable`
invocations require an already trusted working directory and are unsafe inside
an unpacked project directory. When invoked without `-I` (including with
`--help`) or with an unknown command, `launcher.py` exits with code `2`
(`-I ... --help` exits `0`). Startup-path isolation protects only CLI startup; it
is not a render sandbox.

Resolve the interpreter and launcher paths **outside** the received directory
first, keep those paths quoted, and never run a launcher script found inside an
imported archive:

```sh
# Producer (from a trusted local ManimSimpleRender checkout or environment)
TRUSTED_PY="/path/to/trusted-venv/bin/python"
TRUSTED_LAUNCHER="/path/to/trusted-ManimSimpleRender/scene_production/launcher.py"

"$TRUSTED_PY" -I "$TRUSTED_LAUNCHER" portable export \
  --project examples/handoff/project.json \
  --output project.zip

# Receiver (using an existing trusted environment outside received/)
RECEIVER_PY="/path/to/trusted-venv/bin/python"
RECEIVER_LAUNCHER="/path/to/trusted-ManimSimpleRender/scene_production/launcher.py"

"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable inspect project.zip
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable unpack project.zip received
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable preflight received
```

`unpack` requires the destination directory (`received` above) not to exist.
From a trusted checkout directory on the authoring machine, run
`python -m scene_production.portable export --project ... --output ...` to invoke
the same exporter.

## Project manifest (`project.json`)

The project manifest (`project.json`) lists every file to copy into the
archive, its role, its license note, and its runtime requirements (excerpt from
[examples/handoff/project.json](../examples/handoff/project.json)):

```json
{
  "version": 1,
  "name": "agent-handoff-example",
  "manifest": "scene.manifest.json",
  "files": [
    {"path": "scene.py", "role": "source", "license": "MIT; authorised authored example"},
    {"path": "scene_font.py", "role": "source", "license": "MIT; project helper, no fonts included"},
    {"path": "assets/project.svg", "role": "asset", "license": "MIT; original authored illustration"},
    {"path": "captions.zh.json", "role": "subtitle", "license": "MIT; authored review index"},
    {"path": "scene.manifest.json", "role": "config", "license": "MIT"},
    {"path": "timeline.json", "role": "config", "license": "MIT"},
    {"path": "RECEIVER.md", "role": "documentation", "license": "MIT"}
  ],
  "requirements": {
    "python_min": [3, 12],
    "python_max": [3, 14],
    "packages": {"manim": "0.22.1"},
    "tools": ["ffmpeg", "ffprobe"],
    "fonts": ["Arial Unicode MS"]
  }
}
```

All four subcommands (`export`, `inspect`, `unpack`, and `preflight`) enforce
these `project.json` rules:

- **Top-level schema**: Required keys are `version` (`1`), `name` (lowercase
  `[a-z][a-z0-9-]{0,79}`), `manifest` (relative POSIX path to the revision
  manifest, which must also appear in `files`), `files` (`1` to `128` entries),
  and `requirements`. Only `render` is accepted as an optional top-level key
  (see [Preserve requested render settings](#preserve-requested-render-settings)).
  All subcommands reject unknown keys anywhere in `project.json` and reject
  duplicate JSON keys.
- **Path resolution base**: `--project` and `--output` resolve relative to the
  caller's `CWD`. Inside `project.json`, `manifest` and `files[].path` resolve
  relative to the directory containing `project.json`. Inside the revision
  manifest (see
  [examples/handoff/scene.manifest.json](../examples/handoff/scene.manifest.json)),
  `source`, `timeline`, and `dependencies` resolve relative to the revision
  manifest's parent directory.
- **Explicit file closure**: Each `files[]` item requires `path`, `role`
  (`source`, `subtitle`, `asset`, `config`, or `documentation`), and a non-blank
  `license` redistribution note (`<= 200` characters). No directory glob or
  dependency discovery copies unrelated files; `export` verifies only that
  `source`, `timeline` (matching the schema in
  [examples/handoff/timeline.json](../examples/handoff/timeline.json)), and
  `dependencies` named in the revision manifest also appear in `files`, and reads
  `scene` (defaulting to `"Scene"` if omitted) to build the render recipe.
  Because Python source can still read undeclared paths at runtime, reviewers must
  check imports and runtime file reads directly.
- **Requirements**: All five keys inside `requirements` are mandatory
  (pass `{}` or `[]` when empty): `python_min` and `python_max` are
  `[major, minor]` bounds (minimum inclusive, maximum exclusive, with
  `python_min < python_max`); `packages` maps at most `64` distribution names
  to exact installed versions (no version solver or lockfile); `tools` lists at
  most `32` bare executable names on `PATH` (`[A-Za-z0-9_-]+`, not paths or full
  commands); `fonts` lists at most `32` non-blank font family names
  (`<= 100` characters each).

## Subcommands, validation, and exit codes

On success (and on `preflight` input or prerequisite failures), subcommands
print structured JSON to `stdout` and perform no package installation, font
downloads, or network requests:

- **`export`**: Reads `--project`, validates `project.json`, refuses symlink
  inputs, checks that `--output` does not already exist, verifies regular files
  and revision-manifest closure, generates `handoff.json`, and writes a DEFLATE
  ZIP archive.
- **`inspect`**: Validates the ZIP container and entries, `project.json`,
  `handoff.json` (version, project match, provenance, and its generated file
  inventory of byte counts and SHA-256 hashes), and the selected
  revision-manifest closure without writing files or importing project code.
- **`unpack`**: Runs all `inspect` checks first, rejects existing destination
  paths, writes entries to a private staging directory, and atomically renames the
  verified directory to the destination path.
- **`preflight`**: Verifies unpacked `project.json`, `handoff.json`, input
  hashes, revision-manifest closure, and the revision manifest's `timeline` file
  (such as `timeline.json`: `<= 64 KiB`, `1` to `64` contiguous segments ending
  within `600 s`, `<= 256` endpoints/samples). It then checks the active Python
  version against `python_min` and `python_max`, exact installed package
  versions, executables on `PATH`, and installed font families.

When `requirements.fonts` is non-empty, `preflight` spawns an isolated (`-I`)
child Python process in a temporary directory (`20 s` timeout) to run
`manimpango.list_fonts()`, matching exact family names and never importing the
project's `scene.py` or font helpers. If `manimpango` is unavailable, `preflight`
emits structured `errors` entries for both `font` (missing declared font check)
and `font_probe` (failed font-probe tool).

`handoff.json` records `producer` metadata (`python`, OS `system`, CPU `machine`),
the producer's `portable.py` SHA-256 hash (`tool.portable_module_sha256`), and
relative render command recipes and output mappings. When `preflight` checks an
unpacked project:

- `preflight` emits warnings if the receiving OS (`system`) differs from
  `producer.system` or the local `portable.py` hash differs from
  `tool.portable_module_sha256` (`preflight` does not emit separate warnings when
  `producer.python`, CPU `machine`, or external binary builds differ).
- Once unpacked input checks pass, `preflight` always appends a warning that
  matching font family names do not prove glyph coverage and that native Cairo,
  Pango, and FFmpeg versions and rendered media remain unverified until you render
  and inspect the video directly.

| Command | Exit `0` | Exit `1` | Exit `2` |
| --- | --- | --- | --- |
| `portable export` / `inspect` / `unpack` | Succeeded (`stdout` JSON) | Corrupt archive, unsafe path, schema/closure/hash mismatch, symlink input, or existing output or destination (plain error on `stderr`, nothing on `stdout`) | CLI usage error (`stderr`) |
| `portable preflight` | Checks passed (`"ready": true` on `stdout`, with or without warnings) | Not used for input/prerequisite checks (an unexpected `OSError` outside input validation prints to `stderr` and exits `1`) | Missing prerequisites **or** invalid/corrupt unpacked inputs (`"ready": false` on `stdout`, with `"code": "inputs"` on input errors), or CLI usage error (`stderr`) |
| `launcher.py` wrapper | Subcommand succeeded or `-I ... --help` requested | Propagated subcommand exit `1` | Invoked without `-I`, unknown command, or propagated subcommand exit `2` (such as `preflight` failures, `revision audit` differences, or usage errors) |

## Generated handoff, execution, and re-export

`portable export` generates `handoff.json` inside the archive, and `unpack`
places it in the unpacked root (such as `received/handoff.json`). A generated
`handoff.json` looks like:

```json
{
  "version": 1,
  "project": {"...": "validated project.json copy"},
  "files": [
    {"path": "scene.py", "role": "source", "license": "...", "bytes": 1234, "sha256": "..."}
  ],
  "producer": {"python": "3.13.x", "system": "Darwin", "machine": "arm64"},
  "tool": {"name": "manim-simple-render", "portable_module_sha256": "..."},
  "commands": {
    "receiving_launcher": {
      "file_from_trusted_product": "scene_production/launcher.py",
      "argv_placeholder": "<TRUSTED_LAUNCHER>",
      "required_python_option": "-I"
    },
    "render": [
      "python", "-I", "<TRUSTED_LAUNCHER>", "render",
      "--scene-file", "scene.py", "--scene", "HandoffExample",
      "--timeline", "timeline.json", "--output-dir", "runs",
      "--quality", "qm", "--record-scene-frames"
    ],
    "revision_manifest": "scene.manifest.json",
    "outputs": {
      "receipt": "runs/scene-*/result.json",
      "video": "receipt.video",
      "inspection_frames": "receipt.frames"
    }
  }
}
```

In this output, `"3.13.x"` illustrates the producer Python string,
`"receipt.video"` and `"receipt.frames"` refer to fields inside `result.json`,
and callers substitute `"$RECEIVER_PY"` and `"$RECEIVER_LAUNCHER"` for
`"python"` and `"<TRUSTED_LAUNCHER>"` in `commands.render`.

Before running anything, inspect all unpacked Python files (`scene.py` and
helpers), assets, the `commands.render` recipe in `handoff.json`, and the
project's receiving guide (such as
[examples/handoff/RECEIVER.md](../examples/handoff/RECEIVER.md)). Rendering
(including traced capture) executes project Python directly without a sandbox,
while `scene_production.revision` reads, AST-parses, edits those trusted files,
and runs `ffmpeg` on rendered media without executing project Python. Once you
trust the code, run and revise the project from `received/` using your trusted
`RECEIVER_PY` and `-I "$RECEIVER_LAUNCHER"`:

- **Baseline render**: Run the render recipe from `received/` (substituting
  `"$RECEIVER_PY"` and `"$RECEIVER_LAUNCHER"`), keeping the new local baseline
  receipt (`runs/scene-.../result.json`). See
  [scene production](scene-production.md).
- **Scoped edits and reinspection**: Invoke `"$RECEIVER_PY" -I
  "$RECEIVER_LAUNCHER" revision ...` (`capture`, `locate`, `apply`, `audit`,
  `verify`, and `restore`) per [scene revision](scene-revision.md). Animation
  changes require a full render; inspection-configuration changes can use
  `"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" inspect --from-run ...`
  (reinspection). Neither the inspection timeline (`timeline.json`) nor the
  subtitle review index (`captions.zh.json`) drives animation. Audio comparison
  remains outside `scene_production.revision audit`'s supported silent-video
  scope.
- **Traced capture**: Rendering with `--record-scene-frames` launches MSR's
  capture helper through the trusted `scene_production/launcher.py` with `-I` so
  an uninstalled MSR checkout can find `scene_production`. Inside the capture
  child process, `frame_capture.py` imports installed `manim` and MSR modules
  before prepending the trusted MSR checkout and the project's source directory
  ahead of remaining `sys.path` entries (such as `site-packages`).

Editing unpacked files invalidates the hashes in `handoff.json` by design. From
the parent of `received/`, re-export `received/project.json` to a new archive
path and validate the new bundle in a fresh directory (such as `received-v2`):

```sh
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable export \
  --project received/project.json \
  --output project-v2.zip
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable inspect project-v2.zip
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable unpack project-v2.zip received-v2
"$RECEIVER_PY" -I "$RECEIVER_LAUNCHER" portable preflight received-v2
```

Retain the original ZIP archive and SHA-256 hash, and return a separate evidence
summary using relative paths. Do not add local `runs/` outputs or machine-bound
receipts (which embed machine-local paths and metadata) to `project.json`, and
follow the export hygiene exclusions below. Because testing in a clean directory
on the same machine does not prove cross-machine portability, record the
observed environment and any manual or agent interventions in the evidence
summary.

## Security model, archive limits, and codecs

Review these integrity boundaries and size caps before exporting or unpacking
projects:

- **Unsigned consistency**: The `files` inventory in `handoff.json` proves
  self-consistency between unpacked bytes and recorded hashes, not source
  authenticity: anyone able to modify both file contents and hashes can create a
  different consistent bundle.
- **Path restrictions**: `export`, `inspect`, `unpack`, and `preflight` reject
  relative paths over `200` characters, `\` or `:` characters, control
  characters, traversal (`..`) or dot-prefixed segments, trailing spaces or
  dots, Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`,
  `COM1`-`COM9`, `LPT1`-`LPT9`), reserved archive filenames (`project.json`,
  `handoff.json`), file-as-directory collisions, and paths that collide under
  case-insensitive or Unicode-normalized (NFC/NFD) comparisons.
- **Blocked paths, extensions, and key markers**: All four subcommands reject
  blocked directories (`__pycache__`, `node_modules`, `venv`, `cache`, `caches`,
  `partial_movie_files`), blocked extensions (`.pem`, `.key`, `.p12`, `.pfx`,
  `.pyc`, `.env`), and PEM private-key markers
  (`-----BEGIN ... PRIVATE KEY-----`).
- **Author review and exclusions**: Authors must still inspect all selected
  bytes, sharing rights, and license notes because the tools do not verify
  `license` strings. Do not export private keys, credentials, unlicensed fonts
  or assets, caches, or old machine-bound receipts.
- **Archive safety and local races**: `inspect` and `unpack` reject symlinks,
  special files, encrypted entries, unlisted or missing files, and size or hash
  mismatches. Run `unpack` inside a private local directory where untrusted
  processes cannot race filesystem writes.

`export`, `inspect`, and `unpack` enforce these size and archive limits:

| Category | Enforced limit |
| --- | --- |
| Selected inputs | At most `128` files, `16 MiB` per file, `64 MiB` total selected bytes |
| JSON metadata | `2 MiB` each for `project.json`, `handoff.json`, and revision manifest (`64 KiB` for the revision manifest's `timeline` file during `preflight`) |
| ZIP pre-parse bounds | `72 MiB` max regular file size, `1 MiB` max central directory, at most `130` total entries (`128` selected + `project.json` + `handoff.json`), `68 MiB` entry size sum pre-check |
| Rejected ZIP layouts | Archive end-of-central-directory (EOCD) comments (per-entry directory comments still count against the `1 MiB` directory limit), ZIP64 end-directory records, split volumes, prefixes before offset `0`, trailing data after EOCD |
| Supported compression | `STORED` and `DEFLATE` only (`inspect` and `unpack` reject `BZIP2`, `LZMA`, and other codecs before decoding); MSR `export` writes `DEFLATE` |
| Stream verification | `inspect` and `unpack` check the complete stream, byte count, and CRC-32, capping decoded bytes at `min(limit, declared_size)` rather than trusting declared entry sizes as decompression budgets |

## Preserve requested render settings

An optional `render` object in `project.json` records the author's requested
render settings (omit the `render` key when unused rather than setting it to
`null`):

```json
{
  "render": {
    "quality": "ql",
    "fps": 24
  }
}
```

Rules for `render` handling:

- **Schema and recipe emission**: When `render` is present, `quality` is required
  (`ql`, `qm`, `qh`, `qp`, or `qk`, matching MSR's Manim quality ladder from 480p
  to 4K); `fps` (frames per second) is optional (`null`, or an integer from `1`
  to `240` matching the production API). `export` rejects extra keys and
  arbitrary command arguments, validates these values, and writes `--quality`
  (plus `--fps` when non-null) into `commands.render` in `handoff.json`.
  `inspect`, `unpack`, and re-export preserve `render` verbatim.
- **Legacy default without `render`**: Version-1 projects that omit `render` keep
  the default recipe of `--quality qm` and no `--fps` override. `export` does
  **not** infer settings from a previously rendered video, so authors must
  record non-default settings explicitly before export whenever scene code or
  timeline expectations depend on a specific resolution or frame rate.
- **Quality, FPS, and observed output**: Quality normally selects both
  resolution and Manim's default frame rate; an explicit `--fps` overrides that
  frame rate. Receipts and observed media record what actually rendered;
  requested settings do not guarantee identical rendered output across
  environments.
- **Compatibility and scope**: Adding `render` is an additive version-1 change
  that requires an updated receiving MSR installation. Older tools reject
  projects containing `render` (during `export`, `inspect`, `unpack`, and
  `preflight`) rather than silently ignoring it. Do not strip `render` to bypass
  that rejection; update the trusted MSR installation, or inspect the author's
  requested quality/FPS and manually construct the matching `render` command. The
  `render` field does not bundle Manim configuration, fonts, renderer selection,
  or environment variables. `export` writes `commands.render` in `handoff.json`
  as unsigned advice for inspection, and none of the transfer subcommands execute
  it.
