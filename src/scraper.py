from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
import urllib.request
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import cloudscraper
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


_LISTING_ID_RE = re.compile(r"(?:^|/)\s*(?P<id>\d{6,})\s*(?:$|[/?#])")
_PRICE_RE = re.compile(r"(?P<price>\d[\d\s]{1,})\s*Ft\s*/\s*h[óo]", re.IGNORECASE)
_AREA_RE = re.compile(r"Alapterület\s+(?P<area>\d+(?:[\.,]\d+)?)\s*m\s*(?:2|²)", re.IGNORECASE)
_ROOMS_RE = re.compile(r"Szob[áa]k?\s+(?P<rooms>[0-9\s\+\-fél]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RawListing:
    listing_id: str
    url: str
    title: str | None
    location_text: str | None
    price_text: str | None
    area_text: str | None
    rooms_text: str | None
    raw_text: str | None
    source_url: str
    scraped_at: dt.datetime


class IngatlanScraper:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        timeout_s: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._client = cloudscraper.create_scraper()
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._headers = dict(DEFAULT_HEADERS)
        if headers:
            self._headers.update(headers)

    def fetch_html(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                logger.info("GET %s (attempt %s/%s)", url, attempt, self._max_retries)
                response = self._client.get(url, headers=self._headers, timeout=self._timeout_s)
                if response.status_code != 200:
                    if response.status_code == 403:
                        return self._fetch_html_fancy(url)
                    raise RuntimeError(f"HTTP {response.status_code} for {url}")
                return response.text
            except Exception as exc:
                last_error = exc
                logger.warning("Request failed: %s", exc)
                if attempt < self._max_retries:
                    time.sleep(1.5 * attempt)

        raise RuntimeError(f"Failed to fetch {url}") from last_error

    # ha a cloud scraper nem jut at:
    def _fetch_html_fancy(self, url: str) -> str:
        opener = urllib.request.build_opener()
        opener.addheaders = [("User-Agent", DEFAULT_USER_AGENT)]
        with opener.open(url, timeout=self._timeout_s) as response:
            web_bytes = response.read()
        return web_bytes.decode("utf-8", errors="replace")

    def fetch_soup(self, url: str) -> BeautifulSoup:
        html = self.fetch_html(url)
        return BeautifulSoup(html, "html.parser")

    def fetch_listings(
        self,
        url: str,
        *,
        pages: int = 1,
        delay_s: float = 1.0,
        max_listings: int | None = None,
    ) -> list[RawListing]:
        all_listings: dict[str, RawListing] = {}
        current_url = url

        completed_pages = 0

        for page_index in range(1, max(1, pages) + 1):
            page_url = current_url if page_index == 1 else _with_query_param(url, "page", str(page_index))
            try:
                soup = self.fetch_soup(page_url)
                listings = parse_listings(soup, source_url=page_url)
                logger.info("Parsed %s listings from page %s", len(listings), page_index)
                completed_pages = page_index
            except Exception as exc:
                logger.warning("Stopping early at page %s due to fetch/parse error: %s", page_index, exc)
                break

            for listing in listings:
                all_listings.setdefault(listing.listing_id, listing)
                if max_listings and len(all_listings) >= max_listings:
                    break

            if max_listings and len(all_listings) >= max_listings:
                break

            if delay_s and page_index < pages:
                time.sleep(delay_s)

        if completed_pages and completed_pages < pages:
            logger.info("Fetched %s/%s pages (%s listings)", completed_pages, pages, len(all_listings))

        return list(all_listings.values())


def parse_listings(soup: BeautifulSoup, *, source_url: str) -> list[RawListing]:
    scraped_at = dt.datetime.now(dt.timezone.utc)
    base_url = "https://ingatlan.com"

    listings: dict[str, RawListing] = {}

    for url_ in _extract_urls_from_json_ld(soup):
        listing_id = _extract_listing_id(url_)
        if not listing_id:
            continue
        full_url = _normalize_listing_url(url_, base_url=base_url)
        listings.setdefault(
            listing_id,
            RawListing(
                listing_id=listing_id,
                url=full_url,
                title=None,
                location_text=None,
                price_text=None,
                area_text=None,
                rooms_text=None,
                raw_text=None,
                source_url=source_url,
                scraped_at=scraped_at,
            ),
        )

    for a in soup.find_all("a", href=True):
        href = str(a.get("href", "")).strip()
        listing_id = _extract_listing_id(href)
        if not listing_id:
            continue

        text = a.get_text(" ", strip=True)
        if text and ("Ft" not in text) and ("Alapterület" not in text):
            continue

        full_url = _normalize_listing_url(href, base_url=base_url)
        title, location_text, price_text, area_text, rooms_text = _extract_fields_from_text(text)

        listings[listing_id] = RawListing(
            listing_id=listing_id,
            url=full_url,
            title=title,
            location_text=location_text,
            price_text=price_text,
            area_text=area_text,
            rooms_text=rooms_text,
            raw_text=text or None,
            source_url=source_url,
            scraped_at=scraped_at,
        )

    return list(listings.values())


def _extract_listing_id(href_or_url: str) -> str | None:
    if not href_or_url:
        return None
    match = _LISTING_ID_RE.search(href_or_url)
    if not match:
        return None
    return match.group("id")


def _normalize_listing_url(href_or_url: str, *, base_url: str) -> str:
    if href_or_url.startswith("http://") or href_or_url.startswith("https://"):
        return href_or_url
    return urljoin(base_url, href_or_url)


def _extract_fields_from_text(text: str) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    if not text:
        return None, None, None, None, None

    normalized = re.sub(r"\s+", " ", text).strip()

    price_text: str | None = None
    m_price = _PRICE_RE.search(normalized)
    if m_price:
        price_text = f"{m_price.group('price').strip()} Ft/hó"

    area_text: str | None = None
    m_area = _AREA_RE.search(normalized)
    if m_area:
        area_text = f"{m_area.group('area').strip()} m2"

    rooms_text: str | None = None
    m_rooms = _ROOMS_RE.search(normalized)
    if m_rooms:
        rooms_text = m_rooms.group("rooms").strip()

    location_text: str | None = None
    if m_price:
        start = m_price.end()
        end = normalized.lower().find("alapterület", start)
        if end == -1:
            end = normalized.lower().find("szob", start)
        if end != -1:
            loc = normalized[start:end].strip(" -|")
            loc = re.sub(r"\s+", " ", loc).strip()
            location_text = loc or None

    title = location_text or (normalized[:80] if normalized else None)

    return title, location_text, price_text, area_text, rooms_text


def _extract_urls_from_json_ld(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            raw = script.string
            if not raw:
                continue
            data = _safe_json_load(raw)
            urls.extend(_walk_for_urls(data))
        except Exception:
            continue
    return urls


def _walk_for_urls(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() == "url" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_walk_for_urls(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_for_urls(item))
    return found


def _safe_json_load(text: str) -> Any:
    import json

    text = text.strip()
    return json.loads(text)


def _with_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query[key] = [value]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))
