"""Supabase persistence for the ingestion pipeline.

Uses the service-role key, which bypasses RLS. That is correct for this
process — it writes the shared `tenders` corpus, which belongs to no tenant —
and it is exactly why this key must never reach the browser.

The one rule this layer enforces: the service writes shared data only. It
never writes a tenant-scoped row (`alert_profiles`, `tender_pipeline`) on
behalf of an organization. Tenant rows come from the user's own authenticated
session, under RLS. Matches are the exception, and they are produced inside
the database by `generate_matches_for_tender`, which derives
`organization_id` from the profile rather than taking it from this process.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


class SupabaseRepository:
    def __init__(self, settings: Settings | None = None, client=None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self):
        if self._client is None:
            if not self.settings.has_supabase:
                raise RuntimeError(
                    "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required. "
                    "Set DRY_RUN=true to run the pipeline without a database."
                )
            from supabase import create_client

            self._client = create_client(
                self.settings.supabase_url,
                self.settings.supabase_service_role_key,
            )
        return self._client

    # --- Sources ------------------------------------------------------------

    def upsert_source(self, source: dict) -> str:
        """Register an agent as a `tender_sources` row and return its id."""
        result = (
            self.client.table("tender_sources")
            .upsert(source, on_conflict="key")
            .execute()
        )
        return result.data[0]["id"]

    # --- Scrape runs --------------------------------------------------------

    def start_run(self, source_id: str | None) -> str | None:
        result = (
            self.client.table("scrape_runs").insert({"source_id": source_id}).execute()
        )
        return result.data[0]["id"] if result.data else None

    def finish_run(
        self,
        run_id: str | None,
        *,
        items_found: int,
        items_ingested: int,
        ok: bool,
        error: str | None = None,
    ) -> None:
        if run_id is None:
            return
        self.client.table("scrape_runs").update(
            {
                "finished_at": "now()",
                "items_found": items_found,
                "items_ingested": items_ingested,
                "ok": ok,
                "error": error,
            }
        ).eq("id", run_id).execute()

    # --- Tenders ------------------------------------------------------------

    def upsert_tender(self, row: dict[str, Any]) -> str:
        """Insert or update one tender, keyed on (source_id, external_id).

        Writing `embedding` here is what fires `tenders_embedded_match` in
        migration 0005, which fans the tender out across every active alert
        profile. Persisting a tender and alerting on it are therefore a
        single transaction — there is no window where a tender exists but
        nobody has been notified.
        """
        result = (
            self.client.table("tenders")
            .upsert(row, on_conflict="source_id,external_id")
            .execute()
        )
        return result.data[0]["id"]

    def attach_documents(self, tender_id: str, documents: list[dict]) -> None:
        if not documents:
            return
        payload = [{**doc, "tender_id": tender_id} for doc in documents]
        self.client.table("tender_documents").insert(payload).execute()

    def mark_needs_review(self, tender_id: str, error: str | None) -> None:
        self.client.table("tenders").update(
            {"extraction_status": "needs_review", "extraction_error": error}
        ).eq("id", tender_id).execute()

    # --- Profiles -----------------------------------------------------------

    def profiles_missing_embeddings(self, limit: int = 100) -> list[dict]:
        result = (
            self.client.table("alert_profiles")
            .select("id, description")
            .is_("embedding", "null")
            .limit(limit)
            .execute()
        )
        return result.data or []

    def set_profile_embedding(self, profile_id: str, embedding: list[float]) -> None:
        self.client.table("alert_profiles").update({"embedding": embedding}).eq(
            "id", profile_id
        ).execute()
