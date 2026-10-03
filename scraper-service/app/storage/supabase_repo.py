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

    def ingest_fingerprints(self, source_id: str | None) -> dict[str, dict]:
        """What this source already holds, keyed by external_id.

        One query for the whole source rather than one per tender: the point
        of this index is to avoid work, and N round trips to decide whether to
        skip N documents saves nothing.

        This used to filter `extraction_status = 'extracted'`, which was right
        while an embedding could only come from an extraction. Under
        listing-first ingestion it was silently catastrophic: a listing-embedded
        row is `pending`, so it never appeared in the index, so every sweep
        re-embedded every tender it had already seen. Nothing looked wrong --
        the counts were right, the rows were right, and the bill was paid
        again each night.

        So the filter is gone and the *caller* decides what "current" means for
        the mode it is running in. A `needs_review` row still gets retried,
        because it carries no embedding and this index reports that honestly.
        """
        if not source_id:
            return {}

        index: dict[str, dict] = {}
        page, size = 0, 1000
        while True:
            result = (
                self.client.table("tenders")
                .select("external_id, extraction_model, extraction_status, metadata, embedding")
                .eq("source_id", source_id)
                .range(page * size, page * size + size - 1)
                .execute()
            )
            rows = result.data or []
            for row in rows:
                meta = row.get("metadata") or {}
                index[row["external_id"]] = {
                    "model": row.get("extraction_model"),
                    "prompt_version": (meta.get("extraction") or {}).get("prompt_version"),
                    "raw_row": (meta.get("listing") or {}).get("raw_row"),
                    "extracted": row.get("extraction_status") == "extracted",
                    # A row with no vector is not "already done" in any mode --
                    # it is in the table and invisible to every query that
                    # matters, which is the one state a sweep must always fix.
                    "embedded": row.get("embedding") is not None,
                }
            if len(rows) < size:
                break
            page += 1
        return index

    # --- Match alerts -------------------------------------------------------

    def pending_match_alerts(self, limit: int = 500) -> list[dict]:
        """Undelivered matches, joined to the addresses that should get them.

        Migration 0016. The join reaches auth.users, which no tenant role can
        read, so this goes through a SECURITY DEFINER function rather than a
        PostgREST select.
        """
        result = self.client.rpc("pending_match_alerts", {"p_limit": limit}).execute()
        return result.data or []

    def tenders_awaiting_extraction(self, limit: int = 100) -> list[dict]:
        """The matched-but-unread queue (migration 0019).

        A SECURITY DEFINER function rather than a PostgREST select because it
        joins across tenants: it answers "which tenders did *anybody* match",
        which no single organisation is allowed to ask.
        """
        result = self.client.rpc(
            "tenders_awaiting_extraction", {"p_limit": limit}
        ).execute()
        return result.data or []

    def mark_matches_alerted(self, match_ids: list[str]) -> int:
        """Stamp alerted_at, and only after a send actually succeeded.

        Deliberately a separate call from the read: a function that returned
        matches and marked them in one statement would mark a match delivered
        that never left the building.
        """
        if not match_ids:
            return 0
        result = self.client.rpc(
            "mark_matches_alerted", {"p_match_ids": match_ids}
        ).execute()
        return result.data if isinstance(result.data, int) else 0

    def age_tender_status(self) -> dict[str, int]:
        """Re-derive open / closing_soon / closed from each tender's deadline.

        Migration 0011. The scheduled call is the one that matters -- a
        deadline passes on the clock, not on a sweep -- but calling it at the
        end of a run means the corpus is never stale for the interval between
        the sweep and the next scheduled pass, which is when a human is most
        likely to be looking at it.
        """
        result = self.client.rpc("age_tender_status", {}).execute()
        row = (result.data or [{}])[0] if isinstance(result.data, list) else (result.data or {})
        return {
            "to_open": row.get("to_open", 0),
            "to_closing_soon": row.get("to_closing_soon", 0),
            "to_closed": row.get("to_closed", 0),
        }

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

    def generate_matches_for_profile(self, profile_id: str) -> int:
        """Score one profile against every open tender (migration 0015).

        The tender-side trigger only ever matches tenders arriving AFTER a
        profile exists; this is the half that gives a new profile the corpus
        that was already there.
        """
        result = self.client.rpc(
            "generate_matches_for_profile", {"p_profile_id": profile_id}
        ).execute()
        return int(result.data or 0)

    def set_profile_embedding(self, profile_id: str, embedding: list[float]) -> None:
        self.client.table("alert_profiles").update({"embedding": embedding}).eq(
            "id", profile_id
        ).execute()
