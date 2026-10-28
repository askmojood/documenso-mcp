"""The six tools, as plain functions over a Documenso client (the MCP wiring is in server.py).

Four only read. The two that change something (send a reminder, send a draft) email real people, so they never act
on the first call: they return a preview and a one-time confirmation code, and act only when called again with that
code and the same request. Documenso's own audit log records both.
"""

import secrets
import time
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from documenso_mcp.client import Documenso, DocumensoError

CONFIRM_TTL_S = 10 * 60
OURS = "@harbor-trading.example"  # signers inside the company: a document waiting on them is waiting on us
STATUS = {"DRAFT": "draft", "PENDING": "waiting for signatures", "COMPLETED": "completed", "REJECTED": "rejected",
          "CANCELLED": "cancelled"}


def _days(iso: str | None) -> float | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso)
    return round((datetime.now(UTC) - t).total_seconds() / 86400, 1)


class Signer(BaseModel):
    id: int
    name: str
    email: str
    status: str = Field(description="signed, rejected, opened (not signed), not opened, or not sent")
    signed_at: str | None = None
    rejection_reason: str | None = None
    internal: bool = Field(description="one of our own people")


class Document(BaseModel):
    id: int
    title: str
    status: str
    sent: str | None = Field(None, description="when it was sent for signing")
    days_since_sent: float | None = None
    completed: str | None = None
    signers: list[Signer] = Field(default_factory=list)
    waiting_on: list[str] = Field(default_factory=list, description="signers who still need to sign")
    link: str | None = None


def _signer(r: dict) -> Signer:
    s = r["signingStatus"]
    status = ("signed" if s == "SIGNED" else "rejected" if s == "REJECTED" else "not sent" if r["sendStatus"] != "SENT"
              else "opened, not signed" if r["readStatus"] == "OPENED" else "not opened")
    return Signer(id=r["id"], name=r["name"], email=r["email"], status=status, signed_at=r.get("signedAt"),
                  rejection_reason=r.get("rejectionReason") or None, internal=r["email"].endswith(OURS))


def _sent_at(api: Documenso, envelope: str) -> str | None:
    logs = api.get(f"envelope/{envelope}/audit-log", perPage=100)["data"]
    sent = [x["createdAt"] for x in logs if x["type"] == "DOCUMENT_SENT"]
    return min(sent) if sent else None


def document_of(api: Documenso, d: dict) -> Document:
    full = d if "recipients" in d else api.get(f"document/{d['id']}")
    signers = [_signer(r) for r in sorted(full["recipients"], key=lambda r: r.get("signingOrder") or 0)]
    sent = _sent_at(api, full["envelopeId"]) if full["status"] != "DRAFT" else None
    return Document(id=full["id"], title=full["title"], status=STATUS.get(full["status"], full["status"].lower()),
                    sent=sent, days_since_sent=_days(sent) if full["status"] == "PENDING" else None,
                    completed=full.get("completedAt"), signers=signers,
                    waiting_on=[s.name for s in signers if full["status"] == "PENDING" and s.status not in ("signed", "rejected")],
                    link=f"{api.url}/documents/{full['id']}")


def _one(api: Documenso, did: int) -> dict | None:
    try:
        return api.get(f"document/{int(did)}")
    except (DocumensoError, ValueError):
        return None


# ---------- read ----------

class Documents(BaseModel):
    documents: list[Document]
    total: int


def find_documents(api: Documenso, status: str = "all", query: str | None = None) -> Documents:
    wanted = {"waiting": "PENDING", "completed": "COMPLETED", "rejected": "REJECTED", "draft": "DRAFT"}.get(status)
    rows = api.documents(**({"status": wanted} if wanted else {}), **({"query": query} if query else {}))
    docs = [document_of(api, d) for d in sorted(rows, key=lambda d: d["id"])]
    return Documents(documents=docs[:25], total=len(docs))


class Event(BaseModel):
    what: str
    when: str
    who: str | None = None


class Detail(BaseModel):
    document: Document | None = None
    history: list[Event] = Field(default_factory=list, description="from Documenso's audit log, oldest first")
    problem: str | None = None


HISTORY = {"DOCUMENT_CREATED": "created", "DOCUMENT_SENT": "sent for signing", "EMAIL_SENT": "email sent",
           "DOCUMENT_OPENED": "opened", "DOCUMENT_RECIPIENT_COMPLETED": "signed", "DOCUMENT_COMPLETED": "completed",
           "DOCUMENT_RECIPIENT_REJECTED": "rejected"}


def get_document(api: Documenso, document_id: int) -> Detail:
    d = _one(api, document_id)
    if not d:
        return Detail(problem=f"No document has the id {document_id}.")
    logs = api.get(f"envelope/{d['envelopeId']}/audit-log", perPage=100)["data"]
    events = [Event(what=HISTORY.get(x["type"], x["type"].lower().replace("_", " ")), when=x["createdAt"],
                    who=(x.get("data") or {}).get("recipientEmail") or x.get("name"))
              for x in sorted(logs, key=lambda x: x["createdAt"])]
    return Detail(document=document_of(api, d), history=events)


class Contact(BaseModel):
    name: str | None = None
    documents: list[Document] = Field(default_factory=list)
    problem: str | None = None


