"""Registry and agent-contract tests."""

from __future__ import annotations

import pytest

from app.agents import available_keys, describe_agents, get_agent, get_agents, register_agent
from app.agents.base import FetchedTender, TenderSourceAgent, TenderStub
from app.config import Settings


def settings() -> Settings:
    return Settings(scrape_max_retries=1, scrape_backoff_base_seconds=0.0)


def test_sample_agent_is_registered():
    assert "sample-fixtures" in available_keys()


def test_unknown_source_names_the_alternatives():
    with pytest.raises(KeyError, match="sample-fixtures"):
        get_agent("no-such-portal", settings())


def test_get_agents_defaults_to_every_registered_agent():
    assert len(get_agents(None, settings())) == len(available_keys())


def test_describe_agents_shape_matches_tender_sources_columns():
    for entry in describe_agents():
        assert set(entry) == {"key", "display_name", "base_url", "country", "sector"}


def test_agent_without_key_is_rejected():
    with pytest.raises(ValueError, match="non-empty `key`"):

        @register_agent
        class Anonymous(TenderSourceAgent):  # pragma: no cover - construction fails
            async def discover(self, *, limit: int = 50):
                return []

            async def fetch(self, stub):
                raise NotImplementedError


def test_duplicate_key_is_rejected():
    """Two agents claiming the same key would silently shadow each other and
    corrupt dedup, since external_id is only unique per source."""
    with pytest.raises(ValueError, match="duplicate agent key"):

        @register_agent
        class Clash(TenderSourceAgent):  # pragma: no cover
            key = "sample-fixtures"

            async def discover(self, *, limit: int = 50):
                return []

            async def fetch(self, stub):
                raise NotImplementedError


@pytest.mark.asyncio
async def test_backoff_retries_then_succeeds():
    class Flaky(TenderSourceAgent):
        key = "flaky-test-agent"

        async def discover(self, *, limit: int = 50):
            return []

        async def fetch(self, stub):
            raise NotImplementedError

    agent = Flaky(Settings(scrape_max_retries=3, scrape_backoff_base_seconds=0.0))
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("portal rate limited")
        return "ok"

    assert await agent.with_backoff(flaky) == "ok"
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_backoff_gives_up_after_max_retries():
    class AlwaysFails(TenderSourceAgent):
        key = "always-fails-test-agent"

        async def discover(self, *, limit: int = 50):
            return []

        async def fetch(self, stub):
            raise NotImplementedError

    agent = AlwaysFails(Settings(scrape_max_retries=2, scrape_backoff_base_seconds=0.0))

    async def boom():
        raise ConnectionError("banned")

    with pytest.raises(ConnectionError):
        await agent.with_backoff(boom)


@pytest.mark.asyncio
async def test_combined_text_includes_attachments():
    """Threshold conditions often live in an annex, not the notice page."""
    from app.agents.base import TenderDocument

    fetched = FetchedTender(
        stub=TenderStub(external_id="x", title="t", source_url="u"),
        raw_text="Notice page text.",
        documents=[
            TenderDocument(file_name="annex-a.pdf", file_url="u/a.pdf", text_content="Annex text."),
            TenderDocument(file_name="scan.pdf", file_url="u/s.pdf", text_content=None),
        ],
    )

    combined = fetched.combined_text()
    assert "Notice page text." in combined
    assert "Annex text." in combined
    assert "annex-a.pdf" in combined
