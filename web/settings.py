"""Setting yang dibutuhkan halaman dashboard.

Dibaca langsung dari environment, tanpa python-dotenv, supaya berkas ini jalan
di serverless Vercel yang tidak punya file .env. Di laptop, `config.py` sudah
memuat .env ke environment lebih dulu, jadi nilainya tetap terbaca.
"""

from __future__ import annotations

import os

PAYMENT_REIMBURSEMENT = "reimbursement"
PAYMENT_CREDIT_CARD = "kartu_kredit"

SHEET_NAMES = {
    PAYMENT_REIMBURSEMENT: "Reimbursement",
    PAYMENT_CREDIT_CARD: "Kartu Kredit",
}

KARYAWAN_NAMA = os.getenv("KARYAWAN_NAMA", "")
KARYAWAN_NPK = os.getenv("KARYAWAN_NPK", "")
KARYAWAN_DEPARTEMEN = os.getenv("KARYAWAN_DEPARTEMEN", "")
PERUSAHAAN = os.getenv("PERUSAHAAN", "")

CURRENCY = os.getenv("CURRENCY", "IDR")
