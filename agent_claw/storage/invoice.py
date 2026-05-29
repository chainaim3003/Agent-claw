"""Invoice/PDF generation. Falls back to .txt if reportlab is unavailable."""
from __future__ import annotations
from pathlib import Path
from ..config import OUTPUT_DIR
from ..logging_setup import get_logger

log = get_logger("storage.invoice")


def generate_invoice(record: dict) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    lines = [
        "RESERVATION CONFIRMATION",
        f"Confirmation: {record['confirmation_id']}",
        f"Restaurant:   {record['restaurant_name']} ({record['restaurant_id']})",
        f"Date / Time:  {record['date']} @ {record['slot']}",
        f"Party size:   {record['party']}",
        f"Contact:      {record['contact']}",
        f"Status:       {record['status']}  (provider: {record['provider']})",
    ]
    pdf_path = OUTPUT_DIR / f"invoice_{record['confirmation_id']}.pdf"
    try:
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.pdfgen import canvas      # type: ignore
        c = canvas.Canvas(str(pdf_path), pagesize=A4)
        y = 800
        c.setFont("Helvetica-Bold", 16); c.drawString(60, y, lines[0]); y -= 40
        c.setFont("Helvetica", 12)
        for ln in lines[1:]:
            c.drawString(60, y, ln); y -= 22
        c.showPage(); c.save()
        log.info("wrote PDF invoice %s", pdf_path)
        return pdf_path
    except Exception as e:  # noqa: BLE001 - any reportlab failure -> text fallback
        log.warning("reportlab unavailable (%s); writing .txt invoice", e)
        txt_path = OUTPUT_DIR / f"invoice_{record['confirmation_id']}.txt"
        txt_path.write_text("\n".join(lines))
        return txt_path
