import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.config import settings

_data_dir = Path(os.path.normpath(settings.data_dir))

NAV_KEYWORDS = [
    "home", "about", "contact", "faq", "privacy", "terms", "sitemap",
    "sign in", "login", "register", "search", "blog", "career", "help",
    "cookie", "accessibility", "feedback", "report", "advertise",
    "facebook", "twitter", "instagram", "youtube", "linkedin", "tiktok",
    "calendar?action", "calendar/render", "outlook", "yahoo",
    "newsletter", "subscribe", "book now", "buy ticket", "get ticket",
    "get recommendation", "find out more", "submit your",
    "government agency", "how to identify", "trusted websites",
    "scamshield", "all facilities", "facilities closure",
    "learn a sport", "things to do", "what's happening",
    "related sites", "need help", "connect with us",
    "report vulnerability", "last updated",
    "know more", "read more", "learn more", "view details",
    "load more", "show more", "see more",
]


# Additional href keywords that indicate a link is navigation (not event content).
_NAV_HREF_KEYWORDS = [
    "/about", "/career", "/contact", "/faq", "/privacy", "/terms",
    "/sitemap", "/login", "/register", "/search", "/blog", "/help",
    "/newsroom", "/sustainability", "/press", "/media", "/lease",
    "/investment", "/development",
    "facebook.com", "twitter.com", "instagram.com", "youtube.com", "linkedin.com",
]


# Filter out navigation, social, calendar links; only keep links with >=15 chars of text
# or links that contain an image (event card tiles).
def _is_event_detail_link(href: str, text: str, *, has_image: bool = False) -> bool:
    text = text.strip()
    if href.startswith("#") or href.startswith("javascript:"):
        return False
    if any(kw in href.lower() for kw in ["calendar", "outlook", "yahoo.com"]):
        return False
    # For image-based links (event cards), apply href nav checks but skip text-length gate
    if has_image:
        if any(kw in href.lower() for kw in _NAV_HREF_KEYWORDS):
            return False
        return True
    if len(text) < 15:
        return False
    if any(kw in text.lower() for kw in NAV_KEYWORDS):
        return False
    return True


