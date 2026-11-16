"""Web research tools: search (Brave API or self-hosted SearXNG) and safe page fetching.

`fetch_url` resolves the host once, rejects any non-public address, then connects to that
exact IP while sending the original host name for TLS (SNI + certificate check) and the
Host header. This closes the DNS-rebinding window between check and connect. Redirects are
followed manually, and every hop is checked again.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx

from .tools import Tool, params

if TYPE_CHECKING:
    from .tools import ToolRegistry

MAX_DOWNLOAD = 3_000_000
MAX_TEXT = 40_000
MAX_REDIRECTS = 5
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml",
              "application/rss+xml", "application/atom+xml", "application/ld+json")
Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "head"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
             "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
             "hr", "dd", "dt", "nav", "main", "aside", "figure", "figcaption"}
    LINE = {"li", "br", "tr", "dd", "dt"}  # Line break before, no paragraph gap after.

    def __init__(self, base: httpx.URL):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.title: list[str] = []
        self.links: list[dict[str, str]] = []
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li" and not self._skip:
            self.parts.append("• ")
        if tag == "a" and not self._skip:
            href = dict(attrs).get("href")
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                try:
                    self._href = str(self.base.join(href))
                except Exception:
                    self._href = None
                self._anchor = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK and tag not in self.LINE:
            self.parts.append("\n")
        if tag == "a" and self._href:
            label = " ".join("".join(self._anchor).split())
            if label and len(self.links) < 60 and self._href.startswith("http"):
                self.links.append({"text": label[:120], "url": self._href})
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        elif not self._skip:
            self.parts.append(data)
            if self._href:
                self._anchor.append(data)


def html_to_text(markup: str, base: httpx.URL) -> tuple[str, str, list[dict[str, str]]]:
    parser = _TextExtractor(base)
    parser.feed(markup)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text, blank = [], False
    for line in lines:
        if line:
            text.append(line)
            blank = False
        elif not blank and text:
            text.append("")
            blank = True
    return " ".join("".join(parser.title).split()), "\n".join(text).strip(), parser.links


def register_web_tools(registry: ToolRegistry, resolver: Resolver | None = None,
                       transport: httpx.AsyncBaseTransport | None = None) -> None:
    resolve = resolver or system_resolver

    async def public_address(host: str, port: int) -> str:
        try:
            addresses = await resolve(host, port)
        except OSError:
            raise ValueError(f"Host nicht gefunden: {host}") from None
        if not addresses:
            raise ValueError(f"Host nicht gefunden: {host}")
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%")[0])
            if not ip.is_global or ip.is_multicast:
                raise ValueError("Private, lokale oder reservierte Adressen sind nicht erlaubt.")
        return addresses[0].split("%")[0]

    async def fetch_url(args: dict) -> Any:
        url = httpx.URL(args["url"].strip())
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10),
                                     follow_redirects=False, trust_env=False,
                                     transport=transport) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                if url.scheme != "https" or not url.host or url.userinfo:
                    raise ValueError("Nur öffentliche HTTPS-Adressen ohne Zugangsdaten sind "
                                     "erlaubt.")
                ip = await public_address(url.host, url.port or 443)
                host_header = url.host + (f":{url.port}" if url.port else "")
                request = client.build_request(
                    "GET", url.copy_with(host=ip),
                    headers={"Host": host_header, "User-Agent": "JARVIS/0.2 (+local assistant)",
                             "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5"},
                    extensions={"sni_hostname": url.host})
                response = await client.send(request, stream=True)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Weiterleitung ohne Ziel.")
                        url = url.join(location)
                        continue
                    if response.status_code >= 400:
                        raise ValueError(f"HTTP {response.status_code} von {url.host}.")
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith(TEXT_TYPES):
                        raise ValueError(f"Inhaltstyp nicht unterstützt: "
                                         f"{content_type.split(';')[0] or 'unbekannt'}.")
                    body = bytearray()
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_DOWNLOAD:
                            truncated = True
                            break
                    encoding = response.charset_encoding or "utf-8"
                finally:
                    await response.aclose()
                try:
                    raw = bytes(body).decode(encoding, errors="replace")
                except LookupError:
                    raw = bytes(body).decode("utf-8", errors="replace")
                if "html" in content_type:
                    title, text, links = html_to_text(raw, url)
                else:
                    title, text, links = "", raw, []
                truncated = truncated or len(text) > MAX_TEXT
                return {"url": str(url), "title": title, "text": text[:MAX_TEXT],
                        "truncated": truncated, "links": links[:40]}
        raise ValueError("Zu viele Weiterleitungen.")

    async def search_web(args: dict) -> Any:
        query = args["query"].strip()
        if not query or len(query) > 400:
            raise ValueError("Suchbegriff muss 1 bis 400 Zeichen enthalten.")
        count = args.get("count", 6)
        settings = registry.settings.load() if registry.settings else {}
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False,
                                     transport=transport) as client:
            if settings.get("search_provider") == "searxng":
                base = settings.get("searxng_url")
                if not base:
                    raise ValueError("SearXNG-URL fehlt in den Einstellungen.")
                response = await client.get(f"{base}/search", params={
                    "q": query, "format": "json", "safesearch": 1})
                if response.status_code == 403:
                    raise ValueError("SearXNG erlaubt kein JSON-Format; in settings.yml "
                                     "'json' unter search.formats aktivieren.")
                response.raise_for_status()
                rows = response.json().get("results", [])
                return [{"title": r.get("title"), "url": r.get("url"),
                         "snippet": r.get("content")} for r in rows[:count]]
            key = registry.secrets.get("brave-search")
            if not key:
                raise ValueError("Websuche ist nicht eingerichtet: Brave-API-Key oder SearXNG "
                                 "in den Einstellungen hinterlegen.")
            response = await client.get("https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": key, "Accept": "application/json"})
            if response.status_code in (401, 403):
                raise ValueError("Brave Search lehnt den API-Key ab.")
            if response.status_code == 429:
                raise ValueError("Brave Search Rate-Limit erreicht.")
            response.raise_for_status()
            rows = response.json().get("web", {}).get("results", [])
        return [{"title": r.get("title"), "url": r.get("url"),
                 "snippet": r.get("description")} for r in rows[:count]]

    registry.register(Tool("search_web",
        "Search the live web. Returns title, url and snippet; use fetch_url to read a result.",
        params({"query": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
        "network.search", False, search_web))
    registry.register(Tool("fetch_url",
        "Read a public HTTPS page as plain text with its title and outgoing links.",
        params({"url": {"type": "string"}}, ["url"]), "network.fetch", False, fetch_url))
