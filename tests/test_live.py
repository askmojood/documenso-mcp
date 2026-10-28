"""Against the local Documenso with the SAMPLE contracts (scripts/reset.sh). Skipped when it isn't running.

Run: uv run --env-file .env pytest tests/test_live.py
"""

import os

import httpx
import pytest

from documenso_mcp import tools
from documenso_mcp.client import Documenso


def _up() -> bool:
    try:
        return bool(os.environ.get("DOCUMENSO_TOKEN")) and httpx.get(
            os.environ.get("DOCUMENSO_URL", "http://127.0.0.1:3020") + "/signin", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="local Documenso not running (scripts/up.sh)")


@pytest.fixture(scope="module")
def api():
    return Documenso()


def test_the_seeded_contracts_need_attention(api):
    n = tools.needs_attention(api)
    assert n.waiting[0].signer == "Rashid Al-Mutairi" and n.waiting[0].status == "not opened"
    assert [w.signer for w in n.waiting_on_us] == ["Omar Haddad"] and n.rejected and n.drafts


def test_history_comes_from_the_audit_log(api):
    d = tools.get_document(api, tools.find_documents(api, query="Gulf Build").documents[0].id)
    assert any(e.what == "sent for signing" for e in d.history)


def test_a_reminder_lands_in_mailpit(api):
    doc = tools.find_documents(api, query="Offer letter").documents[0]
    p = tools.send_reminder(api, doc.id)
    assert tools.send_reminder(api, doc.id, confirmation=p.confirmation).done
    msgs = httpx.get("http://127.0.0.1:8026/api/v1/messages?limit=5", timeout=5).json()["messages"]
    assert any(m["To"][0]["Address"] == "hessa.almutawa@mail.example" and "Reminder" in m["Subject"] for m in msgs)
