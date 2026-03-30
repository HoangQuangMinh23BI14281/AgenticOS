"""
LCM Database Migration — Facade for the modular migration package.
This file is kept for backward compatibility and to satisfy the <300 lines rule.
"""

from .migration import run_lcm_migrations

__all__ = ["run_lcm_migrations"]
