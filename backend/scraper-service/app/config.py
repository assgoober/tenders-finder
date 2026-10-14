"""Service configuration.

Every tunable lives here. Nothing — model IDs least of all — is hardcoded at
a call site, so swapping a model or an embedding provider is a config change
(CLAUDE.md, "Model usage").
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Must equal the vector(N) declared in backend/supabase/migrations/0001_extensions.sql.
# Changing it requires a migration plus a full re-embedding — see REPOMIX.md
# ADR-6. Asserted at startup in main.py so a mismatch fails loudly and early
# rather than as an opaque INSERT error thousands of rows later.
EMBEDDING_DIM = 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
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

    # --- TED (EU) ---

    # Widened from [35000000] once ingestion stopped costing a model call per
    # tender. Probed against the live API, contract notices, 30-day window:
    #
    #   35000000  security, defence, police, fire-fighting        594
    #   34000000  transport equipment: vehicles, aircraft, ships 2085
    #   72000000  IT services                                    2166
    #   50000000  repair and maintenance                         2042
    #   48000000  software and information systems               1582
    #   38000000  laboratory, optical, precision instruments     1145
    #   31000000  electrical machinery                            976
    #   44000000  construction structures and materials           964
    #   32000000  radio, television, telecom equipment            888
    #   51000000  installation services                           478
    #   18000000  clothing, footwear, personal equipment          327
    #   63000000  supporting transport services                   270
    #   73000000  research and development                        213
    #   75000000  administration and defence services             198
    #                                        union, deduplicated 11664
    #
    # The union is not the sum: a single notice routinely carries codes from
    # four divisions, which is exactly why filtering here is the wrong place
    # to be precise. The matching engine scores every tender against a real
    # capability profile downstream; a division dropped at ingestion is gone.
    # Harvest wide, filter late.
    ted_cpv_codes: list[int] = [
        # Defence-adjacent core.
        35000000, 34000000, 72000000, 50000000, 48000000, 38000000,
        31000000, 44000000, 32000000, 51000000, 18000000, 63000000,
        73000000, 75000000,
        # Dual-use, per the architecture rule that this product is
        # sector-agnostic by construction: a civilian infrastructure or
        # medical procurement must flow through the same tables and the same
        # matching function. Open notices each, measured:
        #   45000000 construction work                      7890
        #   33000000 medical equipment, pharmaceuticals      6134
        #   71000000 architectural and engineering services  5463
        #   79000000 business services                       2847
        #   39000000 furniture and furnishings               1859
        #   30000000 office and computing machinery          1618
        #   42000000 industrial machinery                    1197
        45000000, 33000000, 71000000, 79000000, 39000000, 30000000, 42000000,
        60000000, 85000000, 80000000, 24000000, 64000000, 43000000,
        22000000, 65000000, 19000000, 14000000,
    ]
    # How far back a sweep reaches. Thirty days keeps the run bounded and
    # still covers the 37-day legal minimum between a contract notice and the
    # shortlist deadline, so nothing closes before we have seen it.
    ted_lookback_days: int = 30

    # Filter on when a notice CLOSES, not on when it was published.
    #
    # These are not approximations of each other, and the publication window
    # was wrong in both directions at once. Measured on defence CPV alone:
    #
    #   published inside our 30-day window          594
    #   actually still open                         603
    #   MISSED: open but published >30 days ago     181   <- 30% of what is open
    #   WASTED: inside our window, already closed    62
    #   the overlap we actually got                 422
    #
    # A tender published 50 days ago that closes next month is precisely what
    # a bid team wants, and we dropped it. A tender published yesterday that
    # closed this morning is noise we paid to embed. Seventy percent recall on
    # the only question the customer asks.
    #
    # The OR is not optional. A restricted procedure publishes a
    # request-to-participate deadline and no tender deadline until the
    # shortlist exists, so keying on the tender deadline alone silently drops
    # every two-stage procurement -- which is most large defence work.
    # Together they find 774 open notices where the window found 594.
    ted_open_only: bool = True

    # Seconds to wait between shards. Thirty-one paginated walks fired
    # back-to-back is indistinguishable from an attack, and TED answered the
    # first wide sweep with 429 on all thirty-one in eighteen seconds. The
    # limiter is on sustained request RATE, so the fix is a gap the agent
    # takes voluntarily rather than one the server imposes -- a sweep that
    # finishes slowly beats a sweep that finishes as an IP ban.
    ted_shard_pause_seconds: float = 3.0

    # How far back the window STARTS. The window is a band, not a horizon:
    # offset 0 asks for the last 30 days, offset 30 for the 30 days before
    # that. A historical backfill is therefore a sequence of bounded,
    # independently retryable jobs rather than one request for "everything
    # since 2016" that nobody could resume after it died on page 400.
    ted_window_offset_days: int = 0

    # How many CPV divisions go into one query. Fourteen divisions in one
    # request is not a big query -- it is a walk that cannot be resumed and
    # whose failure loses everything. One at a time, it is fourteen small
    # jobs, and the one that dies is the only one that has to be redone.
    #
    # Measured: walking 34000000 alone reached all 2085 notices it claimed,
    # 22 pages, 100%. There is no per-query cap -- the earlier "623 claimed,
    # 551 served" reading was a connection reset under sustained paging,
    # misread as a service limit. Sustained paging IS what the API objects
    # to, which is the real argument for cutting the sweep up.
    #
    # 0 puts every division in one query (the old behaviour).
    ted_divisions_per_query: int = 1
    # Notices per API page. The service accepts larger, but a smaller page
    # fails cheaper and resumes from a token rather than from the start.
    ted_page_size: int = 100
    # Measured on a 40-notice sample: 14 carried no deadline at all, and
    # every one of those was `can-standard` -- a contract AWARD notice,
    # announcing a deal already done. 47% of the raw defence-CPV feed is
    # award notices. They are real procurement data and completely useless
    # to a bid team, so the default asks for contract notices only.
    ted_notice_types: list[str] = ["cn-standard"]
    # Which CPV field the filter reads, and it is a recall/precision dial:
    #   classification-cpv     623 notices -- ANY code on the notice
    #   main-classification-lot 394 notices -- what the lot is actually FOR
    # Measured on the same 30-day window. The 229-notice difference is
    # things like a hospital supply order carrying one protective-clothing
    # code among twenty-eight.
    #
    # Defaults to the broad field on purpose. Precision is not this stage
    # job: the matching engine scores every tender against a capability
    # profile downstream, so an irrelevant notice ranks low and costs a
    # row, while a defence lot dropped at ingestion is gone for good.
    # Filter late, harvest wide.
    ted_cpv_field: str = "classification-cpv"

    # --- Ingestion mode -------------------------------------------------
    # False: a tender is embedded from its LISTING at ingestion and the full
    # PEGS extraction is deferred until it matches somebody's profile.
    # True: the original behaviour -- fetch the document and extract every
    # tender before it can be matched at all.
    #
    # The default flipped because the old one had a ceiling we hit. Every
    # tender cost one model call before we knew whether any customer wanted
    # it, so a 551-notice sweep aborted on quota at 483 and 11,664 available
    # notices were unreachable in principle, not in practice.
    #
    # What we give up is real and worth naming: until a tender is extracted
    # its `summary`, eligibility conditions and evaluation criteria are
    # empty, and it is matched on the buyer's own title and description
    # rather than on its requirements. What we gain is that it is matched at
    # all. A tender nobody can see has no extraction quality to discuss.
    extract_on_ingest: bool = False

    # How many matched-but-unread tenders to extract per run. This is the
    # spending dial: the queue is ordered by deadline, so a budget that runs
    # out runs out on the tenders with the most time left.
    #
    # The scheduled sweep spends it BEFORE harvesting (run_sweep.py): each
    # extraction costs one model call and one embedding request, and the
    # harvest spends whatever embedding budget it is given, so run after it
    # this would get nothing. 50 is 5% of Gemini's 1,000 free embedding
    # requests a day, taken from the tail of the last source. 0 disables it.
    extract_matched_limit: int = 50

    # Tenders per embedding REQUEST. The free tier limits requests per
    # minute, not tenders per minute, so this is the dial that decides
    # whether a wide sweep finishes. At 1 -- which is what the pipeline
    # effectively used before it was batched -- a 6,000-notice sweep died
    # after 213. At 96, the provider's own cap, the same sweep is 63
    # requests.
    embed_batch_size: int = 96

    # Tokens per minute the embedding model allows. This -- not the request
    # count -- is what actually bounds a wide sweep, and it was invisible
    # until the account's rate-limit dashboard was read:
    #
    #   Gemini Embedding 1   RPM 106/100   TPM 37.57K/30K   RPD 965/1K
    #
    # Measured over 1,001 real listing rows: 792 characters each, about 198
    # tokens. So 30,000 TPM / 198 = roughly 150 tenders per minute, and that
    # ceiling does not move with batch size -- 48 per request at 3.2
    # requests/min and 96 at 1.6 deliver the same 151 tenders. Batching buys
    # request headroom (402 requests for 38,500 tenders, against 1,000/day);
    # it does not buy throughput. 38,500 tenders is about four hours of
    # wall clock, and a sweep that does not know this simply hits 429 in its
    # first minute and spends the rest of the run waiting.
    #
    # Set a little under the stated ceiling: the token count here is an
    # estimate from characters, and being wrong upward costs a 429.
    embed_tokens_per_minute: int = 27000

    # Ceiling on one request's tokens. The batch is cut by whichever comes
    # first, this or `embed_batch_size`, because the two are not equivalent:
    # the p95 listing is 2,590 characters, and 96 of those is 62,000 tokens
    # -- twice the entire minute's budget, in a single request.
    embed_max_tokens_per_request: int = 18000

    # --- Find a Tender (UK) ---
    # The API filters on update time only, so the window is how far back a
    # sweep looks for notices that were touched -- not how far back their
    # deadlines run. Sixty days because a UK above-threshold notice is
    # commonly edited once after publication, and a window shorter than the
    # edit interval keeps re-finding the same notices as if they were new.
    fts_lookback_days: int = 60
    # Releases per page. The service accepts 100 and rate-limits on sustained
    # paging like every other portal here, so a larger page is fewer requests
    # for the same rows.
    fts_page_size: int = 100

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
    # Carries a browser token AND our own identifier, deliberately.
    #
    # ASP.NET does browser-capability detection. Handed an unrecognised
    # user-agent it serves a "downlevel" page that omits the script
    # defining __doPostBack -- so the BALAM pager links render, and do
    # nothing. Measured on the live portal: identical URL, `undefined`
    # under the bare bot string, `function` under a browser one. The
    # agent was reading page one nine times and reporting nine pages.
    #
    # This is not a disguise. The name and contact URL stay in the
    # string, so the operator can still see who we are and reach us in
    # their logs; what the browser token buys is a page whose pagination
    # functions at all. robots.txt permits this path either way -- the
    # probe re-checks it before every run.
    scrape_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 TendersFinderBot/0.1 (+https://tendersfinder.example/bot)"
    )
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
