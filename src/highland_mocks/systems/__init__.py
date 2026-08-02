"""Vertical definitions for Highland's six fictional source systems."""

from . import communications, crm, knowledge, observability, projects, support

SYSTEMS = {
    "crm": crm,
    "knowledge": knowledge,
    "support": support,
    "observability": observability,
    "communications": communications,
    "projects": projects,
}

__all__ = ["SYSTEMS"]
