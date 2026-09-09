"""Perapian foto struk dan penyusunan lembar lampiran.

Dua pekerjaan:
  - `autocrop`            : potong struk dari latar (tangan, meja, keyboard),
                            luruskan perspektifnya, cerahkan supaya enak dibaca
  - `build_contact_sheet` : susun semua struk satu periode ke dalam PDF,
                            berurutan tanggal, diberi nomor sesuai baris di form

Deteksi struk memakai pendekatan klasik: cari bidang terang terbesar yang
berbentuk segi empat, lalu koreksi perspektif. Kalau tidak ketemu bentuk yang
meyakinkan, jatuh ke pemotongan kotak biasa - lebih baik hasil apa adanya
daripada memotong struknya sendiri.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
import db
from excel_report import period_label

# --- Ukuran lembar A4 pada 150 dpi ---------------------------------------
DPI = 150
A4_W = int(8.27 * DPI)   # 1240 px
A4_H = int(11.69 * DPI)  # 1754 px
MARGIN = int(0.4 * DPI)
GAP = int(0.15 * DPI)
COLS = 3
LABEL_H = 26

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --- Pemotongan struk ----------------------------------------------------

# Kertas struk hampir tidak berwarna, sedangkan kulit tangan dan meja kayu
# selalu punya saturasi tinggi. Memisahkan lewat saturasi jauh lebih andal
# daripada lewat kecerahan - lantai kayu terang ikut terbaca sebagai "putih".
SAT_MAX = 60
VAL_MIN = 110
WORK_SIZE = 700

# Baris yang selebar hampir seluruh frame biasanya dinding atau meja, bukan
# struk; yang terlalu sempit biasanya jari. Ambil yang di antaranya.
ROW_MIN_FRAC = 0.12
ROW_MAX_FRAC = 0.70
COL_COVER_FRAC = 0.25
PAD_FRAC = 0.03


def _paper_mask(small: np.ndarray) -> np.ndarray | None:
    """Mask komponen kertas terbesar pada gambar yang sudah diperkecil."""
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] < SAT_MAX) & (hsv[:, :, 2] > VAL_MIN)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count < 2:
        return None
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == largest).astype(np.uint8)


def _longest_run(rows: np.ndarray, gap: int = 5) -> tuple[int, int] | None:
    """Rentang baris bersambung terpanjang, mengabaikan putus kecil."""
    if len(rows) == 0:
        return None
    best = start = prev = int(rows[0])
    best_range = (start, start)
    for r in rows[1:]:
        r = int(r)
        if r - prev > gap:
            if prev - start > best_range[1] - best_range[0]:
                best_range = (start, prev)
            start = r
        prev = r
    if prev - start > best_range[1] - best_range[0]:
        best_range = (start, prev)
    return best_range if best_range[1] > best_range[0] else None


def _paper_box(small: np.ndarray) -> tuple[int, int, int, int] | None:
    """Kotak struk pada gambar kecil, atau None kalau tidak meyakinkan."""
    comp = _paper_mask(small)
    if comp is None:
        return None

    height, width = comp.shape
    widths = comp.sum(axis=1)
    candidates = np.where(
        (widths > width * ROW_MIN_FRAC) & (widths < width * ROW_MAX_FRAC)
    )[0]

    span = _longest_run(candidates)
    if span is None:
        return None
    y0, y1 = span
    if y1 - y0 < height * 0.15:
        return None

    cols = np.where(comp[y0:y1].sum(axis=0) > (y1 - y0) * COL_COVER_FRAC)[0]
    if len(cols) == 0:
        return None
    x0, x1 = int(cols[0]), int(cols[-1])
    if x1 - x0 < width * 0.05:
        return None

    # Sedikit ruang di tepi supaya pinggiran struk tidak ikut terpotong.
    pad_x = int(width * PAD_FRAC)
    pad_y = int(height * PAD_FRAC)
    return (
        max(0, x0 - pad_x), max(0, y0 - pad_y),
        min(width, x1 + pad_x), min(height, y1 + pad_y),
    )


def autocrop(src: Path) -> Image.Image:
    """Potong struk dari latarnya. Selalu mengembalikan gambar, apa pun hasilnya."""
    data = np.fromfile(str(src), dtype=np.uint8)   # tahan path non-ASCII di Windows
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Tidak bisa membaca gambar: {src}")

    height, width = image.shape[:2]
    scale = WORK_SIZE / max(height, width)
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image.copy()

    box = _paper_box(small)
    if box is not None:
        inv = 1 / scale if scale < 1 else 1
        x0, y0, x1, y1 = (int(v * inv) for v in box)
        cropped = image[y0:min(y1, height), x0:min(x1, width)]
        if cropped.size and cropped.shape[0] > 40 and cropped.shape[1] > 40:
            image = cropped

    # Naikkan kontras supaya tulisan tetap terbaca setelah diperkecil di lembar.
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, a, b = cv2.split(lab)
    lightness = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
    image = cv2.cvtColor(cv2.merge((lightness, a, b)), cv2.COLOR_LAB2BGR)

    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


# --- Lembar lampiran -----------------------------------------------------

def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fmt_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    return f"{d.day:02d}-{MONTH_ABBR[d.month - 1]}-{d:%y}"


def _label(row: sqlite3.Row, index: int, prefix: str) -> str:
    return (f"{prefix}{index:02d} · {_fmt_date(row['txn_date'])} · "
            f"{row['merchant']} · Rp {row['jumlah']:,.0f}".replace(",", "."))


def _cell_width(count: int) -> int:
    cols = min(COLS, max(1, count))
    return (A4_W - 2 * MARGIN - (cols - 1) * GAP) // cols


def _scaled(entries: list[tuple[Image.Image, str]]) -> list[tuple[Image.Image, str]]:
    """Perkecil tiap struk agar pas selebar kolom, tetap menjaga proporsinya."""
    cell_w = _cell_width(len(entries))
    max_h = A4_H - 2 * MARGIN - LABEL_H
    out = []
    for img, label in entries:
        thumb = img.copy()
        thumb.thumbnail((cell_w, max_h), Image.LANCZOS)
        out.append((thumb, label))
    return out


def _paginate(entries: list[tuple[Image.Image, str]]) -> list[list[tuple[Image.Image, str]]]:
    """Bagi struk ke halaman-halaman, diisi baris demi baris sampai penuh.

    Tinggi tiap baris mengikuti struk tertinggi di baris itu, jadi halaman terisi
    rapat dari atas alih-alih meregangkan sel ke seluruh tinggi kertas.
    """
    cols = min(COLS, max(1, len(entries)))
    usable = A4_H - 2 * MARGIN

    pages: list[list[tuple[Image.Image, str]]] = []
    current: list[tuple[Image.Image, str]] = []
    used = 0

    for start in range(0, len(entries), cols):
        row = entries[start:start + cols]
        row_h = max(img.height for img, _ in row) + LABEL_H
        extra = row_h + (GAP if current else 0)
        if current and used + extra > usable:
            pages.append(current)
            current, used = [], 0
            extra = row_h
        current.extend(row)
        used += extra

    if current:
        pages.append(current)
    return pages


def _render_page(entries: list[tuple[Image.Image, str]], title: str) -> Image.Image:
    """Gambar satu halaman A4 berisi struk yang sudah diskalakan."""
    page = Image.new("RGB", (A4_W, A4_H), "white")
    draw = ImageDraw.Draw(page)
    draw.text((MARGIN, MARGIN - 34), title, fill="black", font=_font(20))

    cols = min(COLS, max(1, len(entries)))
    cell_w = _cell_width(len(entries))
    label_font = _font(13)

    y = MARGIN
    for start in range(0, len(entries), cols):
        row = entries[start:start + cols]
        row_h = max(img.height for img, _ in row) + LABEL_H

        for j, (thumb, label) in enumerate(row):
            x0 = MARGIN + j * (cell_w + GAP)
            page.paste(thumb, (x0 + (cell_w - thumb.width) // 2, y + LABEL_H))
            draw.rectangle([x0, y, x0 + cell_w, y + row_h], outline="#BBBBBB", width=1)
            draw.text((x0 + 4, y + 5), label, fill="black", font=label_font)

        y += row_h + GAP

    return page


def build_contact_sheet(period: str) -> Path | None:
    """Bangun PDF berisi semua struk satu periode, berurutan tanggal.

    Sheet Reimbursement dan Kartu Kredit dipisah, tiap halaman memuat 9 struk.
    Nomor pada label sama dengan nomor baris di form Excel.
    """
    pages: list[Image.Image] = []

    for payment_type, sheet_name in config.SHEET_NAMES.items():
        rows = db.list_by_period(period, payment_type)   # sudah urut tanggal
        if not rows:
            continue

        prefix = "R-" if payment_type == config.PAYMENT_REIMBURSEMENT else "K-"
        entries: list[tuple[Image.Image, str]] = []

        for index, row in enumerate(rows, start=1):
            photo = Path(row["photo_path"])
            if not photo.exists():
                continue
            try:
                cropped = autocrop(photo)
            except Exception:
                cropped = Image.open(photo).convert("RGB")
            entries.append((cropped, _label(row, index, prefix)))

        chunks = _paginate(_scaled(entries))
        for page_no, chunk in enumerate(chunks, start=1):
            title = (f"Lampiran Struk — {sheet_name} — {period_label(period)}"
                     f"   (hal. {page_no}/{len(chunks)})")
            pages.append(_render_page(chunk, title))

    if not pages:
        return None

    out_path = config.EXPORT_DIR / f"Lampiran_Struk_{period}.pdf"
    pages[0].save(
        out_path, "PDF", resolution=DPI, save_all=True, append_images=pages[1:]
    )
    return out_path
