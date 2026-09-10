"""Penyimpanan data klaim.

SQLite dipakai sebagai sumber kebenaran, bukan Excel. File Excel selalu
di-generate ulang dari tabel ini, sehingga rekap tidak pernah rusak karena
ditulis bersamaan dan bisa dibuat ulang kapan saja untuk periode mana pun.

Alur status satu entri:
    pending_payment  -> user belum pilih Reimbursement / Kartu Kredit
    pending_detail   -> sudah pilih, menunggu kalimat detail dari user
    confirmed        -> lengkap, masuk rekap
    deleted          -> dibatalkan (soft delete, tetap tersimpan untuk audit)
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id  INTEGER NOT NULL,
    user_name         TEXT    NOT NULL,
    period            TEXT    NOT NULL,   -- 'YYYY-MM'
    txn_date          TEXT    NOT NULL,   -- 'YYYY-MM-DD'

    -- Dibaca dari struk
    merchant          TEXT    NOT NULL,   -- mengisi kolom "Lokasi" di form
    alamat            TEXT    NOT NULL DEFAULT '',
    category          TEXT    NOT NULL DEFAULT 'Lain-lain',
    jumlah            REAL    NOT NULL DEFAULT 0,
    currency          TEXT    NOT NULL DEFAULT 'IDR',

    -- Diisi dari kalimat konfirmasi user
    tamu_nama         TEXT    NOT NULL DEFAULT '',
    tamu_posisi       TEXT    NOT NULL DEFAULT '',
    tamu_perusahaan   TEXT    NOT NULL DEFAULT '',
    industri          TEXT    NOT NULL DEFAULT '',
    tipe              TEXT    NOT NULL DEFAULT '',
    tujuan            TEXT    NOT NULL DEFAULT '',
    karyawan_internal TEXT    NOT NULL DEFAULT '',

    payment_type      TEXT,
    photo_path        TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'pending_payment',
    confidence        REAL    NOT NULL DEFAULT 0,
    raw_json          TEXT    NOT NULL DEFAULT '{}',
    created_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expenses_period ON expenses (period, status);
CREATE INDEX IF NOT EXISTS idx_expenses_user   ON expenses (telegram_user_id, status);

-- Mencegah struk yang sama masuk dua kali kalau user tidak sengaja kirim ulang.
CREATE TABLE IF NOT EXISTS photo_hashes (
    sha256      TEXT PRIMARY KEY,
    expense_id  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

-- Karyawan yang boleh memakai bot. Identitas tinggal di sini, bukan di .env,
-- karena tiap orang punya NPK sendiri yang mengisi kepala form Excel-nya.
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    nama             TEXT    NOT NULL,
    username         TEXT    NOT NULL DEFAULT '',
    npk              TEXT    NOT NULL DEFAULT '',
    jabatan          TEXT    NOT NULL DEFAULT '',
    departemen       TEXT    NOT NULL DEFAULT '',
    perusahaan       TEXT    NOT NULL DEFAULT '',
    role             TEXT    NOT NULL DEFAULT 'user',     -- 'admin' | 'user'
    status           TEXT    NOT NULL DEFAULT 'pending',  -- 'pending' | 'active' | 'revoked'
    created_at       TEXT    NOT NULL,
    decided_at       TEXT    NOT NULL DEFAULT '',
    decided_by       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);
"""

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"

ROLE_ADMIN = "admin"
ROLE_USER = "user"

PROFILE_FIELDS = ("npk", "jabatan", "departemen", "perusahaan")

DETAIL_FIELDS = (
    "tamu_nama", "tamu_posisi", "tamu_perusahaan", "industri",
    "tipe", "tujuan", "karyawan_internal",
)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# --- Karyawan ------------------------------------------------------------

def get_user(telegram_user_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
        ).fetchone()


