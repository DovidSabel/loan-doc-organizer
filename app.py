import io
from collections import defaultdict

import streamlit as st

from parser_utils import extract_pages_info
from grouper import group_pages
from exporter import build_zip

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Loan Doc Organizer", layout="wide")
st.title("Loan Document Organizer")
st.caption(
    "Upload a PDF containing Assignment pages, Notary pages, Schedule A pages, "
    "and Promissory Notes.  The app will identify, group, and package every set "
    "into a named PDF inside a folder named for the Original Obligee."
)

# ── Upload ───────────────────────────────────────────────────────────────────
uploaded = st.file_uploader("Choose a PDF", type=["pdf"])

if not uploaded:
    st.stop()

if st.button("Process PDF", type="primary", use_container_width=True):
    pdf_bytes = uploaded.read()

    # ── Extract + classify ────────────────────────────────────────────────────
    with st.spinner("Reading and classifying pages…"):
        progress = st.progress(0)

        # We call extract_pages_info but wrap it so we can show per-page progress.
        # For large files this is important UX feedback.
        import pdfplumber
        from parser_utils import (
            classify_page, extract_doc_number,
            extract_assignment_fields, extract_schedule_a_fields,
            extract_promissory_note_fields,
        )

        pages_info = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                text  = page.extract_text() or ''
                ptype = classify_page(text)
                dnum  = extract_doc_number(text)

                if ptype == 'assignment':
                    fields = extract_assignment_fields(text)
                elif ptype == 'schedule_a':
                    fields = extract_schedule_a_fields(text)
                elif ptype == 'promissory_note':
                    fields = extract_promissory_note_fields(text)
                else:
                    fields = {}

                pages_info.append({
                    'page_num':   i,
                    'page_type':  ptype,
                    'doc_number': dnum,
                    'fields':     fields,
                    'text':       text,
                })
                progress.progress((i + 1) / total,
                                  text=f"Page {i + 1}/{total} — {ptype}")
        progress.empty()

    # ── Group ─────────────────────────────────────────────────────────────────
    with st.spinner("Grouping documents…"):
        doc_groups, loan_groups = group_pages(pages_info)

    # ── Summary metrics ───────────────────────────────────────────────────────
    st.divider()
    type_counts: dict[str, int] = defaultdict(int)
    for p in pages_info:
        type_counts[p['page_type']] += 1

    cols = st.columns(len(type_counts) + 2)
    for (pt, cnt), col in zip(type_counts.items(), cols):
        col.metric(pt.replace('_', ' ').title(), cnt)
    cols[-2].metric("Document groups",  len(doc_groups))
    cols[-1].metric("Loan documents",   len(loan_groups))

    # ── Document groups preview ───────────────────────────────────────────────
    st.divider()
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader(f"Document Groups ({len(doc_groups)})")

        by_folder: dict[str, list] = defaultdict(list)
        for g in doc_groups:
            by_folder[g['original_obligee'] or 'Unknown'].append(g)

        for folder, groups in sorted(by_folder.items()):
            with st.expander(f"📁  {folder}  ({len(groups)} file{'s' if len(groups) != 1 else ''})"):
                for g in groups:
                    n       = len(g['pages'])
                    status  = "✅" if g['is_complete'] else "⚠️ incomplete"
                    rate    = g['interest_rate']
                    princ   = f"${g['principal']}" if g['principal'] else '—'
                    st.markdown(
                        f"**{g['obligor'] or '?'}**  "
                        f"&nbsp;·&nbsp; {n} pages &nbsp;·&nbsp; "
                        f"{princ} @ {rate}  "
                        f"&nbsp;·&nbsp; {status}"
                    )

    with col_right:
        st.subheader(f"Loan Documents ({len(loan_groups)})")
        by_folder_loan: dict[str, list] = defaultdict(list)
        for lg in loan_groups:
            by_folder_loan[lg['original_obligee'] or 'Unknown'].append(lg)

        for folder, loans in sorted(by_folder_loan.items()):
            with st.expander(f"📁  {folder}  ({len(loans)} note{'s' if len(loans) != 1 else ''})"):
                for lg in loans:
                    amt = f"${lg['amount']}" if lg['amount'] else '—'
                    n   = len(lg['pages'])
                    st.markdown(f"💵 {amt} &nbsp;·&nbsp; {n} pages")

    # ── Unmatched pages warning ───────────────────────────────────────────────
    matched_nums: set[int] = set()
    for g in doc_groups:
        for p in g['pages']:
            matched_nums.add(p['page_num'])
    for lg in loan_groups:
        for p in lg['pages']:
            matched_nums.add(p['page_num'])

    unmatched = [p for p in pages_info if p['page_num'] not in matched_nums]
    if unmatched:
        st.warning(
            f"⚠️  {len(unmatched)} page(s) could not be matched into any group."
        )
        with st.expander("View unmatched pages"):
            for p in unmatched:
                st.write(
                    f"Page {p['page_num'] + 1} &nbsp;—&nbsp; "
                    f"`{p['page_type']}` &nbsp;|&nbsp; "
                    f"doc# `{p['doc_number'] or 'none'}`"
                )

    # ── Incomplete groups warning ─────────────────────────────────────────────
    incomplete = [g for g in doc_groups if not g['is_complete']]
    if incomplete:
        st.warning(
            f"⚠️  {len(incomplete)} group(s) are missing a replacement "
            "Promissory Note (only 3 pages instead of 4)."
        )

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    with st.spinner("Building ZIP archive…"):
        zip_bytes = build_zip(doc_groups, loan_groups, pdf_bytes)

    st.download_button(
        label="⬇️  Download Organized ZIP",
        data=zip_bytes,
        file_name="organized_docs.zip",
        mime="application/zip",
        use_container_width=True,
    )
