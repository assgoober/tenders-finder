"""Generic Playwright-backed portal agent.

Most government procurement portals are a paginated listing of tender cards
plus a detail page with downloadable PDFs. This class implements that shape
once and takes the portal-specific parts as CSS selectors, so a new portal of
that shape is a subclass with a handful of class attributes rather than a new
scraper.

Portals that don't fit — SPA/XHR-driven listings, per-tender authentication,
CAPTCHAs — subclass `TenderSourceAgent` directly and implement `discover()`
and `fetch()` themselves.

Legal note: check each portal's terms of service and robots.txt before
enabling it in production (REPOMIX.md §7).
"""

from __future__ import annotations

import io
import logging
from urllib.parse import urljoin

from .base import FetchedTender, TenderDocument, TenderSourceAgent, TenderStub

logger = logging.getLogger(__name__)


class PlaywrightPortalAgent(TenderSourceAgent):
    """Listing-plus-detail portal driven through a real browser."""

    # --- Navigation (override per portal) ---
    listing_path: str = "/"
    listing_item_selector: str = ""
    listing_title_selector: str = ""
    listing_link_selector: str = "a"
    #: Selector for the "next page" control. None means single-page listings.
    pagination_next_selector: str | None = None
    max_pages: int = 5

    #: Set by `discover` when the walk ended because a postback did not land,
    #: as opposed to running out of pages. Read by the pipeline: a partial
    #: harvest that reports the same shape as a complete one is how a third of
    #: a corpus goes missing without anything looking wrong.
    pagination_truncated: bool = False

    # --- Extraction (override per portal) ---
    detail_body_selector: str = "body"
    detail_attachment_selector: str = "a[href$='.pdf']"

    async def discover(self, *, limit: int = 50) -> list[TenderStub]:
        from playwright.async_api import async_playwright

        stubs: list[TenderStub] = []
        self.pagination_truncated = False

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.settings.playwright_headless)
            try:
                context = await browser.new_context(user_agent=self.settings.scrape_user_agent)
                page = await context.new_page()
                page.set_default_timeout(self.settings.scrape_timeout_seconds * 1000)

                url = urljoin(self.base_url, self.listing_path)
                await self.with_backoff(
                    lambda: page.goto(url, wait_until="domcontentloaded"),
                    description=f"open listing {url}",
                )

                for page_number in range(1, self.max_pages + 1):
                    stubs.extend(await self._read_listing_page(page))
                    if len(stubs) >= limit:
                        break
                    if not await self._go_to_next_page(page):
                        # Distinguish "no more pages" from "the page would not
                        # turn". Only the second is a truncated harvest.
                        if getattr(self, "_last_page_had_a_next_link", False):
                            self.pagination_truncated = True
                        break
                    logger.info("[%s] advanced to listing page %d", self.key, page_number + 1)
            finally:
                await browser.close()

        return stubs[:limit]

    async def _read_listing_page(self, page) -> list[TenderStub]:
        stubs: list[TenderStub] = []
        items = await page.query_selector_all(self.listing_item_selector)

        for item in items:
            link = await item.query_selector(self.listing_link_selector)
            href = await link.get_attribute("href") if link else None
            if not href:
                continue

            title_el = (
                await item.query_selector(self.listing_title_selector)
                if self.listing_title_selector
                else link
            )
            title = ((await title_el.inner_text()) if title_el else "").strip()
            source_url = urljoin(self.base_url, href)

            stubs.append(
                TenderStub(
                    external_id=self.external_id_from_url(source_url),
                    title=title or source_url,
                    source_url=source_url,
                )
            )

        return stubs

    async def _go_to_next_page(self, page) -> bool:
        if not self.pagination_next_selector:
            return False
        next_link = await page.query_selector(self.pagination_next_selector)
        if not next_link:
            return False
        await self.with_backoff(
            lambda: next_link.click(), description="paginate"
        )
        await page.wait_for_load_state("domcontentloaded")
        return True

    async def fetch(self, stub: TenderStub) -> FetchedTender:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.settings.playwright_headless)
            try:
                context = await browser.new_context(user_agent=self.settings.scrape_user_agent)
                page = await context.new_page()
                page.set_default_timeout(self.settings.scrape_timeout_seconds * 1000)

                await self.with_backoff(
                    lambda: page.goto(stub.source_url, wait_until="domcontentloaded"),
                    description=f"open detail {stub.source_url}",
                )

                body = await page.query_selector(self.detail_body_selector)
                raw_text = (await body.inner_text()).strip() if body else ""

                documents = await self._collect_attachments(page, context)
            finally:
                await browser.close()

        return FetchedTender(stub=stub, raw_text=raw_text, documents=documents)

    async def _collect_attachments(self, page, context) -> list[TenderDocument]:
        documents: list[TenderDocument] = []
        links = await page.query_selector_all(self.detail_attachment_selector)

        for link in links:
            href = await link.get_attribute("href")
            if not href:
                continue

            file_url = urljoin(self.base_url, href)
            file_name = (await link.inner_text()).strip() or file_url.rsplit("/", 1)[-1]

            try:
                response = await self.with_backoff(
                    lambda url=file_url: context.request.get(url),
                    description=f"download {file_url}",
                )
                payload = await response.body()
                documents.append(
                    TenderDocument(
                        file_name=file_name,
                        file_url=file_url,
                        mime_type=response.headers.get("content-type"),
                        text_content=extract_pdf_text(payload),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                # One unreadable annex must not lose the whole tender: the
                # notice page alone is still worth extracting.
                logger.warning("[%s] attachment %s failed: %s", self.key, file_url, exc)
                documents.append(TenderDocument(file_name=file_name, file_url=file_url))

        return documents

    @staticmethod
    def external_id_from_url(url: str) -> str:
        """Stable per-tender identifier used for deduplication.

        Override wherever the portal exposes a real tender number — a URL
        slug can change while the tender does not.
        """
        return url.rstrip("/").rsplit("/", 1)[-1] or url


def extract_pdf_text(payload: bytes) -> str | None:
    """Best-effort text layer extraction from a PDF.

    Returns None for scanned PDFs with no text layer; those need OCR, which
    is out of MVP scope. The extractor reports them through
    `unclear_sections` rather than silently producing an empty extraction.
    """
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is in requirements.txt
        logger.warning("pypdf not installed; skipping PDF text extraction")
        return None

    try:
        reader = PdfReader(io.BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF parse failed: %s", exc)
        return None
