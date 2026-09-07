"""Ekstraksi data struk dari foto menggunakan Claude vision.

Memakai structured output (`messages.parse`) sehingga hasilnya selalu berupa
objek Pydantic yang tervalidasi - tidak perlu parsing teks bebas.
"""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

import anthropic
from PIL import Image
from pydantic import BaseModel, Field

import config

client = anthropic.Anthropic()

SYSTEM_PROMPT = f"""Kamu adalah asisten finance yang membaca struk/nota/invoice untuk keperluan
klaim reimbursement karyawan di Indonesia.

Tugasmu: baca gambar struk dan keluarkan datanya secara akurat.

Aturan:
- Angka rupiah di Indonesia memakai titik sebagai pemisah ribuan ("Rp 125.000" = 125000).
  Jangan pernah menafsirkannya sebagai desimal.
- `total` adalah jumlah akhir yang benar-benar dibayar, sesudah pajak/service/diskon.
- Kalau PPN/pajak tidak tertulis terpisah, isi `tax` dengan 0 - jangan menghitung sendiri.
- Tanggal harus format YYYY-MM-DD. Kalau tanggal tidak terbaca, isi string kosong.
- `category` harus salah satu dari: {", ".join(config.CATEGORIES)}
- `payment_hint`: isi "kartu_kredit" hanya kalau struk jelas menunjukkan pembayaran
  kartu (ada nama bank, 4 digit terakhir kartu, tulisan DEBIT/CREDIT/EDC). Isi
  "reimbursement" kalau jelas tunai/cash. Isi "tidak_jelas" kalau ragu.
- `confidence` 0.0-1.0: seberapa yakin kamu pada angka total dan merchant.
- Kalau gambar ternyata bukan struk/nota/invoice, set `is_receipt` false dan
  jelaskan singkat di `notes`.

Jangan mengarang data yang tidak terlihat di gambar. Lebih baik kosong daripada salah."""


class ReceiptData(BaseModel):
    """Hasil ekstraksi satu struk."""

    is_receipt: bool = Field(description="True jika gambar benar-benar struk/nota/invoice")
    merchant: str = Field(description="Nama toko/vendor/merchant")
    txn_date: str = Field(description="Tanggal transaksi YYYY-MM-DD, kosong jika tidak terbaca")
    description: str = Field(description="Ringkasan singkat isi belanja, maksimal 80 karakter")
    category: str = Field(description="Kategori pengeluaran")
    subtotal: float = Field(description="Jumlah sebelum pajak, 0 jika tidak tertulis")
    tax: float = Field(description="PPN/pajak, 0 jika tidak tertulis terpisah")
    total: float = Field(description="Total akhir yang dibayar")
    currency: str = Field(description="Kode mata uang, contoh IDR")
    payment_hint: str = Field(description="kartu_kredit | reimbursement | tidak_jelas")
    confidence: float = Field(description="Keyakinan 0.0 sampai 1.0")
    notes: str = Field(description="Catatan tambahan atau alasan jika ada yang janggal")


# Format yang diterima Claude vision.
_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def _media_type(path: Path) -> str:
    """Deteksi tipe gambar dari isi file, bukan dari ekstensi namanya."""
    with Image.open(path) as img:
        fmt = img.format
    if fmt not in _MEDIA_TYPES:
        raise ValueError(f"Format gambar tidak didukung: {fmt or 'tidak dikenali'}")
    return _MEDIA_TYPES[fmt]


def extract(photo_path: Path) -> ReceiptData:
    """Baca satu foto struk dan kembalikan datanya sebagai objek tervalidasi."""
    media_type = _media_type(photo_path)
    image_b64 = base64.standard_b64encode(photo_path.read_bytes()).decode("utf-8")

    response = client.messages.parse(
        model=config.CLAUDE_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Baca struk ini dan keluarkan datanya. "
                            f"Hari ini {date.today():%Y-%m-%d} - pakai sebagai acuan "
                            "kalau struk hanya menulis tanggal tanpa tahun."
                        ),
                    },
                ],
            }
        ],
        output_format=ReceiptData,
    )

    data = response.parsed_output

    # Tanggal kosong atau tidak masuk akal -> pakai hari ini, biar entri tetap
    # masuk rekap bulan berjalan dan bisa dikoreksi user lewat /edit.
    if not _valid_date(data.txn_date):
        data.txn_date = date.today().isoformat()

    if data.category not in config.CATEGORIES:
        data.category = "Lain-lain"

    if data.payment_hint not in {"kartu_kredit", "reimbursement", "tidak_jelas"}:
        data.payment_hint = "tidak_jelas"

    return data


def _valid_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        return False
    # Struk dari masa depan atau lebih dari 2 tahun lalu hampir pasti salah baca.
    today = date.today()
    return (today - parsed).days <= 730 and parsed <= today
