"""ManimSimpleRender rewrite package.

Layered: ui -> controller -> services (config, scenes, render, validation).
Services must not import app.ui.*. See the rewrite plan / memory for the design.
"""
