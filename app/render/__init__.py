"""Render services: job model, command building, subprocess runner, worker,
aggregation, quality ladder.

``runner``/``command_builder``/``jobs``/``aggregation``/``quality`` are Qt-free; only
``worker`` imports the GUI toolkit.
"""
