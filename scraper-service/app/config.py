"""Service configuration.

Every tunable lives here. Nothing — model IDs least of all — is hardcoded at
a call site, so swapping a model or an embedding provider is a config change
(CLAUDE.md, "Model usage").
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Must equal the vector(N) declared in supabase/migrations/0001_extensions.sql.
# Changing it requires a migration plus a full re-embedding — see REPOMIX.md
# ADR-6. Asserted at startup in main.py so a mismatch fails loudly and early
# rather than as an opaque INSERT error thousands of rows later.
EMBEDDING_DIM = 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Anthropic ---
    anthropic_api_key: str | None = None
    # Extraction quality is worth more than per-document cost here: a missed
    # threshold condition can disqualify a bid.
    anthropic_model: str = "claude-opus-5"
    anthropic_max_tokens: int = 16000
    extraction_timeout_seconds: float = 300.0
    # Opt into server-side refusal fallbacks. A safety classifier can decline
    # a request — HTTP 200 with stop_reason "refusal", not an exception — and
    # defense procurement is exactly the adjacent-to-restricted material that
    # draws a false positive. With this on, the API re-runs the declined
    # request on Anthropic's recommended substitute inside the same call, so
    # the tender is extracted instead of parked. Turn it off only to observe
    # raw refusal rates.
    anthropic_fallbacks: bool = True

    # --- Extraction provider ---
    # Which backend performs schema-constrained extraction. The
    # validate-repair-review ladder (ADR-2) and the refusal routing (ADR-13)
    # are provider-independent and run identically either way; only the
    # transport and the response shape differ.
    #
    # "anthropic" is the documented default (ADR-7). "gemini" exists because
    # the operator asked for it; the tradeoffs it carries are recorded in
    # REPOMIX.md ADR-20 rather than argued here.
    extraction_provider: Literal["anthropic", "gemini"] = "anthropic"

    # --- Google Gemini ---
    gemini_api_key: str | None = None
    # Never referenced at a call site — the model is configuration, per
    # CLAUDE.md. The default is the model the operator named; if Google's API
    # spells it differently, `validate_configuration` fails at startup with
    # the list of ids the key can actually see, rather than letting every
    # document fail one at a time against a name nobody checked.
    gemini_model: str = "gemini-3.6-flash"
    gemini_max_output_tokens: int = 16000
    # Embeddings, for deployments that do not want a third vendor. Reaches
    # 1024 via `output_dimensionality`, which is the width the schema
    # declares -- so selecting it needs no migration and no re-embedding.
    gemini_embedding_model: str = "gemini-embedding-001"
    # Verify the model id against ListModels when the service starts. Costs
    # one metadata request and converts a typo from ~190 identical per-tender
    # failures into one refusal to start.
    gemini_verify_model: bool = True

    # --- Notifications ---
    # A needs_review tender is invisible until a human opens it, and there is
    # no review queue in the dashboard yet, so this digest is currently the
    # only thing that says "something is waiting". Without smtp_host nothing
    # is sent and the digest is logged instead — the pipeline never fails
    # because mail is unconfigured.
    notify_email: str | None = "omer200338@gmail.com"
    notify_from_email: str | None = None
    # Send even when a run is entirely clean. Off by default: a mail that
    # always arrives stops being read, and this one exists to be noticed.
    notify_on_success: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    # --- Embeddings ---
    # "voyage" for production, "hash" for offline development and tests.
    embedding_provider: str = "voyage"
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3-large"
    embedding_dim: int = EMBEDDING_DIM

    # --- Supabase ---
    supabase_url: str | None = None
    # Service role key: bypasses RLS. Never expose it to the browser.
    supabase_service_role_key: str | None = None

    # --- Scraping ---
    # Government portals rate-limit and IP-ban aggressively. These defaults
    # are deliberately conservative; raise them only against a portal whose
    # terms of service permit it.
    scrape_concurrency: int = 4
    scrape_timeout_seconds: float = 60.0
    scrape_max_retries: int = 4
    scrape_backoff_base_seconds: float = 2.0
    scrape_backoff_max_seconds: float = 60.0
    scrape_user_agent: str = "TendersFinderBot/0.1 (+https://tendersfinder.example/bot)"
    playwright_headless: bool = True

    # --- Service ---
    log_level: str = "INFO"
    # Dry run keeps the pipeline fully exercisable without a database.
    dry_run: bool = False

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_extraction(self) -> bool:
        """Whether the *selected* provider has what it needs.

        Asked instead of `has_anthropic` everywhere the question is "can this
        run at all". A key for the provider you are not using is not a
        configured pipeline, and an Anthropic key present while
        EXTRACTION_PROVIDER=gemini must not read as ready.
        """
        if self.extraction_provider == "gemini":
            return self.has_gemini
        return self.has_anthropic

    @property
    def extraction_model(self) -> str:
        """The model id for the selected provider, for logs and stored rows."""
        if self.extraction_provider == "gemini":
            return self.gemini_model
        return self.anthropic_model

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def has_email_notifications(self) -> bool:
        return bool(self.notify_email and self.smtp_host)

    @property
    def mail_from(self) -> str | None:
        """Envelope sender. Falls back to the SMTP user, then the recipient.

        Most providers reject a From they do not own, so guessing an address
        here would produce mail that silently never arrives.
        """
        return self.notify_from_email or self.smtp_username or self.notify_email


@lru_cache
def get_settings() -> Settings:
    return Settings()
