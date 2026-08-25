"""Aggregation agents.

Importing this package registers every agent. A new portal is added by
creating a module here and importing it below — nothing in the pipeline
changes (REPOMIX.md ADR-5).
"""

from .base import FetchedTender, TenderDocument, TenderSourceAgent, TenderStub
from .playwright_agent import PlaywrightPortalAgent
from .registry import available_keys, describe_agents, get_agent, get_agents, register_agent

# Side-effect imports: each module registers its agents on import.
from . import sample_agent  # noqa: F401

__all__ = [
    "FetchedTender",
    "PlaywrightPortalAgent",
    "TenderDocument",
    "TenderSourceAgent",
    "TenderStub",
    "available_keys",
    "describe_agents",
    "get_agent",
    "get_agents",
    "register_agent",
]
