# Transfer an editable ordinary Scene project

`python -m scene_production.portable` adds explicit `export`, `inspect`, `unpack`
and `preflight` steps around the existing rendering/revision tools. It does not
interpret feedback, install dependencies, execute imported scenes or replace
ordinary Python with another animation language. The author still selects inputs
and writes the animation; a receiving agent still makes semantic edit decisions.

Example: `examples/handoff/project.json`. Its `files` array explicitly selects
sources, a subtitle review index, an original SVG asset, inspection timeline,
revision manifest and receiving instructions. Each file names a role and a
redistribution permission/licence note. No directory glob or dependency discovery
copies unrelated files. Source code can still read undeclared external resources;
the tool checks declared closure, not arbitrary Python behaviour.

```sh
python -m scene_production.portable export --project examples/handoff/project.json --output project.zip
python -m scene_production.portable inspect project.zip
python -m scene_production.portable unpack project.zip received
python -m scene_production.portable preflight received
```

Inspection validates a bounded ZIP inventory, relative names, SHA256 and the
selected revision manifest inputs. It does not import project Python. Unpack
requires a new destination and validates everything before publishing it.
Preflight checks input hashes, static scene-ID/timeline mapping, Python range,
exact declared distribution versions, executables on PATH and installed font
families. It may import installed manimpango in an isolated Python process to list
fonts, never the project's scene/font helper. Missing prerequisites return exit2;
corrupt archives/invalid operations return exit1. Export/inspect/unpack success is0.
No installation, font download or network request is performed.

Python bounds are `[major, minor]`, minimum inclusive and maximum exclusive.
Package requirements are exact versions rather than a resolver/lockfile. Tools
are executable names, not commands. The archive records producer Python/OS/CPU and
portable-module hash, plus generated relative render recipes/output mappings.
Different OS/tool implementations warn. Cairo/Pango/FFmpeg versions and glyph
coverage are not fully captured by this MVP; a matching font name is insufficient
to claim identical output. Actual rendering and media inspection remain necessary.

After reviewing and trusting code, use the render recipe in handoff.json and the
steps in examples/handoff/RECEIVER.md. Keep the new baseline receipt and use existing
`scene_production.revision` capture/locate/apply/audit/verify/restore. Changes in
animation require a full render; changing inspection configuration can use
reinspection. Neither timeline drives animation. Audio comparison remains outside
the revision audit's supported silent-video scope.

The inventory is unsigned: someone able to rewrite both contents and hashes can
make a different consistent bundle. This is not source authentication, a sandbox,
a complete secret scanner or a semantic quality check. Known hidden/secret/cache
paths, key-file extensions and private-key markers are refused, but authors must
still review all selected bytes, sharing rights and licence notes. A permission
note is an assertion, not an automated licence verification. Do not export private
keys, credentials, unlicensed fonts/assets, caches or machine-bound old receipts.

Limits:128selected files,16MiB per file,64MiB total selected bytes,2MiB per JSON.
Archives reject links/special files, encrypted entries, traversal, absolute names,
portable case/Unicode aliases, unlisted files and hash mismatches. This assumes a
normal local filesystem, not an adversarial process racing writes during import.

Editing intentionally invalidates the old inventory. Re-export project.json to a
new version, retain the original ZIP/hash and return a relative evidence summary.
A same-machine clean-directory test does not establish cross-machine portability
or faster editing. Report observed environment and any manual/agent intervention.
