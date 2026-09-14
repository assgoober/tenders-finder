"""Aggregation agents.

Importing this package registers every agent. A new portal is added by
creating a module here and importing it below — nothing in the pipeline
changes (REPOMIX.md ADR-5).
"""

from .base import FetchedTender, TenderDocument, TenderSourceAgent, TenderStub
from .find_a_tender import FindATenderAgent
from .govil import GovIlTendersAgent
from .mod_balam import ModBalamAgent
from .playwright_agent import PlaywrightPortalAgent
from .ted import TedAgent
from .registry import available_keys, describe_agents, get_agent, get_agents, register_agent

# Side-effect imports: each module registers its agents on import.
from . import find_a_tender  # noqa: F401
from . import govil  # noqa: F401
from . import mod_balam  # noqa: F401
from . import sample_agent  # noqa: F401
from . import ted  # noqa: F401

__all__ = [
    "FetchedTender",
    "GovIlTendersAgent",
    "ModBalamAgent",
    "PlaywrightPortalAgent",
    "TedAgent",
    "FindATenderAgent",
    "TenderDocument",
    "TenderSourceAgent",
    "TenderStub",
    "available_keys",
    "describe_agents",
    "get_agent",
    "get_agents",
    "register_agent",
]
