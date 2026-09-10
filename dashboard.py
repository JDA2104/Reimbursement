"""Mini dashboard tracking pengeluaran, dibaca dari SQLite.

Menghasilkan satu file HTML mandiri - tanpa server, tanpa koneksi internet,
tanpa pustaka eksternal. Cukup dibuka di browser. Datanya sensitif (nominal
dan merchant asli), jadi sengaja tidak diunggah ke mana pun.
"""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path

import config
import db
from excel_report import period_label

# Dua deret saja: Reimbursement dan Kartu Kredit. Warna diambil dari slot
# kategorikal 1 dan 2, sudah divalidasi lolos seluruh gate CVD di mode terang
# maupun gelap (worst adjacent Delta E 24.7 terang / 26.8 gelap).
SERIES = [
    (config.PAYMENT_REIMBURSEMENT, "Reimbursement", "#2a78d6", "#3987e5"),
    (config.PAYMENT_CREDIT_CARD, "Kartu Kredit", "#eb6834", "#d95926"),
]

CHART_W = 720
CHART_H = 260
PAD_L = 74
PAD_R = 16
PAD_T = 16
PAD_B = 40
BAR_MAX_W = 68
SEG_GAP = 2       # celah permukaan antar segmen tumpukan
CORNER = 4        # ujung data membulat


def rupiah(value: float) -> str:
    return f"Rp {value:,.0f}".replace(",", ".")


def _short(value: float) -> str:
    """Angka ringkas untuk sumbu: 3.996.000 -> 4,0 jt."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".replace(".", ",") + " jt"
    if value >= 1_000:
        return f"{value / 1_000:.0f} rb"
    return f"{value:.0f}"


def _nice_max(value: float) -> float:
    """Batas atas sumbu yang bulat, supaya garis bantu jatuh di angka enak."""
    if value <= 0:
        return 1_000_000
    step = 10 ** (len(str(int(value))) - 1)
    for mult in (1, 2, 2.5, 5, 10):
        if step * mult >= value:
            return step * mult
    return step * 10


# --- Pengambilan data ----------------------------------------------------

def _monthly(months: int) -> list[dict]:
    """Total per bulan per jenis pembayaran, urut lama -> baru."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT period, payment_type, COUNT(*) AS n, SUM(jumlah) AS total
               FROM expenses WHERE status = 'confirmed'
               GROUP BY period, payment_type ORDER BY period""",
        ).fetchall()

    periods: dict[str, dict] = {}
    for row in rows:
        entry = periods.setdefault(
            row["period"],
            {"period": row["period"], "total": 0.0, "count": 0,
             **{key: 0.0 for key, *_ in SERIES}},
        )
        entry[row["payment_type"]] = row["total"] or 0.0
        entry["total"] += row["total"] or 0.0
        entry["count"] += row["n"]

    return sorted(periods.values(), key=lambda e: e["period"])[-months:]


def _per_user() -> list[dict]:
    """Rekap per karyawan per bulan - inti dari 'tracking user'."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT user_name, period, payment_type,
                      COUNT(*) AS n, SUM(jumlah) AS total
               FROM expenses WHERE status = 'confirmed'
               GROUP BY user_name, period, payment_type
               ORDER BY user_name, period DESC""",
        ).fetchall()

    combined: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["user_name"], row["period"])
        entry = combined.setdefault(
            key,
            {"user": row["user_name"], "period": row["period"],
             "count": 0, "total": 0.0, **{k: 0.0 for k, *_ in SERIES}},
        )
        entry[row["payment_type"]] = row["total"] or 0.0
        entry["count"] += row["n"]
        entry["total"] += row["total"] or 0.0

    return sorted(combined.values(), key=lambda e: (e["user"], e["period"]), reverse=True)


def _entries(period: str) -> list[dict]:
    rows = db.list_by_period(period)
    return [
        {
            "id": r["id"], "tanggal": r["txn_date"], "merchant": r["merchant"],
            "kategori": r["category"], "tamu": r["tamu_nama"],
            "jenis": config.SHEET_NAMES.get(r["payment_type"], "-"),
            "jumlah": r["jumlah"], "user": r["user_name"],
        }
        for r in sorted(rows, key=lambda r: r["txn_date"], reverse=True)
    ]


def _incomplete() -> int:
    with db.connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM expenses
               WHERE status IN ('pending_payment', 'pending_detail')"""
        ).fetchone()
    return int(row["n"])


