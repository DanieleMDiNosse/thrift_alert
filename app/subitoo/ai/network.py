"""Bounded public-only source retrieval and pluggable free search.

DNS is validated on every request and the connection is pinned to a validated IP
while TLS verifies the original host. No cookies, proxy env, credentials, JS,
paywall bypass or arbitrary browser/agent tools. Redirects are rejected rather
than allowing redirect/robots policy bypasses. SearXNG is an operator-trusted
backend URL, never supplied by the browser or an LLM.
"""

from __future__ import annotations

from html.parser import HTMLParser
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

USER_AGENT = "MarketwatchResearch/1.0"
MAX_BYTES = 1_000_000
TIMEOUT = 12
_gate = threading.Lock()
_last_request = {}


def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise ValueError("Invalid source URL")
    try:
        p = urlsplit(url)
        if (
            p.scheme not in ("http", "https")
            or not p.hostname
            or p.username
            or p.password
            or p.port not in (None, 80 if p.scheme == "http" else 443)
        ):
            raise ValueError(
                "Only public HTTP(S) sources on standard ports are permitted"
            )
        host = p.hostname.encode("idna").decode("ascii")
        if "%" in host:
            raise ValueError("Scoped addresses are not permitted")
        addresses = socket.getaddrinfo(
            host,
            p.port or (443 if p.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        ips = list(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError(
                "Private, internal and reserved network addresses are blocked"
            )
        return p, ips
    except (OSError, UnicodeError) as exc:
        raise ValueError("Source DNS lookup unavailable") from exc


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port):
        super().__init__(
            host, port, timeout=TIMEOUT, context=ssl.create_default_context()
        )
        self.ip = ip

    def connect(self):
        sock = socket.create_connection((self.ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def safe_get(url, *, max_bytes=MAX_BYTES):
    p, ips = validate_url(url)
    # Reserve a per-host slot, so concurrent threads cannot exceed one request/sec.
    with _gate:
        now = time.monotonic()
        slot = max(now, _last_request.get(p.hostname, 0) + 1.0)
        _last_request[p.hostname] = slot
        if len(_last_request) > 1024:
            for host in list(_last_request):
                if _last_request[host] < now - 60:
                    _last_request.pop(host, None)
    time.sleep(max(0.0, slot - time.monotonic()))
    port = p.port or (443 if p.scheme == "https" else 80)
    conn = (
        _PinnedHTTPS(p.hostname, ips[0], port)
        if p.scheme == "https"
        else http.client.HTTPConnection(ips[0], port, timeout=TIMEOUT)
    )
    try:
        conn.request(
            "GET",
            urlunsplit(("", "", p.path or "/", p.query, "")),
            headers={
                "Host": p.netloc,
                "User-Agent": USER_AGENT,
                "Accept": "text/html,text/plain",
                "Accept-Encoding": "identity",
            },
        )
        response = conn.getresponse()
        if response.status == 404:
            raise FileNotFoundError("Source not found")
        if response.status != 200:
            raise ValueError(
                f"Source unavailable (HTTP {response.status}); redirects/access barriers are not bypassed"
            )
        if response.getheader("Content-Encoding", "identity") != "identity":
            raise ValueError("Compressed source not accepted")
        size = response.getheader("Content-Length")
        if size and (not size.isdecimal() or int(size) > max_bytes):
            raise ValueError("Source response too large")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("Source response too large")
        kind = response.getheader("Content-Type", "").split(";")[0].strip().lower()
        if kind not in ("text/html", "text/plain", "application/xhtml+xml"):
            raise ValueError("Source is not a supported text document")
        return {"url": url, "body": body, "content_type": kind}
    finally:
        conn.close()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.in_title = False
        self.title, self.parts = [], []
        self.publication_date = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            a = dict(attrs)
            if a.get("property") in (
                "article:published_time",
                "datePublished",
            ) or a.get("name") in ("date", "pubdate"):
                self.publication_date = (a.get("content") or "")[:100] or None

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if not self.hidden:
            if self.in_title:
                self.title.append(data)
            elif data.strip():
                self.parts.append(data.strip())


def extract_html(html):
    parser = _Text()
    parser.feed(html)
    return {
        "title": " ".join(parser.title)[:500],
        "text": "\n".join(parser.parts)[:24000],
        "publication_date": parser.publication_date,
    }


def fetch_page(url):
    """Check robots for each page. Unavailable robots fails closed; 404 means absent."""
    p = urlsplit(url)
    robots_url = urlunsplit((p.scheme, p.netloc, "/robots.txt", "", ""))
    try:
        rules = safe_get(robots_url, max_bytes=128000)["body"].decode(
            "utf-8", "replace"
        )
    except FileNotFoundError:
        rules = ""
    except (OSError, ValueError) as exc:
        raise ValueError("Source robots policy unavailable; retrieval skipped") from exc
    parser = RobotFileParser()
    parser.parse(rules.splitlines())
    if not parser.can_fetch(USER_AGENT, url):
        raise ValueError("Source robots policy disallows retrieval")
    delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*")
    if delay:
        if delay > 10:
            raise ValueError("Source crawl delay exceeds worker budget; skipped")
        time.sleep(delay)
    response = safe_get(url)
    text = response["body"].decode("utf-8", "replace")
    result = (
        extract_html(text)
        if response["content_type"] != "text/plain"
        else {"title": p.hostname, "text": text[:24000], "publication_date": None}
    )
    if not result["text"].strip():
        raise ValueError("No readable source passages")
    return {
        **result,
        "url": url,
        "website": p.hostname,
        "retrieved_at": int(time.time()),
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Search backend redirects are disabled")


class SearchBackend:
    def __init__(self, config):
        self.config = config

    def search(self, query):
        if not isinstance(query, str) or not 1 <= len(query) <= 500:
            raise ValueError("Invalid search query")
        backend = getattr(self.config, "search_backend", "ddgs")
        limit = min(8, max(1, getattr(self.config, "max_sources", 5)))
        if backend == "ddgs":
            try:
                from ddgs import DDGS
            except ImportError as exc:
                raise ValueError(
                    "Install the optional research extra or configure SearXNG"
                ) from exc
            results = DDGS(timeout=TIMEOUT).text(query, max_results=limit)
            return [
                {"url": x["href"], "title": x.get("title", "")[:500]}
                for x in results
                if x.get("href")
            ][:limit]
        if backend == "searxng":
            base = getattr(self.config, "searxng_url", "").rstrip("/")
            p = urlsplit(base)
            if (
                p.scheme not in ("http", "https")
                or not p.hostname
                or p.username
                or p.password
            ):
                raise ValueError("Configure a trusted SearXNG URL on the backend")
            url = base + "/search?" + urlencode({"q": query, "format": "json"})
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            with opener.open(
                Request(url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT
            ) as r:
                body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Search response too large")
            data = json.loads(body)
            return [
                {"url": x["url"], "title": x.get("title", "")[:500]}
                for x in data["results"][:limit]
                if isinstance(x, dict) and isinstance(x.get("url"), str)
            ]
        raise ValueError("Unknown research search backend")
