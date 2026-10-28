"""A small Documenso API client (the v2 public API): the calls the tools and the seeder need, nothing more."""

import json
import os
from typing import Any

import httpx


class DocumensoError(Exception):
    pass


class Documenso:
    def __init__(self, url: str | None = None, token: str | None = None, transport: httpx.BaseTransport | None = None):
        self.url = (url or os.environ.get("DOCUMENSO_URL", "http://127.0.0.1:3020")).rstrip("/")
        token = token or os.environ.get("DOCUMENSO_TOKEN", "")
        if not token:
            raise DocumensoError("DOCUMENSO_TOKEN is not set (scripts/up.sh writes it to .env)")
        self.http = httpx.Client(base_url=self.url + "/api/v2/", headers={"Authorization": token}, timeout=60,
                                 transport=transport)

    def call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, path, **kw)
        body = r.json() if r.content else None
        if r.status_code >= 400:
            msg = body.get("message") if isinstance(body, dict) else None
            raise DocumensoError(msg or f"Documenso answered {r.status_code}")
        return body

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, params=params)

    def post(self, path: str, data: dict) -> Any:
        return self.call("POST", path, json=data)

    def create_document(self, payload: dict, pdf: bytes, filename: str) -> Any:
        return self.call("POST", "document/create", data={"payload": json.dumps(payload)},
                         files={"file": (filename, pdf, "application/pdf")})

    def documents(self, **params: Any) -> list[dict]:
        out, page = [], 1
        while True:
            body = self.get("document", page=page, perPage=100, **params)
            out += body["data"]
            if page >= body.get("totalPages", 1):
                return out
            page += 1
