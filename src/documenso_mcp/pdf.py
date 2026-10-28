"""One-page text PDFs for the SAMPLE documents (no PDF library needed): a title, a few lines, a signature line."""


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_pdf(title: str, lines: list[str]) -> bytes:
    ops = ["BT /F2 18 Tf 60 770 Td (" + _esc(title) + ") Tj ET",
           "BT /F1 9 Tf 60 750 Td (SAMPLE document for a demo: Harbor Trading is a made-up company.) Tj ET"]
    y = 715
    for line in lines:
        ops.append(f"BT /F1 11 Tf 60 {y} Td (" + _esc(line) + ") Tj ET")
        y -= 20
    ops.append("0.6 w 60 160 m 300 160 l S")
    ops.append("BT /F1 9 Tf 60 146 Td (Signature) Tj ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