# Resolve a potentially relative href to an absolute URL using the base URL.
def _normalize_url(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(base, href)


# Locate an element by CSS selector, scroll it into view, and click it. Returns True if clicked.
def _try_click(page, selector: str, description: str, timeout: int = 5000) -> bool:
    try:
        el = page.query_selector(selector)
        if not el:
            return False
        # Try normal click with actionability checks
        if el.is_visible() and el.bounding_box():
            el.scroll_into_view_if_needed(timeout=2000)
            el.click(timeout=timeout)
            if description not in ("cookie accept", "popup close", "filter expand"):
                _log(f"Clicked {description}")
            return True
        # Fallback: force click via JavaScript (bypasses overlay/visibility issues)
        el.evaluate("e => e.click()")
        if description not in ("cookie accept", "popup close", "filter expand"):
            _log(f"Force-clicked {description} (JS)")
        return True
    except Exception as e:
        if description not in ("cookie accept", "popup close", "filter expand"):
            _log(f"Failed to click {description}: {e}")
    return False


# Aggressively dismiss cookie-consent banners, popups, and overlay dialogs.
def _dismiss_overlays(page) -> None:
    # OneTrust / common cookie frameworks
    for sel in [
        "#onetrust-accept-btn-handler",
        ".accept-all-cookies",
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "button:has-text('ACCEPT ALL')",
        "button:has-text('Accept')",
        "button:has-text('ACCEPT')",
        "button:has-text('Got it')",
        "button:has-text('Got It')",
        "button:has-text('I Agree')",
        "button:has-text('I agree')",
        "button:has-text('Allow')",
        "button:has-text('Continue')",
        ".cookie-accept",
        ".consent-accept",
        "[aria-label*='cookie'] button",
        "[aria-label*='consent'] button",
        "#cookiescript_accept",
        "#cookiescript_continue",
    ]:
        _try_click(page, sel, "cookie accept", timeout=2000)

    page.wait_for_timeout(500)

    # Modal / popup close buttons
    for sel in [
        "button:has-text('Close')",
        "button.close",
        ".modal .close",
        ".modal-close",
        ".popup-close",
        "[aria-label='Close']",
        ".overlay-close",
    ]:
        _try_click(page, sel, "popup close", timeout=1000)

    page.wait_for_timeout(300)


# Try to reveal hidden event content by expanding collapsed month/date filters.
def _expand_filters(page) -> None:
    # "Show Past Events" on ActiveSG (span or button)
    for sel in [
        "span.show-past-events",
        "button:has-text('Show Past Events')",
        "a:has-text('Show Past Events')",
        "[aria-label*='past']",
        "button:has-text('Past')",
    ]:
        _try_click(page, sel, "Show Past Events", timeout=3000)

    page.wait_for_timeout(500)

    # Expand any collapsed month/filter panels
    for sel in [
        "button[aria-expanded='false']:has-text('Month')",
        "button[aria-expanded='false']:has-text('Date')",
        "[class*=togglePanel] button[aria-expanded='false']",
        "[class*=filter] button[aria-expanded='false']",
    ]:
        _try_click(page, sel, "filter expand", timeout=2000)


# Try clicking the next unvisited page in numbered pagination (e.g. NParks .card-listing-pagination).
# Returns True if a new page was clicked.
def _try_numbered_pagination(page, visited_pages: set[int]) -> bool:
    pag = page.query_selector("[class*=pagination]")
    if not pag:
        return False
    active = pag.query_selector(".active, [aria-current]")
    if not active:
        return False
    cur = active.get_attribute("data-pagerindex")
    if not cur or not cur.isdigit():
        return False
    visited_pages.add(int(cur))
    for item in pag.query_selector_all("[data-pagerindex]"):
        idx = item.get_attribute("data-pagerindex")
        if not (idx and idx.isdigit() and int(idx) not in visited_pages):
            continue
        cls = item.get_attribute("class") or ""
        if "disabled" in cls:
            continue
        try:
            link = item.query_selector("a.page-link, a")
            if not link:
                continue
            link.scroll_into_view_if_needed(timeout=2000)
            link.click(timeout=5000)
            # Wait for pagination to reflect the new active page after AJAX navigation
            expected = int(idx)
            try:
                page.wait_for_function(
                    f"() => {{ const p = document.querySelector('[class*=pagination]'); if (!p) return false; const a = p.querySelector('.active, [aria-current]'); return a && a.getAttribute('data-pagerindex') === '{expected}'; }}",
                    timeout=5000,
                )
            except Exception:
                pass
            return True
        except Exception:
            continue
    return False


# Click each month filter chip (Jan-Dec) on the page. Collect both event containers and links
# from each filtered view. Re-queries the month chip before each click to avoid stale handles.
def _accumulate_all_months(page, base_url: str) -> tuple[list[tuple[str, str]], dict[str, str]]:
    all_groups: list[tuple[str, str]] = []
    all_urls: dict[str, str] = {}

    for m_name in ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"):
        try:
            # Re-query the month chip each iteration (page may have navigated, invalidating old handles)
            target = None
            for el in page.query_selector_all("span.month"):
                if el.text_content().strip() == m_name:
                    target = el
                    break
            if not target:
                continue

            target.click(timeout=3000)
            page.wait_for_timeout(1500)

            # Skip extraction if no events loaded for this month
            if not page.query_selector_all("#mAll > .outerDiv"):
                continue

            groups = _extract_event_containers(page, base_url)
            all_groups.extend(groups)
            _extract_links(page, base_url, all_urls)
        except Exception:
            pass

    if all_groups:
        _log(f"Extracted {len(all_groups)} event groups from months")
    return all_groups, all_urls


# Scope link extraction to the main content container when possible, avoiding nav/footer/header bloat.
_CONTENT_CONTAINER_SELS = [
    ".listingcontainer",
    "[class*=listing-container]",
    "main",
    "article",
    "[role=main]",
    "[class*=event-listing]",
    "[class*=eventList]",
]


def _find_content_container(page):
    for sel in _CONTENT_CONTAINER_SELS:
        el = page.query_selector(sel)
        if el:
            return el
    return page


# Extract event detail links from the current page into the provided dict.
def _extract_links(page, base_url: str, url_dict: dict[str, str]) -> None:
    root = _find_content_container(page)
    # Process image links (event cards) — use :has() to avoid per-element evaluate calls
    for l in root.query_selector_all("a[href]:has(img)"):
        try:
            href = l.get_attribute("href") or ""
            text = l.inner_text().strip()
        except Exception:
            continue
        if _is_event_detail_link(href, text, has_image=True):
            full_url = _normalize_url(base_url, href)
            if full_url not in url_dict:
                url_dict[full_url] = text
    # Process text-only links
    for l in root.query_selector_all("a[href]:not(:has(img))"):
        try:
            href = l.get_attribute("href") or ""
            text = l.inner_text().strip()
        except Exception:
            continue
        if _is_event_detail_link(href, text):
            full_url = _normalize_url(base_url, href)
            if full_url not in url_dict:
                url_dict[full_url] = text


# Print a [scraper] prefixed message to stdout; silently ignore any I/O errors.
def _log(msg: str) -> None:
    try:
        print(f"[scraper] {msg}", flush=True)
    except Exception:
        pass


# Get body text including hidden content (e.g. "Free" badges), excluding script/style/noise.
_CONTENT_TAGS = ["div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6"]
_NOISE_TAGS = [
    "script", "style", "noscript",
    "button", "[role='button']", "input[type='submit']",
    "nav", "header", "footer", "aside",
    "i", "svg",
    "[class*=icon]", "[class*=fa-]", "[class*=material-icon]",
]


def _get_content_text(soup: BeautifulSoup) -> str:
    """Extract text only from <div>, <span>, <p>, <h1>-<h6> content elements."""
    for sel in _NOISE_TAGS:
        for el in soup.select(sel):
            el.decompose()
    # Remove only simple inline <a> tags (no block-level children) — keep event card links
    _BLOCK_TAGS = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "section", "article", "figure", "blockquote"}
    for a in soup.find_all("a"):
        has_block = any(c.name in _BLOCK_TAGS for c in a.find_all(recursive=False))
        if not has_block:
            a.decompose()
    for li in soup.find_all("li"):
        li.unwrap()
    parts = []
    seen = {}
    for el in soup.find_all(_CONTENT_TAGS):
        text = el.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if _is_nav_group(text):
            continue
        key = _norm(text)
        is_subset = False
        for s_key, s_text in list(seen.items()):
            if key == s_key or key in s_key:
                is_subset = True
                break
            if s_key in key:
                parts.remove(s_text)
                del seen[s_key]
        if is_subset:
            continue
        seen[key] = text
        parts.append(text)
    return "\n".join(parts)


