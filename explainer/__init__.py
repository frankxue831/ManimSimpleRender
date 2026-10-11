"""Explainer Engine: turn real code into narrated animated explainer videos.

A new package beside ``app`` that reuses the Manim render core as a library.
v1 hand-authors a storyboard (``story.yaml``), verifies every on-screen code
fact against the source repo, deterministically generates a Manim scene, and
renders it.
"""
