# Existing diagram motion: narrow correctness fixes

A storyboard can already move one dot along a cubic path. These fixes retain
that existing path and timing API; no planner schema or provider changes are made.

The moving dot must start at the declared path start. Both the storyboard parser
and direct typed-codegen route now reject an inconsistent start instead of letting
Manim jump the actor at alpha zero. An equivalent named anchor at the same
coordinates is accepted. Author intentional cuts/repositioning explicitly.

The guide is behind the actor, preventing a newly created path from painting a
stripe through the solid dot. An explicitly named path destination retains its
existing opacity; other context dims as before. An anchor-only endpoint does not
identify an arbitrary nearby glyph as the destination.

The previous moving-label FadeOut and movement timing remain unchanged. Default
label tracking, its content-margin check and the proposed two-second travel cap
are deliberately not included. A valid lower-arc experimental layout made the
moving label intersect a middle queue label for five native frames [182,187),
while another authored arc avoided that pair. Checking viewport containment did
not solve this context collision. The actor's intentional destination overlap
also occupied 144 frames, so rejecting every intersection would break valid arrival
behavior. A future label-following feature needs reviewed layout adaptation or
explicit author diagnostics before changing this default.

Review both continuous motion and native-size frames in the authored layout.
No automatic path solving, semantic-quality score, new UI or learning improvement
is claimed. Geometry/source tests and silent emitted-code controls exercise these
rules; they do not establish real-provider narration quality or comprehension.