def _get_body_text(page) -> str:
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    return _get_content_text(soup)


def _content_text_of(element) -> str:
    """Extract text from div/span/p/h1-h6 only within a single element."""
    soup = BeautifulSoup(str(element), "html.parser")
    return _get_content_text(soup)


# Collapse whitespace for duplicate detection.
def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


# Build a DOM hierarchy path for an element (e.g. "div#root > div.content > section.event-card").
def _element_dom_path(el, max_depth: int = 6) -> str:
    parts: list[str] = []
    current = el
    while current is not None and getattr(current, 'name', None) not in ('[document]', None) and len(parts) < max_depth:
        tag = current.name or 'div'
        seg = tag
        if current.get('id'):
            seg = f"{tag}#{current['id']}"
        else:
            classes = current.get('class', [])
            if classes:
                seg = f"{tag}.{'.'.join(classes)}"
        parts.append(seg)
        current = current.parent
    return " > ".join(reversed(parts))


# Check whether a text block looks like navigation / boilerplate rather than event content.
def _is_nav_group(text: str) -> bool:
    low = text.lower()
    hits = sum(1 for kw in NAV_KEYWORDS if kw in low)
    # If 2+ nav keywords appear, or nav keywords account for > 5% of the group,
    # it's probably not an event card.
    if hits >= 3:
        return True
    # Check for event-like signals — if none are present, likely nav.
    has_date = bool(re.search(r"\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", low))
    has_price = bool(re.search(r"(free|\$|ticket|price|from\s+\$)", low))
    has_event_action = bool(re.search(r"(view details|book now|find out more|get ticket)", low))
    if hits >= 1 and not (has_date or has_price or has_event_action):
        return True
    return False


# Walk up from an <a> tag to find the enclosing event container element.
def _find_event_container(soup_a) -> object | None:
    """Walk up ~3 levels from the link to find the full event card (date + name + desc + price)."""
    link_text = soup_a.get_text(strip=True)

    # Collect all candidates at depths 1-5; pick the one with the most text (up to 3000 chars)
    parent = soup_a.parent
    best = None
    best_size = 0
    for depth in range(8):
        if not parent or parent.name in ("html", "body", "[document]"):
            break
        ptext = parent.get_text(strip=True)
        extra = len(ptext) - len(link_text)
        if extra >= 80 and len(ptext) <= 3000 and len(ptext) > best_size:
            best = parent
            best_size = len(ptext)
        parent = parent.parent

    if best:
        return best

    # Fallback: first ancestor with > 100 chars
    parent = soup_a.parent
    for _ in range(8):
        if not parent or parent.name in ("html", "body", "[document]"):
            break
        if len(parent.get_text(strip=True)) > 100:
            return parent
        parent = parent.parent
    return None


