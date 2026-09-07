"""Pembuat rekap Excel bulanan.

Menghasilkan satu file .xlsx berisi dua sheet - "Reimbursement" dan "Kartu Kredit" -
dengan thumbnail struk ter-embed di tiap baris, plus satu file .zip berisi foto
resolusi penuh untuk lampiran email.
"""

from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image

import config
import db

# Ukuran thumbnail di dalam sel (piksel).
THUMB_WIDTH = 90
THUMB_HEIGHT = 120

HEADERS = [
    ("No", 6),
    ("Tanggal", 12),
    ("Merchant", 26),
    ("Deskripsi", 32),
    ("Kategori", 20),
    ("Subtotal", 14),
    ("PPN", 12),
    ("Total", 16),
    ("Pemohon", 18),
    ("Struk", 15),
]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
TOTAL_FILL = PatternFill("solid", fgColor="D9E2F3")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

MONTH_NAMES = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def period_label(period: str) -> str:
    """'2026-09' -> 'September 2026'."""
    year, month = period.split("-")
    return f"{MONTH_NAMES[int(month) - 1]} {year}"


def _money_format(currency: str) -> str:
    # Rupiah tidak memakai desimal; mata uang lain pakai dua angka di belakang koma.
    return '#,##0' if currency.upper() == "IDR" else '#,##0.00'


def _thumbnail(photo_path: Path) -> XLImage | None:
    """Buat thumbnail kecil dari foto struk untuk disisipkan ke sel Excel."""
    if not photo_path.exists():
        return None
    try:
        with Image.open(photo_path) as img:
            img = img.convert("RGB")
            img.thumbnail((THUMB_WIDTH, THUMB_HEIGHT))
            buffer = BytesIO()
            img.save(buffer, format="PNG")
    except OSError:
        return None
    buffer.seek(0)
    return XLImage(buffer)


def _build_sheet(wb: Workbook, title: str, rows: list[sqlite3.Row], period: str) -> float:
    """Isi satu sheet dan kembalikan total nominalnya."""
    ws = wb.create_sheet(title=title)
    currency = rows[0]["currency"] if rows else config.CURRENCY
    money = _money_format(currency)

    # Judul
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEADERS))
    header_text = f"Rekap {title} - {period_label(period)}"
    if config.COMPANY_NAME:
        header_text = f"{config.COMPANY_NAME}\n{header_text}"
    title_cell = ws.cell(row=1, column=1, value=header_text)
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 40

    # Header kolom
    header_row = 3
    for col, (label, width) in enumerate(HEADERS, start=1):
        cell = ws.cell(row=header_row, column=col, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[header_row].height = 22

    total_sum = 0.0
    for idx, row in enumerate(rows, start=1):
        r = header_row + idx
        # Tinggi baris disesuaikan tinggi thumbnail (1 pt ~ 1.33 px).
        ws.row_dimensions[r].height = THUMB_HEIGHT * 0.78

        values = [
            idx,
            row["txn_date"],
            row["merchant"],
            row["description"],
            row["category"],
            row["subtotal"],
            row["tax"],
            row["total"],
            row["user_name"],
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=value)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=col in (3, 4))
            if col in (6, 7, 8):
                cell.number_format = money
            elif col in (1, 2):
                cell.alignment = Alignment(horizontal="center", vertical="center")

        photo_cell = ws.cell(row=r, column=len(HEADERS))
        photo_cell.border = BORDER
        thumb = _thumbnail(Path(row["photo_path"]))
        if thumb is not None:
            ws.add_image(thumb, f"{get_column_letter(len(HEADERS))}{r}")
        else:
            photo_cell.value = "(foto hilang)"
            photo_cell.alignment = Alignment(horizontal="center", vertical="center")

        total_sum += float(row["total"] or 0)

    # Baris total
    total_row = header_row + len(rows) + 1
    ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True)
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=7)
    label_cell = ws.cell(row=total_row, column=1)
    label_cell.alignment = Alignment(horizontal="right", vertical="center")
    label_cell.fill = TOTAL_FILL

    total_cell = ws.cell(row=total_row, column=8, value=total_sum)
    total_cell.font = Font(bold=True, size=12)
    total_cell.number_format = money
    total_cell.fill = TOTAL_FILL
    total_cell.border = BORDER

    for col in range(1, len(HEADERS) + 1):
        ws.cell(row=total_row, column=col).fill = TOTAL_FILL

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    if rows:
        ws.auto_filter.ref = (
            f"A{header_row}:{get_column_letter(len(HEADERS))}{header_row + len(rows)}"
        )
    return total_sum


def build_workbook(period: str) -> Path:
    """Bangun ulang file Excel untuk satu periode. Selalu menimpa file lama."""
    wb = Workbook()
    wb.remove(wb.active)  # buang sheet default kosong

    totals: dict[str, float] = {}
    for payment_type, sheet_name in config.SHEET_NAMES.items():
        rows = db.list_by_period(period, payment_type)
        totals[sheet_name] = _build_sheet(wb, sheet_name, rows, period)

    wb.properties.title = f"Rekap Pengeluaran {period_label(period)}"
    wb.properties.created = datetime.now()

    out_path = config.EXPORT_DIR / f"Rekap_Pengeluaran_{period}.xlsx"
    wb.save(out_path)
    return out_path


def build_photo_archive(period: str) -> Path | None:
    """Kumpulkan foto struk resolusi penuh untuk satu periode ke dalam satu .zip.

    Nama file di dalam zip dibuat deskriptif (sheet/tanggal_merchant_id) supaya
    penerima email bisa mencocokkannya dengan baris di Excel.
    """
    out_path = config.EXPORT_DIR / f"Struk_{period}.zip"
    written = 0

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for payment_type, sheet_name in config.SHEET_NAMES.items():
            for idx, row in enumerate(db.list_by_period(period, payment_type), start=1):
                photo = Path(row["photo_path"])
                if not photo.exists():
                    continue
                safe_merchant = "".join(
                    c for c in row["merchant"] if c.isalnum() or c in " -_"
                ).strip()[:40] or "struk"
                arcname = (
                    f"{sheet_name}/{idx:03d}_{row['txn_date']}_{safe_merchant}"
                    f"_id{row['id']}{photo.suffix}"
                )
                zf.write(photo, arcname)
                written += 1

    if written == 0:
        out_path.unlink(missing_ok=True)
        return None
    return out_path


def build_all(period: str) -> tuple[Path, Path | None]:
    """Bangun Excel dan arsip foto sekaligus. Dipakai oleh /excel dan /kirim."""
    return build_workbook(period), build_photo_archive(period)
