"""Canvas HTML -> clean Markdown / plain-text excerpts."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify

_HIDDEN_RE = re.compile(r"display\s*:\s*none", re.I)


def _clean_soup(html: str, base_url: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    for tag in soup.find_all(style=_HIDDEN_RE):
        tag.decompose()
    for tag in soup.find_all(href=True):
        tag["href"] = _absolute(tag["href"], base_url)
    for tag in soup.find_all(src=True):
        tag["src"] = _absolute(tag["src"], base_url)
    return soup


def _absolute(url: str, base_url: str) -> str:
    url = urljoin(base_url + "/", url.strip())
    # Canvas file links open a preview wrapper; the plain file URL is more useful
    return re.sub(r"[?&]wrap=1$", "", url)


def html_to_md(html: str | None, base_url: str) -> str | None:
    if not html or not html.strip():
        return None
    soup = _clean_soup(html, base_url)
    md = markdownify(str(soup), heading_style="ATX", bullets="-", strip=["span"])
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md or None


def excerpt(text: str | None, limit: int = 240) -> str:
    """Plain-text excerpt of Markdown/HTML-ish text for compact notifications."""
    if not text:
        return ""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)      # links -> link text
    text = re.sub(r"[*_`#>]+", "", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"
