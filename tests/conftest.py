"""Shared helpers: recorded answers and a scripted fake citilink.ru."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    path = FIXTURES / name
    return json.loads(path.read_text()) if name.endswith(".json") else path.read_text()


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, text: str | None = None,
                 content_type: str = "application/json", cookies: dict | None = None):
        self.status_code = status
        self.text = text if text is not None else json.dumps(body if body is not None else {}, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": content_type}
        self.cookies = _Jar(cookies or {})

    def json(self) -> Any:
        return json.loads(self.text)


class _Cookie:
    def __init__(self, name: str, value: str):
        self.name, self.value = name, value


class _Jar(list):
    def __init__(self, cookies: dict):
        super().__init__(_Cookie(k, v) for k, v in cookies.items())


def html(status: int, text: str) -> FakeResponse:
    return FakeResponse(status, text=text, content_type="text/html; charset=utf-8")


class FakeSite:
    """Records every POST; answers from a script (list) or a router (callable)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.script: list[FakeResponse] = []
        self.router: Callable[[dict], FakeResponse] | None = None

    def post(self, url, data=None, headers=None, **kw):
        body = json.loads(data)
        self.sent.append({"url": url, "query": body["query"], "variables": body["variables"],
                          "cookie": (headers or {}).get("Cookie", "")})
        if self.script:
            return self.script.pop(0)
        if self.router:
            return self.router(body)
        raise AssertionError("unexpected request: " + body["query"][:60])

    def cookies_sent(self, i: int = -1) -> dict[str, str]:
        raw = self.sent[i]["cookie"]
        return dict(part.split("=", 1) for part in raw.split("; ") if part)


def fixture_router(body: dict) -> FakeResponse:
    """Answer each of the server's queries with the matching recorded response."""
    q, v = body["query"], body["variables"]
    if q.startswith("query Search"):
        text = v["in"]["searchText"]
        name = {"samsung 990 pro": "search_samsung_990_pro.json", "бумага a4": "search_bumaga_a4_price_asc.json",
                "ssd samsung": "search_ssd_samsung_instore.json"}[text]
    elif q.startswith("query Product"):
        name = f"product_{v['id']}.json"
    elif q.startswith("query Batch"):
        name = "batch_4.json"
    elif q.startswith("query Reviews"):
        name = ("reviews_1896626_group_worst_123.json" if v["in"]["withGroup"]
                else "reviews_1896626_product_new.json")
    elif q.startswith("query Questions"):
        name = "questions_1896626.json"
    elif q.startswith("query MarkdownCopies"):
        name = "markdown_copies_1856569.json"
    elif q.startswith("query Markdown"):
        name = "markdown_noutbuki.json"
    elif q.startswith("query Cities"):
        name = "cities_sample.json"
    else:
        raise AssertionError("no fixture for " + q[:40])
    return FakeResponse(200, load(name))


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fake citilink.ru behind the real client; no sleeping, private cache dir."""
    from citilink_mcp import client as client_mod

    monkeypatch.setenv("CITILINK_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CITILINK_MIN_INTERVAL", "0")
    monkeypatch.delenv("CITILINK_CITY", raising=False)
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    fake = FakeSite()
    monkeypatch.setattr(client_mod.requests.Session, "post", fake.post)
    return fake
