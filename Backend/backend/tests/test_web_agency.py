"""Browsing, research and web bridging (network faked where possible)."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.agency import tools


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def site():
    """A tiny local website + JSON API for the browser / bridge to use."""
    pages = {
        "/": b"""<html><head><title>Shop</title></head><body>
            <h1>Welcome to the shop</h1>
            <input id=q placeholder="Search products"><button onclick="document.getElementById('r').innerText='Found: '+document.getElementById('q').value">Search</button>
            <p id=r></p><a href="/about">About us</a></body></html>""",
        "/about": b"<html><head><title>About</title></head><body><p>We sell handmade mugs since 2019.</p></body></html>",
        "/products": b"""<html><body><div class=item><a href="/p/1">Blue mug</a></div><div class=item><a href="/p/2">Red mug</a></div>
            <table><tr><th>Item</th><th>Price</th></tr><tr><td>Blue mug</td><td>$12</td></tr><tr><td>Red mug</td><td>$14</td></tr></table></body></html>""",
    }

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/price"):
                body, ctype = json.dumps({"item": "mug", "price": 12.5}).encode(), "application/json"
            else:
                body, ctype = pages.get(self.path, b"not found"), "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            body = json.dumps({"echo": data}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_web_bridge_get_and_post(site):
    got = run(tools.call("http_request", {"url": f"{site}/api/price?x=1"}))
    assert got["ok"] and '"price": 12.5' in got["result"]
    posted = run(tools.call("http_request", {"url": f"{site}/api/order", "method": "POST", "json": {"qty": 2}}))
    assert posted["ok"] and '"status": 201' in posted["result"] and '"qty": 2' in posted["result"]


def test_web_bridge_refuses_her_own_backend():
    out = run(tools.call("http_request", {"url": "http://127.0.0.1:8907/api/agency/resume", "method": "POST"}))
    assert not out["ok"] and "your own backend" in out["result"]


def test_browser_reads_clicks_types_and_follows_links(site):
    async def session():
        from backend.agency.browser import browser
        try:
            page = await browser.act("open", url=site)
            assert page["title"] == "Shop" and "Welcome to the shop" in page["text"]
            refs = {line.split("]")[0].strip("["): line for line in page["elements"]}
            box = next(r for r, l in refs.items() if "Search products" in l)
            typed = await browser.act("type", ref=int(box), text="blue mug")
            button = next(l for l in typed["elements"] if '"Search"' in l).split("]")[0].strip("[")
            clicked = await browser.act("click", ref=int(button))
            assert "Found: blue mug" in clicked["text"]
            link = next(l for l in clicked["elements"] if "About us" in l).split("]")[0].strip("[")
            about = await browser.act("click", ref=int(link))
            assert about["title"] == "About" and "handmade mugs" in about["text"]
            back = await browser.act("back")
            assert back["title"] == "Shop"
        finally:
            await browser.close()

    asyncio.run(session())


def test_browser_scrapes_items_and_tables(site):
    async def session():
        from backend.agency.browser import browser
        try:
            await browser.act("open", url=f"{site}/products")
            items = await browser.act("extract", text=".item a")
            tables = await browser.act("tables")
            return items, tables
        finally:
            await browser.close()

    items, tables = asyncio.run(session())
    assert items["count"] == 2 and items["items"][0]["text"] == "Blue mug" and items["items"][0]["href"].endswith("/p/1")
    assert tables["tables"][0] == [["Item", "Price"], ["Blue mug", "$12"], ["Red mug", "$14"]]


def test_websites_open_in_chrome(monkeypatch):
    from backend.agency import desktop

    launched = []
    monkeypatch.setattr(desktop, "chrome_path", lambda: r"C:\Chrome\chrome.exe")
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **kw: launched.append(args))
    assert "Chrome" in run(tools.call("open_item", {"target": "https://www.youtube.com/results?search_query=lofi"}))["result"]
    run(tools.call("open_item", {"target": "youtube.com"}))
    assert launched == [[r"C:\Chrome\chrome.exe", "https://www.youtube.com/results?search_query=lofi"],
                        [r"C:\Chrome\chrome.exe", "https://youtube.com"]]


def test_research_picks_distinct_sources_and_relevant_passages(monkeypatch):
    import ddgs

    class FakeDDGS:
        def text(self, query, max_results=10):
            return [
                {"title": "A", "href": "https://a.example/1", "body": "snippet a"},
                {"title": "A again", "href": "https://a.example/2", "body": "dup domain"},
                {"title": "B", "href": "https://b.example/x", "body": "snippet b"},
            ]

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    texts = {
        "https://a.example/1": "Unrelated intro paragraph about the weather and other things here.\n\n"
                               "Margit, the Fell Omen is weak to bleed and jump attacks; Margit's Shackle stuns him twice.",
        "https://b.example/x": "",
    }
    monkeypatch.setattr(tools, "_page_text", lambda url: texts[url])
    out = json.loads(run(tools.call("research", {"question": "How do I beat Margit the Fell Omen?", "sources": 3}))["result"])
    urls = [s["url"] for s in out["sources"]]
    assert urls == ["https://a.example/1", "https://b.example/x"]  # one page per site
    assert out["sources"][0]["passages"][0].startswith("Margit, the Fell Omen")
    assert out["sources"][1]["passages"] == ["snippet b"]  # unreadable page: fall back to the snippet
