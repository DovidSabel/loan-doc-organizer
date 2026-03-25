import re
import io
import pdfplumber
from pdf2image import convert_from_bytes
import pytesseract


# ── Page classification ──────────────────────────────────────────────────────

def classify_page(text: str) -> str:
    t = text.upper()
    if "ASSIGNMENT" in t and "ORIGINAL OBLIGEE" in t and "NEW OBLIGEE" in t:
        return "assignment"
    if "STATE OF NEW YORK" in t and "NOTARY PUBLIC" in t:
        return "notary"
    if "SCHEDULE A" in t and "OBLIGOR" in t and "PAYEE" in t:
        return "schedule_a"
    if "PROMISSORY NOTE" in t and "FOR VALUE RECEIVED" in t:
        return "promissory_note"
    return "unknown"


# ── Document-number extraction (footer) ─────────────────────────────────────

def extract_doc_number(text: str) -> str | None:
    """
    Footers look like  '3713660v1 005612.0100'.
    We use only the first token (unique per document set) as the key.
    """
    m = re.search(r'(\d{6,8}v\d+)\s+\d{5,7}\.\d{4}', text)
    return m.group(1) if m else None


# ── Field extractors ─────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def extract_assignment_fields(text: str) -> dict:
    f = {}

    # Original Obligee – "as Trustee of the X (the "Original Obligee")"
    m = re.search(
        r'as Trustee of the (.+?)\s*\(the\s*["\u201c]?Original Obligee["\u201d]?\)',
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        f['original_obligee'] = _clean(m.group(1))

    # Obligor – appears as "by Lipa Friedman, as Trustee of the X (the "Obligor")"
    # Use [^()]+ so the match cannot cross into another parenthesised label.
    m = re.search(
        r'by\s+(?:\w+\s+\w+,\s+as\s+Trustee\s+of\s+the\s+)?([^()]+?)\s*\(the\s*["\u201c]?Obligor["\u201d]?\)',
        text, re.IGNORECASE
    )
    if m:
        f['obligor'] = _clean(m.group(1))

    # New Obligee – "convey to X (the "New Obligee")"
    m = re.search(
        r'convey to (.+?)\s*\(the\s*["\u201c]?New Obligee["\u201d]?\)',
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        f['new_obligee'] = _clean(m.group(1))

    # Original principal amount referenced in the assignment
    m = re.search(r'original principal amount of \$([0-9,]+\.?\d*)', text, re.IGNORECASE)
    if m:
        f['original_amount'] = m.group(1).replace(',', '')

    # Original note date
    m = re.search(r'Promissory Note dated (.+?)\s*\(the', text, re.IGNORECASE)
    if m:
        f['note_date'] = _clean(m.group(1))

    return f


def extract_schedule_a_fields(text: str) -> dict:
    f = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for i, line in enumerate(lines):
        # Each row: the label is on the left, value either on same line or next
        if re.match(r'^Obligor\b', line, re.I) and 'obligor' not in f:
            f['obligor'] = _table_val(line, lines, i)
        elif re.match(r'^Payee\b', line, re.I) and 'payee' not in f:
            f['payee'] = _table_val(line, lines, i)
        elif re.match(r'^Principal\b', line, re.I) and 'accumulated' not in line.lower():
            m = re.search(r'\$([0-9,]+\.?\d*)', line)
            if not m and i + 1 < len(lines):
                m = re.search(r'\$([0-9,]+\.?\d*)', lines[i + 1])
            if m:
                f['principal'] = m.group(1).replace(',', '')
        elif re.match(r'^Interest Rate\b', line, re.I):
            m = re.search(r'(\d+\.?\d*%)', line)
            if m:
                f['interest_rate'] = m.group(1)
        elif re.match(r'^Effective Date\b', line, re.I):
            f['effective_date'] = _table_val(line, lines, i)

    return f


def _table_val(line: str, lines: list, idx: int) -> str:
    """Return value after pipe/colon on same line, or from next line."""
    m = re.search(r'[:\|]\s*(.+)', line)
    if m:
        v = m.group(1).strip().strip('|').strip()
        if v:
            return v
    if idx + 1 < len(lines):
        nxt = lines[idx + 1].strip('| \t')
        skip = re.compile(
            r'^(Payee|Principal|Interest|Effective|Accumulated|Interest Due)',
            re.I
        )
        if nxt and not skip.match(nxt):
            return nxt
    return ''


def extract_promissory_note_fields(text: str) -> dict:
    f = {}

    # Dollar amount at the very top of the page
    m = re.search(r'^\s*\$([0-9,]+\.?\d*)', text, re.MULTILINE)
    if m:
        f['amount'] = m.group(1).replace(',', '')

    # Maker – entity right after "FOR VALUE RECEIVED,"
    m = re.search(
        r'FOR VALUE RECEIVED,?\s*(.+?)(?:,\s*(?:a New York|whose address))',
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        f['maker'] = _clean(m.group(1))

    # Payee – entity after "promises to pay to"
    m = re.search(
        r'promises to pay to\s*(.+?)(?:,\s*(?:a New York|whose address))',
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        f['payee'] = _clean(m.group(1))

    # Is this a loan note? (Schreiber Family LLC is the maker)
    maker = f.get('maker', '')
    f['is_loan_note'] = 'Schreiber Family LLC' in maker

    # For loan notes: extract the trust name from the payee string
    if f['is_loan_note']:
        payee = f.get('payee', '')
        m2 = re.search(r'of the (.+?(?:Trust|LLC))', payee, re.IGNORECASE)
        f['payee_trust'] = _clean(m2.group(1)) if m2 else payee

    # For replacement notes: the original amount this note replaces
    m = re.search(
        r'in the original principal amount of \$([0-9,]+\.?\d*)',
        text, re.IGNORECASE
    )
    if m:
        f['replaces_amount'] = m.group(1).replace(',', '')

    # Which original obligee this note references
    m = re.search(r'in favor of the (.+?(?:Trust|LLC))', text, re.IGNORECASE | re.DOTALL)
    if m:
        f['original_obligee_ref'] = _clean(m.group(1))

    return f


# ── Main entry point ─────────────────────────────────────────────────────────

def _ocr_page(pdf_bytes: bytes, page_num: int) -> str:
    """OCR a single page (1-indexed) to avoid loading all pages into memory at once."""
    images = convert_from_bytes(pdf_bytes, dpi=150, first_page=page_num + 1, last_page=page_num + 1)
    return pytesseract.image_to_string(images[0]) if images else ''


def extract_pages_info(pdf_bytes: bytes) -> list[dict]:
    """Return a list of page-info dicts for every page in the PDF."""
    pages = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        raw_texts = [page.extract_text() or '' for page in pdf.pages]

    use_ocr = not any(t.strip() for t in raw_texts[:3])

    for i, embedded_text in enumerate(raw_texts):
        if embedded_text.strip():
            text = embedded_text
        elif use_ocr:
            text = _ocr_page(pdf_bytes, i)
        else:
            text = ''

        ptype = classify_page(text)
        doc_num = extract_doc_number(text)

        if ptype == 'assignment':
            fields = extract_assignment_fields(text)
        elif ptype == 'schedule_a':
            fields = extract_schedule_a_fields(text)
        elif ptype == 'promissory_note':
            fields = extract_promissory_note_fields(text)
        else:
            fields = {}

        pages.append({
            'page_num': i,
            'page_type': ptype,
            'doc_number': doc_num,
            'fields': fields,
            'text': text,
        })
    return pages
