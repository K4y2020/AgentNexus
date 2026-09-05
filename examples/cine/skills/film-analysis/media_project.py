"""
media_project.py — legacy compat entry point for Cine film-analysis skill.
This module is preserved for backwards compatibility.
New code should use examples.cine.skills.film-analysis.pipeline directly.
"""
from .pipeline import run_pipeline, ProjectRecord, SourceMediaRecord

__all__ = ["run_pipeline", "ProjectRecord", "SourceMediaRecord"]
