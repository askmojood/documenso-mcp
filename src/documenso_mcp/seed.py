"""Fill a fresh local Documenso with the contracts of Harbor Trading, a made-up Gulf supplier (SAMPLE data).

Documents (one-page SAMPLE PDFs), their signers and signature fields go in through Documenso's own API, and every
document that isn't a draft is sent, so the emails land in the local Mailpit and nothing leaves the machine. The API
can't sign on someone's behalf or set past times, so the seeder also writes docker/backdate.sql (who has opened,
signed or rejected, and when it was sent), which scripts/reset.sh applies: there is then always a contract waiting
on our own side, an NDA opened but not signed, one never opened, a rejection, a draft and completed history.
Run on a fresh Documenso only (scripts/reset.sh).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from documenso_mcp.client import Documenso
from documenso_mcp.pdf import text_pdf

HT = "harbor-trading.example"


def ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


# title, body lines, sent (hours ago, None = draft), status after signing, signers:
# (name, email, read, signed (hours ago, None = not yet), rejection reason)
DOCUMENTS = [
    ("Supply agreement 2027: Al Noor Hotels", [
        "Harbor Trading supplies room amenities and housekeeping stock to Al Noor Hotels.",
        "Term: 1 January to 31 December 2027. Prices as in the attached 2027 price list (SAMPLE).",
        "Payment: 30 days from invoice. No interest or late fees."], 52, "PENDING", [
        ("Mona Farouk", "mona@alnoor-hotels.example", True, 30, None),
        ("Omar Haddad", f"omar.haddad@{HT}", True, None, None)]),
    ("Mutual NDA: Bayt Interiors", [
        "Mutual non-disclosure agreement between Harbor Trading and Bayt Interiors,",
        "covering the hotel minibar project in Riyadh. Duration: two years."], 120, "PENDING", [
        ("Noor Al-Saadi", "noor@bayt-interiors.example", True, None, None)]),
    ("Distribution agreement: Gulf Build Contracting", [
        "Harbor Trading is the preferred supplier of PPE and site consumables to Gulf Build Contracting",
        "for its projects in the Eastern Province, from 1 December 2026."], 192, "PENDING", [
        ("Yousef Karim", f"yousef.karim@{HT}", True, 190, None),
        ("Rashid Al-Mutairi", "rashid@gulf-build.example", False, None, None)]),
    ("Offer letter: buyer, Dammam HQ", [
        "Offer of employment as Buyer at Harbor Trading, Dammam HQ, starting 8 November 2026."], 20, "PENDING", [
        ("Hessa Al-Mutawa", "hessa.almutawa@mail.example", True, None, None)]),
    ("Delivery acceptance: Desert Rose Catering", [
        "Desert Rose Catering accepts delivery and installation of one conveyor dishwasher (SAMPLE)."], 260, "COMPLETED", [
        ("Fatima Zaidi", "fatima@desertrose-catering.example", True, 250, None)]),
    ("Service contract renewal: Coastal Marine Services", [
        "Renewal of the fastener supply and stock-holding service for 2027."], 400, "COMPLETED", [
        ("James Whitfield", "james@coastal-marine.example", True, 380, None),
        ("Omar Haddad", f"omar.haddad@{HT}", True, 370, None)]),
    ("Mutual NDA: Najd Facilities Management", [
        "Mutual non-disclosure agreement for the Riyadh towers cleaning-supply tender."], 150, "REJECTED", [
        ("Abdullah Al-Rashid", "abdullah@najd-facilities.example", True, None,
         "The legal entity name is wrong: it should be Najd Facilities Management Co.")]),
    ("Price list acknowledgement 2027: Red Sea Fit-Out", [
        "Red Sea Fit-Out acknowledges Harbor Trading's 2027 price list (SAMPLE prices)."], None, "DRAFT", [
        ("Elena Petrova", "elena@redsea-fitout.example", False, None, None)]),
]


def seed(api: Documenso, backdate: Path) -> dict:
    if api.get("document", perPage=1)["count"]:
        raise SystemExit("Documenso already has documents: seed a fresh one (scripts/reset.sh)")
    sql: list[str] = []
    for title, lines, sent, status, signers in DOCUMENTS:
        recipients = [{"email": e, "name": n, "role": "SIGNER", "signingOrder": i,
                       "fields": [{"type": "SIGNATURE", "pageNumber": 1, "pageX": 10 + 45 * (i % 2), "pageY": 76,
                                   "width": 35, "height": 6}]}
                      for i, (n, e, *_rest) in enumerate(signers)]
        doc = api.create_document({"title": title, "recipients": recipients}, text_pdf(title, lines),
                                  title.split(":")[0].lower().replace(" ", "-") + ".pdf")
        env = doc["envelopeId"]
        created = ago((sent or 2) + 1)
        sql.append(f"""UPDATE "Envelope" SET "createdAt"='{created}', "updatedAt"='{created}' WHERE id='{env}';""")
        if sent is None:
            continue
        api.post("document/distribute", {"documentId": doc["id"]})
        when = ago(sent)
        sql.append(f"""UPDATE "Recipient" SET "sentAt"='{when}' WHERE "envelopeId"='{env}';""")
        sql.append(f"""UPDATE "DocumentAuditLog" SET "createdAt"='{when}' WHERE "envelopeId"='{env}';""")
        last = sent
        for name, email, read, signed, rejected in signers:
            cols = [f""""readStatus"='{"OPENED" if read else "NOT_OPENED"}'"""]
            if signed is not None:
                cols += [""""signingStatus"='SIGNED'""", f""""signedAt"='{ago(signed)}'"""]
                last = min(last, signed)
            if rejected:
                cols += [""""signingStatus"='REJECTED'""", f""""rejectionReason"='{rejected.replace("'", "''")}'""",
                         f""""signedAt"='{ago(sent - 20)}'"""]
                last = sent - 20
            sql.append(f"""UPDATE "Recipient" SET {", ".join(cols)} WHERE "envelopeId"='{env}' AND email='{email}';""")
        extra = f""", "completedAt"='{ago(last)}'""" if status == "COMPLETED" else ""
        sql.append(f"""UPDATE "Envelope" SET status='{status}', "updatedAt"='{ago(last)}'{extra} WHERE id='{env}';""")
    backdate.write_text("\n".join(sql) + "\n")
    return {"documents": len(DOCUMENTS), "sent": sum(1 for d in DOCUMENTS if d[2] is not None)}


def main() -> None:
    print(seed(Documenso(), Path(__file__).resolve().parents[2] / "docker" / "backdate.sql"))
