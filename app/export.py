"""CSV / Excel export of a user's contacts."""

from __future__ import annotations

import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Font

from .schemas import Contact

EXPORT_FIELDS = [
    "name", "name_native", "title", "title_native", "department", "company", "company_native",
    "industry", "description", "phones", "emails", "website", "address", "address_native",
    "city", "province_state", "country", "postal_code", "wechat", "whatsapp", "linkedin",
    "other_contacts", "tags", "notes", "created_at",
]

LABELS = {
    "name_native": "Name (native)", "title_native": "Title (native)", "company_native": "Company (native)",
    "address_native": "Address (native)", "province_state": "Province/State", "postal_code": "Postal code",
    "wechat": "WeChat", "whatsapp": "WhatsApp", "linkedin": "LinkedIn", "other_contacts": "Other contacts",
    "created_at": "Added",
}


def label(field: str) -> str:
    return LABELS.get(field, field.replace("_", " ").capitalize())


def cell(contact: Contact, field: str) -> str:
    value = getattr(contact, field, "")
    if field == "phones":
        return "; ".join(f"{p.type}: {p.number}" for p in value)
    if isinstance(value, list):
        return "; ".join(value)
    return "" if value is None else str(value)


def _safe(text: str) -> str:
    # Prevent spreadsheet formula injection from card text.
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(contacts: list[Contact], fields: list[str] | None = None) -> bytes:
    fields = fields or EXPORT_FIELDS
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label(f) for f in fields])
    for c in contacts:
        w.writerow([_safe(cell(c, f)) for f in fields])
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel shows Chinese correctly


def to_xlsx(contacts: list[Contact], fields: list[str] | None = None) -> bytes:
    fields = fields or EXPORT_FIELDS
    wb = Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append([label(f) for f in fields])
    for c_ in ws[1]:
        c_.font = Font(bold=True)
    for c in contacts:
        ws.append([_safe(cell(c, f)) for f in fields])
    for i, f in enumerate(fields, start=1):
        width = max([len(label(f))] + [len(cell(c, f)) for c in contacts[:200]] or [10])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max(width + 2, 10), 50)
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
