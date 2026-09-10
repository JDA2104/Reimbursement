"""Konfigurasi terpusat. Semua nilai sensitif dibaca dari environment / file .env."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Lokasi file ---------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

# Folder web/ berisi modul bersama yang juga dipakai serverless Vercel
# (perenderan dashboard, klien Turso, penamaan bulan). Modul-modul itu hanya
# memakai pustaka standar; menaruhnya di path di sini membuat bot dan Vercel
# memakai kode yang sama persis, bukan dua salinan yang bisa melenceng.
sys.path.insert(0, str(BASE_DIR / "web"))
DATA_DIR = BASE_DIR / "data"
PHOTO_DIR = DATA_DIR / "photos"
INBOX_DIR = PHOTO_DIR / "inbox"        # foto baru, sebelum user pilih R atau K
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "reimbursement.db"

# --- Jenis pembayaran ----------------------------------------------------
PAYMENT_REIMBURSEMENT = "reimbursement"
PAYMENT_CREDIT_CARD = "kartu_kredit"

# Nama sheet di Excel.
SHEET_NAMES = {
    PAYMENT_REIMBURSEMENT: "Reimbursement",
    PAYMENT_CREDIT_CARD: "Kartu Kredit",
}

# Folder penyimpanan foto: File R untuk reimbursement, File K untuk kartu kredit.
PHOTO_FOLDERS = {
    PAYMENT_REIMBURSEMENT: PHOTO_DIR / "R",
    PAYMENT_CREDIT_CARD: PHOTO_DIR / "K",
}

for _d in (DATA_DIR, PHOTO_DIR, INBOX_DIR, EXPORT_DIR, *PHOTO_FOLDERS.values()):
    _d.mkdir(parents=True, exist_ok=True)

# --- Kredensial ----------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# --- Akses ---------------------------------------------------------------
def _parse_ids(raw: str) -> set[int]:
    return {int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()}


ALLOWED_USER_IDS = _parse_ids(os.getenv("ALLOWED_TELEGRAM_USER_IDS", ""))

# --- Email ---------------------------------------------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]

# --- Identitas karyawan (mengisi kepala form Excel) ----------------------
KARYAWAN_NAMA = os.getenv("KARYAWAN_NAMA", "")
KARYAWAN_NPK = os.getenv("KARYAWAN_NPK", "")
KARYAWAN_JABATAN = os.getenv("KARYAWAN_JABATAN", "")
KARYAWAN_DEPARTEMEN = os.getenv("KARYAWAN_DEPARTEMEN", "")
PERUSAHAAN = os.getenv("PERUSAHAAN", "")

# Nilai default kolom yang jarang berubah, supaya user tidak perlu mengetiknya tiap kali.
DEFAULT_TIPE = os.getenv("DEFAULT_TIPE", "meal")
DEFAULT_TUJUAN = os.getenv("DEFAULT_TUJUAN", "Maintain/Build Relation")

CURRENCY = os.getenv("CURRENCY", "IDR")

# Jumlah baris kosong yang tetap dicetak di form, meniru template asli.
FORM_MIN_ROWS = int(os.getenv("FORM_MIN_ROWS", "20"))

CATEGORIES = [
    "Restaurant",
    "Hotel",
    "Transportasi",
    "Perlengkapan Kantor",
    "Komunikasi",
    "Kesehatan",
    "Lain-lain",
]

MAX_PHOTO_BYTES = 10 * 1024 * 1024


def missing_settings() -> list[str]:
    """Setting wajib yang belum diisi, dicek saat bot dinyalakan."""
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ALLOWED_TELEGRAM_USER_IDS": ALLOWED_USER_IDS,
    }
    return [name for name, value in required.items() if not value]
