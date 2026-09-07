"""Konfigurasi terpusat. Semua nilai sensitif dibaca dari environment / file .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Lokasi file ---------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PHOTO_DIR = DATA_DIR / "photos"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "reimbursement.db"

for _d in (DATA_DIR, PHOTO_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Kredensial ----------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")

# --- Akses ---------------------------------------------------------------
# Daftar telegram_user_id yang boleh memakai bot, dipisah koma.
# Sengaja kosong secara default: bot menolak semua orang sampai diisi.
def _parse_ids(raw: str) -> set[int]:
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}


ALLOWED_USER_IDS = _parse_ids(os.getenv("ALLOWED_TELEGRAM_USER_IDS", ""))

# --- Email ---------------------------------------------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
# Penerima rekap (petugas yang mengumpulkan semua). Boleh lebih dari satu, dipisah koma.
EMAIL_TO = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]

# --- Bisnis --------------------------------------------------------------
CURRENCY = os.getenv("CURRENCY", "IDR")
COMPANY_NAME = os.getenv("COMPANY_NAME", "")

# Dua jenis pembayaran = dua sheet di Excel.
PAYMENT_REIMBURSEMENT = "reimbursement"
PAYMENT_CREDIT_CARD = "kartu_kredit"

SHEET_NAMES = {
    PAYMENT_REIMBURSEMENT: "Reimbursement",
    PAYMENT_CREDIT_CARD: "Kartu Kredit",
}

CATEGORIES = [
    "Transportasi",
    "Akomodasi",
    "Makan & Entertain",
    "Perlengkapan Kantor",
    "Komunikasi",
    "Kesehatan",
    "Pelatihan & Seminar",
    "Lain-lain",
]

# Batas ukuran foto yang diterima (Telegram sendiri membatasi ~20 MB).
MAX_PHOTO_BYTES = 10 * 1024 * 1024


def missing_settings() -> list[str]:
    """Kembalikan daftar setting wajib yang belum diisi, untuk dicek saat startup."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        missing.append("ANTHROPIC_API_KEY")
    if not ALLOWED_USER_IDS:
        missing.append("ALLOWED_TELEGRAM_USER_IDS")
    return missing
