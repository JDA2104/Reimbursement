"""Pembacaan struk dan pemilahan data lewat OpenAI.

Dua fungsi utama:
  - `extract_receipt`  : foto struk  -> tanggal, merchant, alamat, kategori, jumlah
  - `parse_details`    : satu kalimat bebas dari user -> kolom-kolom form nominatif

Keduanya memakai structured output, jadi hasilnya selalu objek Pydantic yang
tervalidasi - tidak ada parsing teks bebas di sisi kita.
"""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

from openai import OpenAI
from PIL import Image
from pydantic import BaseModel, Field

import config

client = OpenAI(api_key=config.OPENAI_API_KEY)

# Format gambar yang diterima OpenAI vision.
_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


class ReceiptData(BaseModel):
    """Data yang bisa dibaca langsung dari lembar struk."""

    is_receipt: bool = Field(description="True jika gambar benar-benar struk/nota/invoice")
    merchant: str = Field(description="Nama tempat/merchant, contoh: Remboelan, Pagi Sore")
    alamat: str = Field(description="Kota atau area alamat merchant, contoh: Jakarta Pusat")
    txn_date: str = Field(description="Tanggal transaksi YYYY-MM-DD, kosong jika tidak terbaca")
    category: str = Field(description="Kategori merchant")
    jumlah: float = Field(description="Total akhir yang dibayar")
    currency: str = Field(description="Kode mata uang, contoh IDR")
    payment_hint: str = Field(description="kartu_kredit | reimbursement | tidak_jelas")
    confidence: float = Field(description="Keyakinan pada merchant dan jumlah, 0.0 sampai 1.0")
    notes: str = Field(description="Catatan kalau ada yang janggal, atau alasan bukan struk")


class EntertainmentDetail(BaseModel):
    """Kolom form nominatif yang tidak tertulis di struk, diisi dari kalimat user."""

    tamu_nama: str = Field(description="Nama orang yang dijamu, pisahkan dengan koma jika lebih dari satu")
    tamu_posisi: str = Field(description="Jabatan/posisi tamu, contoh: Executive")
    tamu_perusahaan: str = Field(description="Perusahaan tamu, contoh: JA Solar")
    industri: str = Field(description="Bidang industri perusahaan tamu, contoh: Pendidikan, Energy")
    tipe: str = Field(description="Jenis jamuan, contoh: meal")
    tujuan: str = Field(description="Tujuan jamuan, contoh: Maintain/Build Relation")
    karyawan_internal: str = Field(description="Karyawan internal yang ikut, pisahkan dengan koma")
    lokasi_override: str = Field(description="Nama lokasi jika user menyebut lokasi berbeda dari struk, selain itu kosong")
    alamat_override: str = Field(description="Alamat jika user menyebutkannya, selain itu kosong")


RECEIPT_PROMPT = f"""Kamu membaca struk/nota/invoice Indonesia untuk keperluan klaim biaya karyawan.

Aturan:
- Angka rupiah memakai titik sebagai pemisah ribuan ("Rp 125.000" = 125000).
  JANGAN pernah menafsirkan titik itu sebagai desimal.
- `jumlah` adalah total akhir yang benar-benar dibayar, sesudah pajak dan service charge.
- `merchant` adalah nama tempatnya saja, tanpa embel-embel cabang atau PT.
- `alamat` cukup kota atau area yang tertulis di struk, contoh "Jakarta Pusat".
  Kosongkan kalau tidak tertulis - jangan menebak.
- `txn_date` format YYYY-MM-DD. Kosongkan kalau tidak terbaca.
- `category` harus salah satu dari: {", ".join(config.CATEGORIES)}
- `payment_hint`: isi "kartu_kredit" hanya kalau struk jelas menunjukkan pembayaran
  kartu (nama bank, 4 digit terakhir kartu, tulisan DEBIT/CREDIT/EDC).
  Isi "reimbursement" kalau jelas tunai. Isi "tidak_jelas" kalau ragu.
- Kalau gambar bukan struk, set `is_receipt` false dan jelaskan di `notes`.

Jangan mengarang data yang tidak terlihat. Lebih baik kosong daripada salah."""


