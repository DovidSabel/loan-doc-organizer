"""
Grouping logic:

1. Pages that share a document-number belong together as a set.
   A set that contains an Assignment becomes an "assignment set"
   (Assignment + Notary + Schedule A).  A set whose only promissory
   note is a loan note becomes a "loan set".

2. Replacement promissory notes (Maker = the Obligor trust) that have
   no doc-number, or whose doc-number isn't already claimed, are matched
   to an assignment set by:
       (a) maker name overlaps with obligor name  AND
       (b) original_obligee_ref overlaps with original_obligee  AND
       (c) replaces_amount == original_amount  (strongest tie-breaker)

3. Loan notes (Maker = Schreiber Family LLC) are collected into loan
   groups; each loan group may also contain a notary page if one shares
   the same doc-number or is the closest unclaimed notary page.
"""

from collections import defaultdict


# ── Helpers ──────────────────────────────────────────────────────────────────

def _tokens(name: str) -> list[str]:
    """Return meaningful words from a name string."""
    return [w for w in (name or '').split() if len(w) > 3]


def _overlap(a: str, b: str) -> bool:
    """True if any significant token of `a` appears in `b`."""
    return any(tok in b for tok in _tokens(a))


def _find_adjacent_notary(
    target_page: int,
    notary_pool: list[dict],
    max_distance: int = 6,
) -> dict | None:
    if not notary_pool:
        return None
    closest = min(notary_pool, key=lambda p: abs(p['page_num'] - target_page))
    if abs(closest['page_num'] - target_page) <= max_distance:
        return closest
    return None


# ── Main grouper ─────────────────────────────────────────────────────────────

def group_pages(pages_info: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Returns
    -------
    doc_groups  : list of dicts  {original_obligee, obligor, principal,
                                  interest_rate, pages, is_complete}
    loan_groups : list of dicts  {original_obligee, amount, pages}
    """

    # ── Step 1: Bucket pages by document number ──────────────────────────────
    by_doc: dict[str, list] = defaultdict(list)
    no_doc: list = []

    for p in pages_info:
        (by_doc[p['doc_number']] if p['doc_number'] else no_doc).append(p)

    used_page_nums: set[int] = set()

    assignment_sets: list[dict] = []   # will be completed later
    loan_sets:       list[dict] = []   # loan note + optional notary

    for doc_num, pages in by_doc.items():
        has_assignment = any(p['page_type'] == 'assignment' for p in pages)
        prom_pages     = [p for p in pages if p['page_type'] == 'promissory_note']
        has_loan_note  = any(p['fields'].get('is_loan_note') for p in prom_pages)

        if has_assignment:
            apage = next(p for p in pages if p['page_type'] == 'assignment')
            assignment_sets.append({
                'doc_number':      doc_num,
                'pages':           pages,
                'original_obligee': apage['fields'].get('original_obligee', ''),
                'obligor':          apage['fields'].get('obligor', ''),
                'original_amount':  apage['fields'].get('original_amount', ''),
                'note_date':        apage['fields'].get('note_date', ''),
                'matched_note':     None,
            })
            used_page_nums.update(p['page_num'] for p in pages)

        elif has_loan_note:
            for lp in prom_pages:
                if lp['fields'].get('is_loan_note'):
                    other = [p for p in pages if p is not lp]
                    loan_sets.append({
                        'original_obligee': lp['fields'].get('payee_trust', ''),
                        'amount':           lp['fields'].get('amount', ''),
                        'pages':            [lp] + other,
                    })
            used_page_nums.update(p['page_num'] for p in pages)

    # ── Step 2: Separate unclaimed promissory notes ───────────────────────────
    replacement_pool: list[dict] = []
    standalone_loans: list[dict] = []
    free_notaries:    list[dict] = []

    for p in pages_info:
        if p['page_num'] in used_page_nums:
            continue
        if p['page_type'] == 'promissory_note':
            if p['fields'].get('is_loan_note'):
                standalone_loans.append(p)
            else:
                replacement_pool.append(p)
        elif p['page_type'] == 'notary':
            free_notaries.append(p)

    # ── Step 3: Match replacement notes → assignment sets ────────────────────
    unmatched = list(replacement_pool)

    for aset in assignment_sets:
        obligor    = aset['obligor']
        orig_oblig = aset['original_obligee']
        orig_amt   = aset['original_amount']

        # Prefer notes where replaces_amount == original_amount (exact match)
        exact   = [n for n in unmatched if n['fields'].get('replaces_amount') == orig_amt and orig_amt]
        candidates = exact if exact else unmatched

        best = None
        for note in candidates:
            f = note['fields']
            maker    = f.get('maker', '')
            orig_ref = f.get('original_obligee_ref', '')

            if _overlap(obligor, maker) and _overlap(orig_oblig, orig_ref):
                best = note
                break

        if best:
            aset['matched_note'] = best
            unmatched.remove(best)

    # ── Step 4: Build final doc_groups ───────────────────────────────────────
    doc_groups: list[dict] = []

    for aset in assignment_sets:
        pages = list(aset['pages'])
        if aset['matched_note']:
            pages.append(aset['matched_note'])

        sched = next((p for p in pages if p['page_type'] == 'schedule_a'), None)
        principal     = sched['fields'].get('principal', '')     if sched else ''
        interest_rate = sched['fields'].get('interest_rate', '') if sched else ''

        doc_groups.append({
            'original_obligee': aset['original_obligee'],
            'obligor':          aset['obligor'],
            'principal':        principal,
            'interest_rate':    interest_rate,
            'pages':            pages,
            'is_complete':      aset['matched_note'] is not None,
        })

    # ── Step 5: Assemble loan groups ─────────────────────────────────────────
    # Standalone loan notes (no doc-number partner) get a nearby notary page
    for loan_note in standalone_loans:
        f = loan_note['fields']
        notary = _find_adjacent_notary(loan_note['page_num'], free_notaries)
        group_pages = [loan_note]
        if notary:
            group_pages.append(notary)
            free_notaries.remove(notary)

        loan_sets.append({
            'original_obligee': f.get('payee_trust', ''),
            'amount':           f.get('amount', ''),
            'pages':            group_pages,
        })

    return doc_groups, loan_sets