def contact_documents(api: Documenso, who: str) -> Contact:
    q = who.strip().lower()
    docs = [document_of(api, d) for d in api.documents()]
    hits = [d for d in docs if any(q in s.name.lower() or q in s.email.lower() for s in d.signers) or q in d.title.lower()]
    if not hits:
        return Contact(problem=f'No document involves "{who}".')
    names = sorted({s.name for d in hits for s in d.signers if q in s.name.lower() or q in s.email.lower()})
    return Contact(name=", ".join(names) or who, documents=hits)


class Waiting(BaseModel):
    document: str
    id: int
    signer: str
    status: str
    days_since_sent: float | None
    internal: bool


class Attention(BaseModel):
    waiting: list[Waiting] = Field(description="every unsigned signer on a sent document, longest wait first")
    waiting_on_us: list[Waiting] = Field(description="the same, for our own people")
    rejected: list[Document]
    drafts: list[Document] = Field(description="documents not sent yet")


def needs_attention(api: Documenso) -> Attention:
    docs = [document_of(api, d) for d in api.documents()]
    waiting = sorted([Waiting(document=d.title, id=d.id, signer=s.name, status=s.status, days_since_sent=d.days_since_sent,
                              internal=s.internal)
                      for d in docs if d.status == STATUS["PENDING"] for s in d.signers if s.name in d.waiting_on],
                     key=lambda w: -(w.days_since_sent or 0))
    return Attention(waiting=waiting, waiting_on_us=[w for w in waiting if w.internal],
                     rejected=[d for d in docs if d.status == STATUS["REJECTED"]],
                     drafts=[d for d in docs if d.status == STATUS["DRAFT"]])


# ---------- change (preview, then confirm) ----------

class Change(BaseModel):
    done: bool = Field(description="false: nothing has changed yet; true: Documenso has done it")
    summary: str
    confirmation: str | None = Field(None, description="when done is false: the code to pass back, only after the user says yes")
    expires_in_minutes: int | None = None
    document: Document | None = None
    problem: str | None = Field(None, description="why it can't be done; nothing was changed")


_pending: dict[str, tuple[float, tuple]] = {}


def _ask(request: tuple, summary: str, doc: Document) -> Change:
    now = time.monotonic()
    for k in [k for k, (t, _) in _pending.items() if now - t > CONFIRM_TTL_S]:
        del _pending[k]
    code = secrets.token_hex(3)
    _pending[code] = (now, request)
    return Change(done=False, summary=summary + " Nothing has been sent yet: ask the user to confirm.",
                  confirmation=code, expires_in_minutes=CONFIRM_TTL_S // 60, document=doc)


def _confirmed(code: str, request: tuple) -> bool:
    held = _pending.get(code.strip())
    if not held or time.monotonic() - held[0] > CONFIRM_TTL_S or held[1] != request:
        return False
    del _pending[code.strip()]
    return True


def _expired(summary: str, doc: Document) -> Change:
    return Change(done=False, summary=summary, document=doc,
                  problem="That confirmation code doesn't match this request or has expired. Ask again.")


def send_reminder(api: Documenso, document_id: int, signer: str | None = None, confirmation: str | None = None) -> Change:
    d = _one(api, document_id)
    if not d:
        return Change(done=False, summary="", problem=f"No document has the id {document_id}.")
    doc = document_of(api, d)
    if d["status"] != "PENDING":
        return Change(done=False, summary="", document=doc, problem=f"“{doc.title}” is {doc.status}, so there is no one to remind.")
    targets = [s for s in doc.signers if s.name in doc.waiting_on and (not signer or signer.lower() in s.name.lower() or signer.lower() in s.email.lower())]
    if not targets:
        who = f'"{signer}"' if signer else "anyone"
        return Change(done=False, summary="", document=doc, problem=f"{who} isn't waiting to sign “{doc.title}”. Waiting: {', '.join(doc.waiting_on) or 'no one'}.")
    request = ("remind", d["id"], tuple(s.id for s in targets))
    summary = (f"Email a reminder to sign “{doc.title}” to " + ", ".join(f"{s.name} ({s.email})" for s in targets)
               + f", sent {doc.days_since_sent} days ago.")
    if not confirmation:
        return _ask(request, summary, doc)
    if not _confirmed(confirmation, request):
        return _expired(summary, doc)
    api.post("document/redistribute", {"documentId": d["id"], "recipients": [s.id for s in targets]})
    return Change(done=True, summary="Done: the reminder is sent to " + ", ".join(s.name for s in targets) + ".",
                  document=document_of(api, api.get(f"document/{d['id']}")))


def send_draft(api: Documenso, document_id: int, confirmation: str | None = None) -> Change:
    d = _one(api, document_id)
    if not d:
        return Change(done=False, summary="", problem=f"No document has the id {document_id}.")
    doc = document_of(api, d)
    if d["status"] != "DRAFT":
        return Change(done=False, summary="", document=doc, problem=f"“{doc.title}” is already {doc.status}.")
    if not doc.signers:
        return Change(done=False, summary="", document=doc, problem=f"“{doc.title}” has no signers yet.")
    request = ("send", d["id"])
    summary = f"Send “{doc.title}” for signing to " + ", ".join(f"{s.name} ({s.email})" for s in doc.signers) + "."
    if not confirmation:
        return _ask(request, summary, doc)
    if not _confirmed(confirmation, request):
        return _expired(summary, doc)
    api.post("document/distribute", {"documentId": d["id"]})
    return Change(done=True, summary=f"Done: “{doc.title}” is sent for signing.",
                  document=document_of(api, api.get(f"document/{d['id']}")))
