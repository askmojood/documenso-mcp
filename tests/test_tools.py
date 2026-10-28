"""Unit tests over a fake Documenso API (httpx.MockTransport): no Docker needed."""

import json

import httpx
import pytest

from documenso_mcp import tools
from documenso_mcp.client import Documenso, DocumensoError

SENT = "2026-10-20T14:18:05.000Z"


def recipient(rid, name, email, signing="NOT_SIGNED", read="NOT_OPENED", send="SENT"):
    return {"id": rid, "name": name, "email": email, "signingStatus": signing, "readStatus": read, "sendStatus": send,
            "signingOrder": rid, "signedAt": None, "rejectionReason": None}


class Fake:
    def __init__(self):
        self.docs = {
            3: {"id": 3, "title": "Distribution agreement", "status": "PENDING", "envelopeId": "env3", "completedAt": None,
                "recipients": [recipient(1, "Yousef Karim", "yousef@harbor-trading.example", "SIGNED", "OPENED"),
                               recipient(2, "Rashid Al-Mutairi", "rashid@gulf-build.example")]},
            8: {"id": 8, "title": "Price list acknowledgement", "status": "DRAFT", "envelopeId": "env8", "completedAt": None,
                "recipients": [recipient(3, "Elena Petrova", "elena@redsea.example", send="NOT_SENT")]},
        }
        self.posts: list[tuple[str, dict]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/v2/")
        if req.method == "POST":
            body = json.loads(req.content)
            self.posts.append((path, body))
            return httpx.Response(200, json={"success": True})
        if path == "document":
            return httpx.Response(200, json={"data": list(self.docs.values()), "count": 2, "totalPages": 1})
        if path.startswith("document/"):
            d = self.docs.get(int(path.split("/")[1]))
            return httpx.Response(200, json=d) if d else httpx.Response(404, json={"message": "Document not found"})
        if path.startswith("envelope/") and path.endswith("audit-log"):
            return httpx.Response(200, json={"data": [{"type": "DOCUMENT_SENT", "createdAt": SENT, "data": {}}]})
        return httpx.Response(404, json={"message": "nope"})


@pytest.fixture
def fake():
    return Fake()


def api(fake: Fake) -> Documenso:
    return Documenso("http://doc.test", "api_x", transport=httpx.MockTransport(fake))


def test_needs_a_token(monkeypatch):
    monkeypatch.delenv("DOCUMENSO_TOKEN", raising=False)
    with pytest.raises(DocumensoError):
        Documenso("http://doc.test", "")


def test_errors_carry_documensos_message(fake):
    with pytest.raises(DocumensoError, match="Document not found"):
        api(fake).get("document/99")


def test_signer_statuses(fake):
    d = tools.document_of(api(fake), fake.docs[3])
    assert [(s.name, s.status, s.internal) for s in d.signers] == [
        ("Yousef Karim", "signed", True), ("Rashid Al-Mutairi", "not opened", False)]
    assert d.waiting_on == ["Rashid Al-Mutairi"] and d.days_since_sent > 0


def test_needs_attention_puts_the_longest_wait_first_and_finds_drafts(fake):
    n = tools.needs_attention(api(fake))
    assert [w.signer for w in n.waiting] == ["Rashid Al-Mutairi"] and n.waiting_on_us == []
    assert [d.id for d in n.drafts] == [8]


def test_reminder_sends_nothing_without_a_code(fake):
    c = tools.send_reminder(api(fake), 3, "Rashid")
    assert not c.done and c.confirmation and "rashid@gulf-build.example" in c.summary and fake.posts == []


def test_reminder_with_the_code_goes_to_that_signer_once(fake):
    a = api(fake)
    code = tools.send_reminder(a, 3, "Rashid").confirmation
    assert tools.send_reminder(a, 3, "Rashid", confirmation=code).done
    assert fake.posts == [("document/redistribute", {"documentId": 3, "recipients": [2]})]
    assert not tools.send_reminder(a, 3, "Rashid", confirmation=code).done  # used once


def test_no_reminder_to_someone_who_signed(fake):
    assert "isn't waiting" in tools.send_reminder(api(fake), 3, "Yousef").problem


def test_a_code_only_fits_its_own_request(fake):
    a = api(fake)
    code = tools.send_reminder(a, 3, "Rashid").confirmation
    assert not tools.send_draft(a, 8, confirmation=code).done
    assert not tools.send_reminder(a, 8, confirmation=code).done  # another document
    assert fake.posts == []


def test_send_draft_previews_then_distributes(fake):
    a = api(fake)
    p = tools.send_draft(a, 8)
    assert "Elena Petrova" in p.summary and fake.posts == []
    assert tools.send_draft(a, 8, confirmation=p.confirmation).done
    assert fake.posts == [("document/distribute", {"documentId": 8})]
    assert "already" in tools.send_draft(a, 3).problem


def test_an_expired_code_is_refused(fake, monkeypatch):
    a = api(fake)
    code = tools.send_reminder(a, 3).confirmation
    monkeypatch.setattr(tools, "CONFIRM_TTL_S", -1)
    assert not tools.send_reminder(a, 3, confirmation=code).done
    assert fake.posts == []
