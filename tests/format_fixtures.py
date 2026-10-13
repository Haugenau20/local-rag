"""Synthetic CPU-only document fixtures; no user data or model calls."""

from io import BytesIO


def pdf_bytes(pages, *, encrypted=False):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=(612, 792), invariant=True)
    for text in pages:
        if text is None:
            from PIL import Image
            document.drawImage(ImageReader(Image.new("RGB", (20, 20), "black")), 40, 500, width=200, height=100)
        else:
            y = 740
            for line in text.split("\n"):
                if line:
                    document.drawString(40, y, line)
                y -= 16
        document.showPage()
    document.save()
    raw = buffer.getvalue()
    if encrypted:
        from pypdf import PdfReader, PdfWriter
        output = BytesIO()
        writer = PdfWriter()
        # A fresh writer avoids inheriting ReportLab's textual trailer ID
        # when pypdf computes the encryption key.
        for page in PdfReader(BytesIO(raw)).pages:
            writer.add_page(page)
        writer.encrypt("secret")
        writer.write(output)
        return output.getvalue()
    return raw


def docx_bytes(build=None):
    from docx import Document
    document = Document()
    if build:
        build(document)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