# --- Grafik --------------------------------------------------------------

def _bar_chart(monthly: list[dict]) -> str:
    """Kolom bertumpuk: satu kolom per bulan, dipecah jenis pembayaran."""
    if not monthly:
        return '<p class="empty">Belum ada data.</p>'

    top = _nice_max(max(m["total"] for m in monthly))
    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = CHART_H - PAD_T - PAD_B
    slot = plot_w / len(monthly)
    bar_w = min(BAR_MAX_W, slot * 0.55)

    parts: list[str] = []

    # Garis bantu resesif + label sumbu
    for i in range(5):
        value = top * i / 4
        y = PAD_T + plot_h - (plot_h * i / 4)
        parts.append(
            f'<line class="grid" x1="{PAD_L}" y1="{y:.1f}" '
            f'x2="{CHART_W - PAD_R}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{PAD_L - 10}" y="{y + 4:.1f}" '
            f'text-anchor="end">{_short(value)}</text>'
        )

    for index, month in enumerate(monthly):
        cx = PAD_L + slot * (index + 0.5)
        x = cx - bar_w / 2
        y_cursor = PAD_T + plot_h

        # Tumpuk dari bawah; segmen paling atas yang mendapat ujung membulat.
        stack = [(key, label, month[key]) for key, label, *_ in SERIES if month[key] > 0]
        for pos, (key, label, value) in enumerate(stack):
            height = plot_h * value / top
            if height <= 0:
                continue
            gap = SEG_GAP if pos < len(stack) - 1 else 0
            y = y_cursor - height
            radius = CORNER if pos == len(stack) - 1 else 0
            parts.append(
                f'<rect class="bar" data-series="{key}" x="{x:.1f}" y="{y:.1f}" '
                f'width="{bar_w:.1f}" height="{max(height - gap, 1):.1f}" '
                f'rx="{radius}" fill="var(--{key})" '
                f'data-tip="{html.escape(label)} · {html.escape(rupiah(value))}" '
                f'data-month="{html.escape(period_label(month["period"]))}"/>'
            )
            y_cursor = y

        # Label langsung di atas kolom - angka tidak ditempel di tiap segmen.
        parts.append(
            f'<text class="value" x="{cx:.1f}" y="{y_cursor - 8:.1f}" '
            f'text-anchor="middle">{_short(month["total"])}</text>'
        )
        parts.append(
            f'<text class="axis" x="{cx:.1f}" y="{CHART_H - PAD_B + 20:.1f}" '
            f'text-anchor="middle">{html.escape(period_label(month["period"]))}</text>'
        )

    parts.append(
        f'<line class="baseline" x1="{PAD_L}" y1="{PAD_T + plot_h}" '
        f'x2="{CHART_W - PAD_R}" y2="{PAD_T + plot_h}"/>'
    )

    return (
        f'<svg viewBox="0 0 {CHART_W} {CHART_H}" role="img" '
        f'aria-label="Pengeluaran per bulan menurut jenis pembayaran">'
        + "".join(parts) + "</svg>"
    )


# --- Perakitan halaman ---------------------------------------------------

def _stat(label: str, value: str, note: str = "", swatch: str = "") -> str:
    dot = f'<span class="dot" style="background:{swatch}"></span>' if swatch else ""
    sub = f'<div class="note">{html.escape(note)}</div>' if note else ""
    return (f'<div class="stat"><div class="label">{dot}{html.escape(label)}</div>'
            f'<div class="value">{html.escape(value)}</div>{sub}</div>')


