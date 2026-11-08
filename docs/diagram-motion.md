# Generated diagram motion

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md)

An Explainer storyboard can move one dot along a cubic path. The storyboard
parser and generated Scene enforce the following start-position and layering rules.

The moving dot must start at the declared path start. Both the storyboard parser
and direct typed-codegen route reject an inconsistent start instead of letting
Manim jump the actor at alpha zero. An equivalent named anchor at the same
coordinates is accepted. Author intentional cuts/repositioning explicitly.

The guide is behind the actor, preventing a newly created path from painting a
stripe through the solid dot. An explicitly named path destination retains its
existing opacity; other context dims as before. An anchor-only endpoint does not
identify an arbitrary nearby glyph as the destination.

Moving labels fade out during movement; default label-following, a following-label
content-margin check and a two-second travel cap are not provided. Review both
continuous motion and native-size frames in the authored layout. Geometry/source
tests and silent emitted-code controls do not establish narration quality or
viewer comprehension.

## Historical layout observation

During the PR 23 motion work, a valid lower-arc experimental layout made the
moving label intersect a middle queue label for five native frames [182,187),
while another authored arc avoided that pair. Checking viewport containment did
not solve this context collision. The actor's intentional destination overlap
also occupied 144 frames, so rejecting every intersection would break valid arrival
behavior. A future label-following feature needs reviewed layout adaptation or
explicit author diagnostics before changing this default.

This observation explains the scope of the existing behavior; it is not a new
validation run or an automatic path/layout solver.
