"""Klien Turso (SQLite terkelola) lewat HTTP API.

Sengaja memakai `urllib` biasa, bukan driver libsql: satu berkas ini jalan
apa adanya di laptop maupun di serverless Vercel, tanpa menambah dependency
yang harus dikompilasi di kedua sisi.

Pembagian tugasnya:
  - Bot menulis ke SQLite lokal dulu (cepat, tetap jalan tanpa internet),
    lalu `sync_from_local()` menyalin barisnya ke Turso.
  - Dashboard di Vercel hanya membaca lewat `fetch_rows()`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()

TIMEOUT = 20

# Skema di cloud sengaja dibuat sama persis dengan yang lokal, supaya query
# dashboard tidak perlu ditulis dua versi.
CLOUD_SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id                INTEGER PRIMARY KEY,
    telegram_user_id  INTEGER NOT NULL,
    user_name         TEXT    NOT NULL,
    period            TEXT    NOT NULL,
    txn_date          TEXT    NOT NULL,
    merchant          TEXT    NOT NULL,
    alamat            TEXT    NOT NULL DEFAULT '',
    category          TEXT    NOT NULL DEFAULT 'Lain-lain',
    jumlah            REAL    NOT NULL DEFAULT 0,
    currency          TEXT    NOT NULL DEFAULT 'IDR',
    tamu_nama         TEXT    NOT NULL DEFAULT '',
    tamu_posisi       TEXT    NOT NULL DEFAULT '',
    tamu_perusahaan   TEXT    NOT NULL DEFAULT '',
    industri          TEXT    NOT NULL DEFAULT '',
    tipe              TEXT    NOT NULL DEFAULT '',
    tujuan            TEXT    NOT NULL DEFAULT '',
    karyawan_internal TEXT    NOT NULL DEFAULT '',
    payment_type      TEXT,
    status            TEXT    NOT NULL DEFAULT 'pending_payment',
    created_at        TEXT    NOT NULL
)
"""

# Kolom yang ikut disalin. `photo_path` dan `raw_json` sengaja TIDAK ikut -
# dashboard tidak membutuhkannya, dan jalur file lokal tidak ada artinya di cloud.
SYNC_COLUMNS = (
    "id", "telegram_user_id", "user_name", "period", "txn_date", "merchant",
    "alamat", "category", "jumlah", "currency", "tamu_nama", "tamu_posisi",
    "tamu_perusahaan", "industri", "tipe", "tujuan", "karyawan_internal",
    "payment_type", "status", "created_at",
)


class CloudNotConfigured(RuntimeError):
    """TURSO_DATABASE_URL atau TURSO_AUTH_TOKEN belum diisi."""


def is_configured() -> bool:
    return bool(TURSO_URL and TURSO_TOKEN)


def _endpoint() -> str:
    """Ubah URL bergaya libsql:// menjadi endpoint HTTPS."""
    url = TURSO_URL
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return url.rstrip("/") + "/v2/pipeline"


def _encode(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _decode(cell: dict[str, Any]) -> Any:
    kind = cell.get("type")
    if kind == "null":
        return None
    raw = cell.get("value")
    if kind == "integer":
        return int(raw)
    if kind == "float":
        return float(raw)
    return raw


def execute(statements: list[tuple[str, list[Any]]]) -> list[list[dict[str, Any]]]:
    """Jalankan beberapa statement dalam satu perjalanan HTTP.

    Kembalikan satu daftar baris (berupa dict) per statement.
    """
    if not is_configured():
        raise CloudNotConfigured(
            "TURSO_DATABASE_URL dan TURSO_AUTH_TOKEN belum diisi di environment."
        )

    payload = {
        "requests": [
            {"type": "execute",
             "stmt": {"sql": sql, "args": [_encode(a) for a in args]}}
            for sql, args in statements
        ] + [{"type": "close"}]
    }

    request = urllib.request.Request(
        _endpoint(),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TURSO_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = json.loads(response.read().decode("utf-8"))

    out: list[list[dict[str, Any]]] = []
    for item in body.get("results", []):
        if item.get("type") == "error":
            message = item.get("error", {}).get("message", "galat tidak diketahui")
            raise RuntimeError(f"Turso menolak query: {message}")
        response_body = item.get("response", {})
        if response_body.get("type") != "execute":
            continue
        result = response_body.get("result", {})
        names = [c.get("name") for c in result.get("cols", [])]
        out.append([
            dict(zip(names, (_decode(cell) for cell in row)))
            for row in result.get("rows", [])
        ])
    return out


def init_schema() -> None:
    execute([(CLOUD_SCHEMA, [])])


def fetch_rows() -> list[dict[str, Any]]:
    """Semua baris untuk dashboard - dipakai oleh serverless function."""
    results = execute([(
        "SELECT * FROM expenses WHERE status != 'deleted' ORDER BY txn_date DESC, id DESC",
        [],
    )])
    return results[0] if results else []


def sync_from_local(rows: list[dict[str, Any]]) -> int:
    """Timpa isi tabel cloud dengan baris dari SQLite lokal.

    Lokal adalah sumber kebenaran, jadi penyalinan ini satu arah dan
    idempoten: baris yang sama dikirim ulang akan menimpa dirinya sendiri.
    """
    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in SYNC_COLUMNS)
    columns = ", ".join(SYNC_COLUMNS)
    sql = f"INSERT OR REPLACE INTO expenses ({columns}) VALUES ({placeholders})"

    statements: list[tuple[str, list[Any]]] = [(CLOUD_SCHEMA, [])]
    statements += [(sql, [row.get(c) for c in SYNC_COLUMNS]) for row in rows]

    # Baris yang sudah dihapus di lokal harus hilang juga dari cloud.
    keep = ", ".join(str(int(r["id"])) for r in rows)
    statements.append((f"DELETE FROM expenses WHERE id NOT IN ({keep})", []))

    execute(statements)
    return len(rows)
