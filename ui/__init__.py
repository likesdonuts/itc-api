"""UI layer: static HTML rendering of the data the data layer stored.

Read-only with respect to data/ -- nothing here fetches anything.
"""

from __future__ import annotations

from . import templates
from .render import RenderReport, render_site

__all__ = ["RenderReport", "render_site", "templates"]