def build_dashboard(months: int = 6, period: str | None = None) -> Path:
    """Bangun file HTML dashboard. Kembalikan lokasinya."""
    period = period or date.today().strftime("%Y-%m")
    monthly = _monthly(months)
    per_user = _per_user()
    entries = _entries(period)
    incomplete = _incomplete()

    this_month = next((m for m in monthly if m["period"] == period), None)
    total_now = this_month["total"] if this_month else 0.0
    count_now = this_month["count"] if this_month else 0

    prev = [m for m in monthly if m["period"] < period]
    delta_html = ""
    if prev and prev[-1]["total"] > 0:
        change = (total_now - prev[-1]["total"]) / prev[-1]["total"] * 100
        arrow = "▲" if change >= 0 else "▼"
        cls = "up" if change >= 0 else "down"
        delta_html = (f'<span class="delta {cls}">{arrow} {abs(change):.0f}% '
                      f'vs {html.escape(period_label(prev[-1]["period"]))}</span>')

    stats = [
        _stat("Jumlah struk", str(count_now), f"{len(per_user)} baris rekap"),
        _stat("Reimbursement", rupiah(this_month[config.PAYMENT_REIMBURSEMENT]) if this_month else "Rp 0",
              swatch="var(--reimbursement)"),
        _stat("Kartu Kredit", rupiah(this_month[config.PAYMENT_CREDIT_CARD]) if this_month else "Rp 0",
              swatch="var(--kartu_kredit)"),
    ]
    if incomplete:
        stats.append(_stat("Belum lengkap", str(incomplete), "struk menunggu detail"))

    user_rows = "".join(
        f'<tr><td>{html.escape(u["user"])}</td>'
        f'<td>{html.escape(period_label(u["period"]))}</td>'
        f'<td class="num">{u["count"]}</td>'
        f'<td class="num">{html.escape(rupiah(u[config.PAYMENT_REIMBURSEMENT]))}</td>'
        f'<td class="num">{html.escape(rupiah(u[config.PAYMENT_CREDIT_CARD]))}</td>'
        f'<td class="num strong">{html.escape(rupiah(u["total"]))}</td></tr>'
        for u in per_user
    ) or '<tr><td colspan="6" class="empty">Belum ada data.</td></tr>'

    entry_rows = "".join(
        f'<tr><td class="mono">#{e["id"]}</td><td>{html.escape(e["tanggal"])}</td>'
        f'<td>{html.escape(e["merchant"])}</td>'
        f'<td>{html.escape(e["tamu"] or "—")}</td>'
        f'<td>{html.escape(e["jenis"])}</td>'
        f'<td class="num strong">{html.escape(rupiah(e["jumlah"]))}</td></tr>'
        for e in entries
    ) or '<tr><td colspan="6" class="empty">Belum ada struk bulan ini.</td></tr>'

    legend = "".join(
        f'<span class="key"><span class="dot" style="background:var(--{key})"></span>'
        f'{html.escape(label)}</span>'
        for key, label, *_ in SERIES
    )

    identitas = " · ".join(
        p for p in (config.KARYAWAN_NAMA, config.KARYAWAN_NPK,
                    config.KARYAWAN_DEPARTEMEN, config.PERUSAHAAN) if p
    )

    page = TEMPLATE.format(
        period_label=html.escape(period_label(period)),
        identitas=html.escape(identitas) or "—",
        hero=html.escape(rupiah(total_now)),
        delta=delta_html,
        stats="".join(stats),
        legend=legend,
        chart=_bar_chart(monthly),
        user_rows=user_rows,
        entry_rows=entry_rows,
        generated=date.today().isoformat(),
        months=len(monthly),
        series_json=json.dumps({k: label for k, label, *_ in SERIES}),
    )

    out_path = config.EXPORT_DIR / f"Dashboard_{period}.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path


