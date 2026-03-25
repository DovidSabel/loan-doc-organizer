import io
import re
import zipfile
from collections import defaultdict

from pypdf import PdfReader, PdfWriter


def _sanitize(name: str) -> str:
    """Strip characters that are illegal in file/folder names."""
    return re.sub(r'[<>:"/\\|?*]', '_', name or '').strip() or 'Unknown'


def build_zip(
    doc_groups: list[dict],
    loan_groups: list[dict],
    pdf_bytes: bytes,
) -> bytes:
    """
    Build a ZIP archive with the following layout:

        <Original Obligee>/
            <Obligor>.pdf                   (4-page group)
            <Obligor>_<principal>.pdf       (when same obligor has >1 note)
            LOAN_NOTE_<amount>.pdf          (loan note + optional notary)

    Returns the raw ZIP bytes.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    # ── Count how many files share the same (folder, obligor) pair ──────────
    pair_count: dict[tuple, int] = defaultdict(int)
    for g in doc_groups:
        folder  = _sanitize(g['original_obligee'])
        obligor = _sanitize(g['obligor'])
        pair_count[(folder, obligor)] += 1

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:

        # ── Document groups ──────────────────────────────────────────────────
        written: dict[tuple, int] = defaultdict(int)

        for g in doc_groups:
            folder  = _sanitize(g['original_obligee'])
            obligor = _sanitize(g['obligor'])
            key     = (folder, obligor)

            if pair_count[key] > 1:
                # Disambiguate with principal amount
                principal = g.get('principal', '').replace(',', '') or str(written[key])
                filename  = f"{obligor}_{principal}.pdf"
            else:
                filename = f"{obligor}.pdf"

            written[key] += 1

            sorted_pages = sorted(g['pages'], key=lambda p: p['page_num'])
            writer = PdfWriter()
            for pi in sorted_pages:
                writer.add_page(reader.pages[pi['page_num']])

            buf = io.BytesIO()
            writer.write(buf)
            zf.writestr(f"{folder}/{filename}", buf.getvalue())

        # ── Loan note groups ─────────────────────────────────────────────────
        loan_count: dict[str, int] = defaultdict(int)

        for lg in loan_groups:
            folder = _sanitize(lg['original_obligee'])
            amount = lg.get('amount', '').replace(',', '') or 'unknown'
            idx    = loan_count[folder]
            suffix = f"_{idx}" if idx > 0 else ''
            filename = f"LOAN_NOTE_{amount}{suffix}.pdf"
            loan_count[folder] += 1

            sorted_pages = sorted(lg['pages'], key=lambda p: p['page_num'])
            writer = PdfWriter()
            for pi in sorted_pages:
                writer.add_page(reader.pages[pi['page_num']])

            buf = io.BytesIO()
            writer.write(buf)
            zf.writestr(f"{folder}/{filename}", buf.getvalue())

    return zip_buf.getvalue()