# Find event container elements in the current page DOM and return one text block per event.
_EVENT_CONTAINER_SELECTORS = [
    "a.eventCard",
    "div.cal_item-row",
    "article",
    "[class*=event-card]",
    "[class*=eventCard]",
    "[class*=event-item]",
    "[class*=eventItem]",
    "[class*=listing-item]",
    "section[class*=event]",
    ".outerDiv",
    "a.cal-wrap",
    ".item-inner",
    ".cmp-eventlist__item",
    "[class*=cmp-eventList]",
    ".cmp-listing-card",
    ".cmp-listing-card--event",
    ".cmp-listing-card__link",
]


def _extract_event_containers(page, base_url: str) -> list[tuple[str, str]]:
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1 — known CSS selectors
    for sel in _EVENT_CONTAINER_SELECTORS:
        try:
            els = soup.select(sel)
            groups: list[tuple[str, str]] = []
            for el in els:
                text = _content_text_of(el)
                if len(text) > 50:
                    label = _element_dom_path(el)
                    groups.append((label, text))
            if len(groups) >= 2:
                _log(f"Found {len(groups)} event groups via selector: {sel}")
                return groups
        except Exception:
            continue

    # Strategy 2 — walk up from each event detail link
    seen_outer: set[str] = set()
    groups: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        atext = a.get_text(strip=True)
        has_image = a.find("img") is not None
        if not _is_event_detail_link(href, atext, has_image=has_image):
            continue
        container = _find_event_container(a)
        if container:
            outer = str(container)
            if outer not in seen_outer:
                seen_outer.add(outer)
                text = _content_text_of(container)
                if len(text) > 100 and not _is_nav_group(text):
                    label = _element_dom_path(container)
                    groups.append((label, text))
    if len(groups) >= 2:
        _log(f"Found {len(groups)} event groups via link-ancestor walk")
        return groups

    # Fallback — entire body as one group
    body = _content_text_of(soup)
    return [("body", body)] if body and not _is_nav_group(body) else []


