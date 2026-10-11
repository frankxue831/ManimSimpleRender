"""Render job model.

A render job is one (file, scene_class) pair. Binding the scene to its OWNING
file is what replaces the legacy file x scene cross-product (render.py:34-49)
and disambiguates duplicate class names. No Qt.
"""

import os
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderJob:
    """A single unit of render work: render ``scene_class`` defined in ``file_path``."""

    file_path: str
    scene_class: str


def build_jobs(selected_scenes):
    """Map an explicit selection of :class:`~app.scenes.discovery.DiscoveredScene`
    to :class:`RenderJob` objects - one per selection, bound to its owning file.

    Identity travels with each selected scene (Condition 2), so duplicate class
    names across files yield distinct jobs and there is no cross-product. Order is
    preserved; identical (file, scene) pairs are de-duplicated. ``total`` for
    progress is simply ``len(build_jobs(...))``.
    """
    jobs = []
    seen = set()
    for scene in selected_scenes:
        job = RenderJob(file_path=scene.file_path, scene_class=scene.scene_class)
        if job not in seen:
            seen.add(job)
            jobs.append(job)
    return jobs


def _media_component(value):
    """Conservative cross-platform media path identity (NFC + casefold)."""
    return unicodedata.normalize("NFC", str(value)).casefold()


def media_output_collisions(jobs):
    """Return groups of jobs whose manim media output would alias each other (BL-05).

    manim keys output on the module *basename* + scene class
    (``videos/<stem>/<quality>/<Scene>.mp4``), so two selected jobs from different
    folders that share a file basename and scene class write to the same path at a
    given resolution and the second silently overwrites the first. Returns a list of
    colliding job groups (each length >= 2); empty when there are none.

    Grouping uses NFC + casefold so basename stems / scene names that differ only
    by case or Unicode canonical form are treated as the same output identity
    (conservative cross-platform check).
    """
    groups = {}
    for job in jobs:
        stem = os.path.splitext(os.path.basename(job.file_path))[0]
        key = (_media_component(stem), _media_component(job.scene_class))
        groups.setdefault(key, []).append(job)
    return [group for group in groups.values() if len(group) > 1]