TEMPLATE = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard Reimbursement — {period_label}</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb;
    --plane: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --muted: #898781;
    --grid: #e1e0d9;
    --baseline: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --reimbursement: #2a78d6;
    --kartu_kredit: #eb6834;
    --good: #006300;
    --critical: #d03b3b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --surface-1: #1a1a19;
      --plane: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --muted: #898781;
      --grid: #2c2c2a;
      --baseline: #383835;
      --border: rgba(255,255,255,0.10);
      --reimbursement: #3987e5;
      --kartu_kredit: #d95926;
      --good: #0ca30c;
      --critical: #e66767;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 28px 20px 56px;
    background: var(--plane); color: var(--text-primary);
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 940px; margin: 0 auto; }}
  header {{ margin-bottom: 24px; }}
  h1 {{ font-size: 19px; margin: 0 0 4px; font-weight: 600; }}
  .sub {{ color: var(--text-secondary); font-size: 13px; }}
  .card {{
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px; margin-bottom: 16px;
  }}
  .hero-row {{ display: flex; flex-wrap: wrap; gap: 28px; align-items: flex-end; }}
  .hero .cap {{ color: var(--text-secondary); font-size: 13px; margin-bottom: 2px; }}
  .hero .fig {{ font-size: 40px; font-weight: 600; letter-spacing: -0.02em; }}
  .delta {{ font-size: 13px; font-weight: 500; margin-left: 10px; }}
  .delta.up {{ color: var(--critical); }}
  .delta.down {{ color: var(--good); }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 26px; margin-left: auto; }}
  .stat .label {{ color: var(--text-secondary); font-size: 12px; margin-bottom: 3px;
                  display: flex; align-items: center; gap: 6px; }}
  .stat .value {{ font-size: 17px; font-weight: 600; }}
  .stat .note {{ color: var(--muted); font-size: 11px; margin-top: 2px; }}
  .dot {{ width: 9px; height: 9px; border-radius: 2px; display: inline-block; }}
  h2 {{ font-size: 14px; font-weight: 600; margin: 0 0 2px; }}
  .hint {{ color: var(--muted); font-size: 12px; margin: 0 0 14px; }}
  .legend {{ display: flex; gap: 16px; margin-bottom: 10px; }}
  .key {{ display: flex; align-items: center; gap: 6px;
          font-size: 12px; color: var(--text-secondary); }}
  svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
  .grid {{ stroke: var(--grid); stroke-width: 1; }}
  .baseline {{ stroke: var(--baseline); stroke-width: 1; }}
  .tick, .axis {{ fill: var(--muted); font-size: 11px; }}
  .value {{ fill: var(--text-secondary); font-size: 11px; font-weight: 600; }}
  .bar {{ cursor: pointer; transition: opacity .12s; }}
  .bar:hover {{ opacity: .82; }}
  .scroll {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; font-weight: 600; color: var(--text-secondary);
        font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--border);
        white-space: nowrap; }}
  td {{ padding: 9px 10px; border-bottom: 1px solid var(--grid); }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .strong {{ font-weight: 600; }}
  .mono {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
  .empty {{ color: var(--muted); text-align: center; padding: 22px; }}
  footer {{ color: var(--muted); font-size: 11px; text-align: center; margin-top: 22px; }}
  #tip {{
    position: fixed; pointer-events: none; opacity: 0; transition: opacity .1s;
    background: var(--text-primary); color: var(--surface-1);
    padding: 6px 9px; border-radius: 6px; font-size: 12px; white-space: nowrap; z-index: 9;
  }}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>Dashboard Reimbursement</h1>
    <div class="sub">{identitas}</div>
  </header>

  <div class="card">
    <div class="hero-row">
      <div class="hero">
        <div class="cap">Total {period_label}</div>
        <div class="fig">{hero}{delta}</div>
      </div>
      <div class="stats">{stats}</div>
    </div>
  </div>

  <div class="card">
    <h2>Pengeluaran per bulan</h2>
    <p class="hint">{months} bulan terakhir, dipecah menurut jenis pembayaran.</p>
    <div class="legend">{legend}</div>
    {chart}
  </div>

  <div class="card">
    <h2>Rekap per karyawan</h2>
    <p class="hint">Total klaim tiap karyawan, dipecah per bulan.</p>
    <div class="scroll">
      <table>
        <thead><tr>
          <th>Karyawan</th><th>Bulan</th><th class="num">Struk</th>
          <th class="num">Reimbursement</th><th class="num">Kartu Kredit</th>
          <th class="num">Total</th>
        </tr></thead>
        <tbody>{user_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Struk {period_label}</h2>
    <p class="hint">Urut dari yang terbaru.</p>
    <div class="scroll">
      <table>
        <thead><tr>
          <th>ID</th><th>Tanggal</th><th>Merchant</th>
          <th>Dijamu</th><th>Jenis</th><th class="num">Jumlah</th>
        </tr></thead>
        <tbody>{entry_rows}</tbody>
      </table>
    </div>
  </div>

  <footer>Dibuat {generated} dari data lokal · tidak diunggah ke mana pun</footer>
</div>

<div id="tip"></div>
<script>
  // Tooltip melayang untuk tiap segmen kolom.
  const tip = document.getElementById('tip');
  document.querySelectorAll('.bar').forEach(bar => {{
    bar.addEventListener('mousemove', e => {{
      tip.textContent = bar.dataset.month + ' — ' + bar.dataset.tip;
      tip.style.opacity = 1;
      tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
      tip.style.top = (e.clientY - 34) + 'px';
    }});
    bar.addEventListener('mouseleave', () => tip.style.opacity = 0);
  }});
</script>
</body>
</html>
"""