def request_access(telegram_user_id: int, nama: str, username: str = "") -> str:
    """Catat permintaan akses. Kembalikan status karyawan sesudahnya.

    Aman dipanggil berulang: karyawan yang menekan /start dua kali tidak
    menimpa status yang sudah diputuskan admin.
    """
    existing = get_user(telegram_user_id)
    if existing is not None:
        # Nama Telegram bisa berubah; ikutkan tanpa menyentuh status.
        with connect() as conn:
            conn.execute(
                "UPDATE users SET nama = ?, username = ? WHERE telegram_user_id = ?",
                (nama, username, telegram_user_id),
            )
        return existing["status"]

    with connect() as conn:
        conn.execute(
            """INSERT INTO users (telegram_user_id, nama, username, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (telegram_user_id, nama, username, STATUS_PENDING,
             datetime.now().isoformat(timespec="seconds")),
        )
    return STATUS_PENDING


def decide_access(telegram_user_id: int, status: str, by_admin: int) -> None:
    """Setujui, tolak, atau cabut akses. Baris karyawan tidak pernah dihapus."""
    with connect() as conn:
        conn.execute(
            """UPDATE users SET status = ?, decided_at = ?, decided_by = ?
               WHERE telegram_user_id = ?""",
            (status, datetime.now().isoformat(timespec="seconds"),
             by_admin, telegram_user_id),
        )


def set_role(telegram_user_id: int, role: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE telegram_user_id = ?",
            (role, telegram_user_id),
        )


def update_profile(telegram_user_id: int, **fields: Any) -> None:
    """Isi NPK/jabatan/departemen/perusahaan yang mengisi kepala form Excel."""
    updates = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
    if not updates:
        return
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        conn.execute(
            f"UPDATE users SET {assignments} WHERE telegram_user_id = ?",
            (*updates.values(), telegram_user_id),
        )


def list_users(status: str | None = None) -> list[sqlite3.Row]:
    query = "SELECT * FROM users"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, nama"
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def admin_ids() -> list[int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT telegram_user_id FROM users WHERE role = ? AND status = ?",
            (ROLE_ADMIN, STATUS_ACTIVE),
        ).fetchall()
    return [int(r["telegram_user_id"]) for r in rows]


def ensure_admin(telegram_user_id: int, nama: str = "Admin") -> None:
    """Jadikan seseorang admin aktif. Dipakai untuk menyemai admin pertama.

    Tanpa ini, sistem persetujuan menjadi buntu: tidak ada yang berwenang
    menyetujui permintaan akses pertama.
    """
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """INSERT INTO users (telegram_user_id, nama, role, status, created_at, decided_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_user_id) DO UPDATE SET
                   role = excluded.role, status = excluded.status""",
            (telegram_user_id, nama, ROLE_ADMIN, STATUS_ACTIVE, now, now),
        )


def find_by_hash(sha256: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            """SELECT e.* FROM photo_hashes h
               JOIN expenses e ON e.id = h.expense_id
               WHERE h.sha256 = ? AND e.status != 'deleted'""",
            (sha256,),
        ).fetchone()


def find_similar(merchant: str, txn_date: str, jumlah: float,
                 exclude_id: int | None = None) -> sqlite3.Row | None:
    """Cari klaim dengan merchant, tanggal, dan nominal sama.

    Hash foto hanya menangkap file yang identik. Struk yang sama difoto dua kali
    menghasilkan hash berbeda, jadi kecocokan isi inilah yang mencegah satu
    pengeluaran diklaim dua kali.
    """
    query = """SELECT * FROM expenses
               WHERE status != 'deleted'
                 AND lower(merchant) = lower(?)
                 AND txn_date = ?
                 AND abs(jumlah - ?) < 1"""
    params: list[Any] = [merchant, txn_date, jumlah]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    with connect() as conn:
        return conn.execute(query + " ORDER BY id LIMIT 1", params).fetchone()


def insert_expense(data: dict[str, Any], photo_sha256: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO expenses
               (telegram_user_id, user_name, period, txn_date, merchant, alamat,
                category, jumlah, currency, payment_type, photo_path, status,
                confidence, raw_json, created_at)
               VALUES (:telegram_user_id, :user_name, :period, :txn_date, :merchant,
                       :alamat, :category, :jumlah, :currency, :payment_type,
                       :photo_path, :status, :confidence, :raw_json, :created_at)""",
            data,
        )
        expense_id = int(cur.lastrowid)
        conn.execute(
            "INSERT OR REPLACE INTO photo_hashes (sha256, expense_id, created_at) VALUES (?, ?, ?)",
            (photo_sha256, expense_id, datetime.now().isoformat(timespec="seconds")),
        )
    return expense_id