# Open the listing URL in Playwright, paginate through all pages (clicking "Next"/"Load More"), collect every
# event-detail link and the full body text of each page. Returns (combined_listing_text, list_of_event_urls).
def _collect_event_urls_and_listing(url: str) -> tuple[str, list[tuple[str, str]]]:
    from playwright.sync_api import sync_playwright

    _log("Starting Playwright browser...")
    all_urls: dict[str, str] = {}
    all_event_groups: list[tuple[str, str]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        _log(f"Navigating to {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _log("Page loaded, waiting 1.5s...")
        page.wait_for_timeout(1500)

        _dismiss_overlays(page)
        _expand_filters(page)

        start_time = __import__("time").time()
        pagination_exhausted = False
        visited_pages: set[int] = set()

        for iteration in range(20):
            elapsed = __import__("time").time() - start_time
            if elapsed > 70:
                _log(f"Time budget exceeded ({elapsed:.0f}s), stopping early")
                break

            _log(f"Iteration {iteration}")
            clicked = False

            if iteration == 0:
                groups = _extract_event_containers(page, url)
                all_event_groups.extend(groups)
                _extract_links(page, url, all_urls)
                month_groups, month_urls = _accumulate_all_months(page, url)
                all_event_groups.extend(month_groups)
                for k, v in month_urls.items():
                    if k not in all_urls:
                        all_urls[k] = v

            if iteration > 0:
                height_before = page.evaluate("document.body.scrollHeight")
                next_el = page.query_selector('[aria-label="Next page"]')
                next_enabled = next_el is not None and next_el.is_enabled()

                if next_enabled and not pagination_exhausted:
                    if _try_click(page, '[aria-label="Next page"]', "Next page", timeout=2000):
                        page.wait_for_timeout(1000)
                        height_after = page.evaluate("document.body.scrollHeight")
                        if height_after > height_before:
                            clicked = True
                        else:
                            _log("Next page click did not change content")
                            pagination_exhausted = True

                if not clicked:
                    for load_more_sel in (
                        ".cmp-listing-container__cta a",
                        ".cmp-cta__text",
                        "[class*=cta] a:has-text('Load More')",
                        "a:has-text('Load More')",
                        "a:has-text('Show More')",
                        "a:has-text('View More')",
                        "a:has-text('See More')",
                        "button:has-text('Load More')",
                        "button:has-text('Show More')",
                        "button:has-text('View More')",
                        "button:has-text('See More')",
                        ".cmp-button:has-text('Load More')",
                    ):
                        if _try_click(page, load_more_sel, "Load More", timeout=3000):
                            page.wait_for_timeout(1000)
                            page.wait_for_timeout(2000)
                            if page.evaluate("document.body.scrollHeight") > height_before:
                                clicked = True
                            break

                if not clicked:
                    clicked = _try_numbered_pagination(page, visited_pages)
                    if clicked:
                        page.wait_for_timeout(500)

                if clicked:
                    groups2 = _extract_event_containers(page, url)
                    all_event_groups.extend(groups2)
                    _extract_links(page, url, all_urls)
                else:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(800)
                    if page.evaluate("document.body.scrollHeight") <= height_before:
                        _log(f"No new content, stopping at iteration {iteration}")
                        break

        _log(f"Closed browser, found {len(all_urls)} event URLs")
        browser.close()

    # Deduplicate at the group level preferring larger text over smaller fragments
    seen: dict[str, tuple[str, str]] = {}
    unique_groups: list[tuple[str, str]] = []
    for label, text in all_event_groups:
        key = _norm(text)
        is_subset = False
        for s_key, (s_label, s_text) in list(seen.items()):
            if key == s_key or key in s_key:
                is_subset = True
                break
            if s_key in key:
                unique_groups.remove((s_label, s_text))
                del seen[s_key]
        if is_subset:
            continue
        seen[key] = (label, text)
        unique_groups.append((label, text))

    parts = [f"[{label}]\n{text}" for label, text in unique_groups]
    return "\n\n".join(parts), list(all_urls.items())


# Fetch a single URL — first via httpx/BeautifulSoup; if that yields <200 chars, fall back to Playwright.
# Returns (text, label) where label is the CSS class/id of the main content container.
def _extract_text_from_url(target_url: str) -> tuple[str, str]:
    def _parse(html: str) -> tuple[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        label = "body"
        content_el = soup
        for sel in ["main", "article", "[role=main]", "[class*=content]", "[class*=detail]"]:
            el = soup.select_one(sel)
            if el:
                label = _element_dom_path(el)
                content_el = el
                break
        for sel in _NOISE_TAGS:
            for el in content_el.select(sel):
                el.decompose()
        text = content_el.get_text(separator="\n", strip=True)
        lines = [l for l in text.split('\n') if len(l.strip()) > 2]
        text = '\n'.join(lines)
        return text, label

    try:
        resp = httpx.get(
            target_url,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
        text, label = _parse(resp.text)
        if len(text) > 200:
            return text, label
    except Exception:
        pass

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            text, label = _parse(page.content())
            browser.close()
        return text, label
    except Exception:
        return "", "body"


# Orchestrate scraping: run _collect_event_urls_and_listing in a thread, then scrape every found event URL
# concurrently. Falls back to a simple GET+BeautifulSoup if no event links are found.
async def fetch_and_extract_text(client: httpx.AsyncClient, url: str) -> str:
    listing_text, url_anchor_pairs = await asyncio.to_thread(
        _collect_event_urls_and_listing, url
    )

    if url_anchor_pairs:
        detail_texts = await asyncio.gather(
            *[asyncio.to_thread(_extract_text_from_url, u) for u, _ in url_anchor_pairs],
            return_exceptions=True,
        )
        listing_norm = _norm(listing_text)
        event_sections: list[str] = []
        seen: dict[str, bool] = {}
        for (url, anchor), dt in zip(url_anchor_pairs, detail_texts):
            if isinstance(dt, tuple) and len(dt) == 2:
                text, label = dt
                if text:
                    key = _norm(text)
                    if key not in seen and key[:200] not in listing_norm:
                        seen[key] = True
                        event_sections.append(f"[{label}]\n{text}")

        parts = [listing_text]
        if event_sections:
            parts.append("\n\n".join(event_sections))
        return "\n\n".join(parts)

    response = await client.get(url, follow_redirects=True, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for sel in _NOISE_TAGS:
        for el in soup.select(sel):
            el.decompose()
    return soup.get_text(separator=" ", strip=True)


# Convert a URL to a safe filesystem name by replacing non-alphanumeric characters with underscores.
def _slugify(url: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", url).strip("_")[:100]


# Top-level entry point: scrape a URL, save the extracted text to a local file, return (text, filepath, filename).
async def scrape_website(client: httpx.AsyncClient, url: str) -> tuple[str, str, str]:
    text = await fetch_and_extract_text(client, url)
    os.makedirs(settings.data_dir, exist_ok=True)
    filename = f"{_slugify(url)}.txt"
    filepath = _data_dir / filename
    filepath.write_text(text, encoding="utf-8")
    return text, str(filepath), filename