DETAIL_PROMPT = f"""Kamu memilah kalimat bebas dari karyawan menjadi kolom form
Daftar Nominatif Entertainment (form pajak Indonesia untuk biaya jamuan).

User menulis satu pesan singkat, contoh:
  "Chen Yifei sama Huang Xinmin dari JA Solar, Executive, industri pendidikan,
   yang ikut Ariadi Alex Janu Galih"

Tugasmu memecahnya ke kolom yang benar.

Aturan:
- Beberapa nama tamu digabung dengan koma dalam satu string.
- `tipe` default "{config.DEFAULT_TIPE}" kalau user tidak menyebut jenis jamuan lain.
- `tujuan` default "{config.DEFAULT_TUJUAN}" kalau user tidak menyebut tujuan lain.
- `karyawan_internal` adalah karyawan kantor sendiri yang ikut hadir - bukan tamu.
- `lokasi_override` dan `alamat_override` HANYA diisi kalau user menyebut lokasi
  atau alamat secara eksplisit. Kalau tidak, kosongkan - data dari struk yang dipakai.
- Kalau suatu kolom tidak disebut user sama sekali, kosongkan. Jangan mengarang.
- Pertahankan ejaan nama orang dan perusahaan persis seperti yang ditulis user."""


def _data_url(path: Path) -> str:
    """Baca gambar jadi data URL base64 untuk dikirim ke OpenAI vision."""
    with Image.open(path) as img:
        fmt = img.format
    if fmt not in _MEDIA_TYPES:
        raise ValueError(f"Format gambar tidak didukung: {fmt or 'tidak dikenali'}")
    b64 = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{_MEDIA_TYPES[fmt]};base64,{b64}"


def extract_receipt(photo_path: Path) -> ReceiptData:
    """Baca satu foto struk, kembalikan datanya sebagai objek tervalidasi."""
    completion = client.beta.chat.completions.parse(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": RECEIPT_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Baca struk ini. Hari ini "
                            f"{date.today():%Y-%m-%d} - pakai sebagai acuan kalau "
                            "struk hanya menulis tanggal tanpa tahun."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url(photo_path), "detail": "high"},
                    },
                ],
            },
        ],
        response_format=ReceiptData,
    )

    data = completion.choices[0].message.parsed
    if data is None:
        raise ValueError("Model tidak mengembalikan data terstruktur")

    # Tanggal kosong / tidak masuk akal -> pakai hari ini, biar entri tetap masuk
    # rekap bulan berjalan dan bisa dikoreksi lewat /edit.
    if not _valid_date(data.txn_date):
        data.txn_date = date.today().isoformat()

    if data.category not in config.CATEGORIES:
        data.category = "Lain-lain"

    if data.payment_hint not in {"kartu_kredit", "reimbursement", "tidak_jelas"}:
        data.payment_hint = "tidak_jelas"

    return data


def parse_details(user_text: str) -> EntertainmentDetail:
    """Ubah satu kalimat bebas dari user menjadi kolom-kolom form."""
    completion = client.beta.chat.completions.parse(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": DETAIL_PROMPT},
            {"role": "user", "content": user_text},
        ],
        response_format=EntertainmentDetail,
    )

    detail = completion.choices[0].message.parsed
    if detail is None:
        raise ValueError("Model tidak mengembalikan data terstruktur")

    if not detail.tipe.strip():
        detail.tipe = config.DEFAULT_TIPE
    if not detail.tujuan.strip():
        detail.tujuan = config.DEFAULT_TUJUAN

    return detail


def _valid_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        return False
    # Struk dari masa depan atau lebih dari 2 tahun lalu hampir pasti salah baca.
    today = date.today()
    return parsed <= today and (today - parsed).days <= 730
