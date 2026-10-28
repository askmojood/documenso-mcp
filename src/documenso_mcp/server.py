"""Documenso MCP server (Mojood Connect demo): ask about contracts and signatures in plain words, and send reminders
or drafts with a confirmation step.

Run:  uv run --env-file .env documenso-mcp          → http://127.0.0.1:8007/mcp (Streamable HTTP)
      uv run --env-file .env documenso-mcp --stdio  → for Claude Desktop's config file

Settings (environment, written to .env by scripts/up.sh):
  DOCUMENSO_URL      the Documenso instance (default http://127.0.0.1:3020)
  DOCUMENSO_TOKEN    a Documenso API token; the server acts for that token's team
  DOCUMENSO_SAMPLE   "1" (default) says in every session that the data is SAMPLE
  DOCUMENSO_MCP_PORT default 8007
"""

import logging
import os
import sys
from typing import Annotated, Literal

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from documenso_mcp import tools
from documenso_mcp.client import Documenso

SAMPLE = os.environ.get("DOCUMENSO_SAMPLE", "1") != "0"
_api: Documenso | None = None


def api() -> Documenso:
    global _api
    if _api is None:
        _api = Documenso()
    return _api


INSTRUCTIONS = f"""\
Documenso e-signing: documents sent for signature, who has opened, signed or rejected them, and their history.
{"This is a demo with SAMPLE data for a made-up company, Harbor Trading; every email goes to a local mail catcher. " if SAMPLE else ""}\
Answer only from these tools; never guess who has signed. Documents are numbered (e.g. 3). send_reminder and
send_draft email real people. Call one without `confirmation` first: it sends nothing and returns what it would do
plus a code. Show the user that summary and ask them to confirm. Only after they say yes, call again with the same
arguments and the code. Never confirm on the user's behalf. If a tool returns `problem`, nothing was sent: tell the
user why. Never sign on anyone's behalf: there is no tool for it."""

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
SEND = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)

server = MCPServer(
    name="documenso-mcp",
    title="Documenso",
    description="Documents and signatures in Documenso: find documents, who still has to sign, history, reminders and sending drafts.",
    instructions=INSTRUCTIONS,
    website_url="https://mojood.app/connect",
    version="0.1.0",
)


@server.tool(title="Find documents", annotations=READ)
def find_documents(
    status: Annotated[Literal["all", "waiting", "completed", "rejected", "draft"], Field(description="which documents")] = "all",
    query: Annotated[str | None, Field(description='words from the title, e.g. "NDA"')] = None,
) -> tools.Documents:
    """List documents with their status, when they were sent, each signer's status and who still has to sign."""
    return tools.find_documents(api(), status, query)


@server.tool(title="Read a document", annotations=READ)
def get_document(document_id: Annotated[int, Field(description="e.g. 3")]) -> tools.Detail:
    """One document: its signers (signed, rejected with the reason, opened, not opened) and its history from
    Documenso's audit log."""
    return tools.get_document(api(), document_id)


@server.tool(title="A contact's documents", annotations=READ)
def contact_documents(who: Annotated[str, Field(description='a person, an email or a company in the title, e.g. "Gulf Build"')]) -> tools.Contact:
    """Every document a person (or a company in its title) is part of, with their status on each."""
    return tools.contact_documents(api(), who)


@server.tool(title="What needs attention", annotations=READ)
def needs_attention() -> tools.Attention:
    """Every signer still to sign, longest wait first (and which of them are our own people), rejected documents with
    the reason, and drafts not sent yet."""
    return tools.needs_attention(api())


@server.tool(title="Send a reminder", annotations=SEND)
def send_reminder(
    document_id: Annotated[int, Field(description="e.g. 3")],
    signer: Annotated[str | None, Field(description="who to remind; empty means everyone still to sign")] = None,
    confirmation: Annotated[str | None, Field(description="leave empty first; pass the code only after the user confirms")] = None,
) -> tools.Change:
    """Email a reminder to sign. The first call sends nothing and returns a summary and a confirmation code; the
    reminder goes only on a second call with the code, after the user has said yes."""
    return tools.send_reminder(api(), document_id, signer, confirmation)


@server.tool(title="Send a draft for signing", annotations=SEND)
def send_draft(
    document_id: Annotated[int, Field(description="e.g. 8")],
    confirmation: Annotated[str | None, Field(description="leave empty first; pass the code only after the user confirms")] = None,
) -> tools.Change:
    """Send a draft to its signers. The first call sends nothing and returns a summary and a confirmation code; it is
    sent only on a second call with the code, after the user has said yes."""
    return tools.send_draft(api(), document_id, confirmation)


def app():
    security = TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    )
    # Stateful sessions: a confirmation code must reach the same server process that issued it.
    return server.streamable_http_app(json_response=True, transport_security=security)


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Documenso call is noise in a client's log
    if "--stdio" in sys.argv:
        server.run("stdio")
        return
    uvicorn.run(app(), host="127.0.0.1", port=int(os.environ.get("DOCUMENSO_MCP_PORT", "8007")), log_level="info",
                timeout_graceful_shutdown=5)
