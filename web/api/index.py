"""Serverless function Vercel: render dashboard dari database cloud.

Hanya membaca. Penulisan tetap dilakukan bot di laptop, yang menyalin barisnya
ke Turso setelah tiap entri selesai.
"""

from __future__ import annotations

import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Modul bersama ada satu tingkat di atas folder api/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cloud            # noqa: E402
from render import render, summarize   # noqa: E402

ERROR_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Dashboard belum siap</title>
<style>
 body {{ font: 15px/1.6 system-ui, sans-serif; max-width: 640px;
        margin: 15vh auto; padding: 0 24px; color: #0b0b0b; }}
 h1 {{ font-size: 18px; }}
 pre {{ background: #f4f4f2; padding: 12px 14px; border-radius: 8px;
        overflow-x: auto; font-size: 13px; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background: #0d0d0d; color: #fff; }}
   pre {{ background: #1c1c1a; }}
 }}
</style>
<h1>Dashboard belum bisa menampilkan data</h1>
<p>{pesan}</p>
<pre>{detail}</pre>
"""


def _page() -> tuple[int, str]:
    if not cloud.is_configured():
        return 503, ERROR_PAGE.format(
            pesan=("Environment variable <code>TURSO_DATABASE_URL</code> dan "
                   "<code>TURSO_AUTH_TOKEN</code> belum diisi di project Vercel."),
            detail="Settings → Environment Variables → tambahkan keduanya → Redeploy.",
        )

    try:
        rows = cloud.fetch_rows()
    except Exception:
        return 502, ERROR_PAGE.format(
            pesan="Gagal membaca database cloud.",
            detail=traceback.format_exc(limit=3),
        )

    if not rows:
        return 200, ERROR_PAGE.format(
            pesan=("Database cloud masih kosong. Jalankan <code>/sync</code> "
                   "di bot Telegram untuk mengirim data ke sini."),
            detail="",
        )

    return 200, render(summarize(rows), live=True)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:      # noqa: N802  (nama wajib dari BaseHTTPRequestHandler)
        status, body = _page()
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        # Data finansial - jangan disimpan proxy atau cache browser.
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)