def get_expense(expense_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()


def set_payment_type(expense_id: int, payment_type: str, photo_path: str) -> None:
    """Simpan pilihan R/K beserta lokasi foto barunya, lalu tunggu detail dari user."""
    with connect() as conn:
        conn.execute(
            """UPDATE expenses
               SET payment_type = ?, photo_path = ?, status = 'pending_detail'
               WHERE id = ?""",
            (payment_type, photo_path, expense_id),
        )


def save_details(expense_id: int, detail: dict[str, Any]) -> None:
    """Isi kolom-kolom form dari kalimat user, lalu tandai entri lengkap."""
    fields = {k: v for k, v in detail.items() if k in DETAIL_FIELDS}
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE expenses SET {assignments}, status = 'confirmed' WHERE id = ?",
            (*fields.values(), expense_id),
        )


def update_fields(expense_id: int, **fields: Any) -> None:
    """Update kolom tertentu. Nama kolom dibatasi ke daftar aman."""
    allowed = {
        "merchant", "alamat", "category", "jumlah", "txn_date", "period",
        "payment_type", *DETAIL_FIELDS,
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        conn.execute(
            f"UPDATE expenses SET {assignments} WHERE id = ?",
            (*updates.values(), expense_id),
        )


def soft_delete(expense_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE expenses SET status = 'deleted' WHERE id = ?", (expense_id,))


def awaiting_detail(telegram_user_id: int) -> sqlite3.Row | None:
    """Entri terakhir milik user yang masih menunggu kalimat detail."""
    with connect() as conn:
        return conn.execute(
            """SELECT * FROM expenses
               WHERE telegram_user_id = ? AND status = 'pending_detail'
               ORDER BY id DESC LIMIT 1""",
            (telegram_user_id,),
        ).fetchone()


def list_by_period(period: str, payment_type: str | None = None,
                   telegram_user_id: int | None = None) -> list[sqlite3.Row]:
    """Struk satu periode. Tanpa `telegram_user_id`, semua karyawan ikut."""
    query = "SELECT * FROM expenses WHERE period = ? AND status = 'confirmed'"
    params: list[Any] = [period]
    if payment_type:
        query += " AND payment_type = ?"
        params.append(payment_type)
    if telegram_user_id is not None:
        query += " AND telegram_user_id = ?"
        params.append(telegram_user_id)
    query += " ORDER BY txn_date, id"
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def list_recent(telegram_user_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """SELECT * FROM expenses
               WHERE telegram_user_id = ? AND status != 'deleted'
               ORDER BY id DESC LIMIT ?""",
            (telegram_user_id, limit),
        ).fetchall()


def summary_by_period(period: str,
                      telegram_user_id: int | None = None) -> dict[str, dict[str, float]]:
    query = """SELECT payment_type, COUNT(*) AS n, COALESCE(SUM(jumlah), 0) AS total
               FROM expenses
               WHERE period = ? AND status = 'confirmed'"""
    params: list[Any] = [period]
    if telegram_user_id is not None:
        query += " AND telegram_user_id = ?"
        params.append(telegram_user_id)
    with connect() as conn:
        rows = conn.execute(query + " GROUP BY payment_type", params).fetchall()
    return {r["payment_type"]: {"count": r["n"], "total": r["total"]} for r in rows}


def incomplete_count(telegram_user_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM expenses
               WHERE telegram_user_id = ?
                 AND status IN ('pending_payment', 'pending_detail')""",
            (telegram_user_id,),
        ).fetchone()
    return int(row["n"])
