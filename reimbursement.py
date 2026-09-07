"""
Reimbursement Agent
===================
Template awal untuk proyek agen reimbursement.

TODO:
- Definisikan input (foto/PDF struk, form pengeluaran)
- Ekstraksi data (tanggal, merchant, jumlah, kategori)
- Validasi terhadap kebijakan perusahaan
- Output ringkasan + status approval
"""

from dataclasses import dataclass


@dataclass
class ReimbursementItem:
    date: str
    merchant: str
    amount: float
    category: str
    note: str = ""


def process_receipt(path: str) -> ReimbursementItem:
    """Ekstrak data dari sebuah struk. (belum diimplementasikan)"""
    raise NotImplementedError


def validate(item: ReimbursementItem) -> bool:
    """Cek item terhadap kebijakan reimbursement. (belum diimplementasikan)"""
    raise NotImplementedError


def main() -> None:
    print("Reimbursement agent - template. Isi logikamu di sini.")


if __name__ == "__main__":
    main()
