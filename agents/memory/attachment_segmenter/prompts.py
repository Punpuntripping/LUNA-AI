"""Segmenter prompt — the ONLY copy (edit here, never a .md).

**v5.0** (owner, 2026-11-12) — the value rule is ONE principle instead of per-label
arrows: `core` = everything worth investigating and searching on for the case;
`supporting` = only appendix-like data that holds nothing to investigate, never on a
guess; when in doubt `core`; tables that carry the decision or the figures in issue
stay `core`. `metadata` / `boilerplate` keep their mechanical rules. The user's own
messages are part of the input ("What the user said") and can only raise a passage
to `core`. Labels (what each part IS) are Phase 0's v4.4 labels minus the value
arrows; v4.4 itself is in ``experiments/run_flash_segmenter.py`` for comparison.

Production additions: split mode (``_PART_COVERAGE`` + ``CHUNK_NOTE``, from
``experiments/run_split.py``) and the user-message builders.

English instructions, Arabic output (``what``, ``name``, ``why``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .paragraphs import Paragraph, pid, render_numbered

#: Stored on ``attachment_segment_maps.prompt_version``; part of the helper's version
#: (``agents/helpers/registry.py``), so a bump re-runs every item.
PROMPT_VERSION = "seg-v5.1"

# ---------------------------------------------------------------------------------------------
# Labels per doc_type — what each part of the document IS. The value comes from the rule in
# PROMPT, never from the label. hearing_minutes = judgment + attendance (D4).
# ---------------------------------------------------------------------------------------------
LABELS = {
    "judgment": (
        "- `header` — court, circuit, case / deed numbers, dates, the parties table, the opening formula\n"
        "- `claim` — what the plaintiff / prosecutor (or their representative) SAYS: facts as they tell them, arguments, replies, comments on the expert report, the prosecutor's investigation summary and list of evidence (الأدلة والقرائن) and the prosecutor's own «الأسباب»\n"
        "- `claim_requests` — what the plaintiff / prosecutor ASKS the court to rule: the requests (original, amended, alternative, final); in a criminal case the charge (التهمة / الاتهام) and the punishments requested\n"
        "- `defense` — what the defendant / their lawyer SAYS to argue: arguments, replies, rebuttals, comments on the expert report, the explanation and legal basis of each plea\n"
        "- `defense_pleas` — what the defendant ASKS the court to rule: the defendant's requests (dismissal, non-acceptance, rejection…) and the sentence that FIRST raises each plea (دفع) — «أدفع بعدم…», «يدفع موكلي بـ…»: jurisdiction, standing, limitation / non-hearing «عدم سماع», res judicata «سبق الفصل», prematurity, nullity\n"
        "- `answers` — a party's direct answer to a question from the court or the investigator about the facts or the charge: what they admit, confess or deny (إقرار / اعتراف / إنكار), including a defendant's statements in the investigation\n"
        "- `evidence` — what came from OUTSIDE the parties: witness testimony, an expert's or laboratory's findings, figures and conclusions, the content of documents as the court cites them\n"
        "- `proceedings` — what the court did procedurally: sessions, adjournments, notices, referrals, appointing an expert and the expert's mission, the court's questions (the answers are `answers`), closing the pleadings\n"
        "- `grounds` — the court's OWN reasoning: how it weighs the evidence, its answer to each plea and request, the rules it applies\n"
        "- `ruling` — the operative decision: what the court rules, orders, awards or dismisses, amounts, costs and fees\n"
        "- `objection` — how and by when the ruling can be objected to / appealed, and whether it is final\n"
        "- `signatures` — names and signatures of the judges, clerks, parties"
    ),
    "expert_report": (
        "- `header` — report title / number, court, circuit, case and appointment numbers, the expert's name, firm and licence, dates, the parties\n"
        "- `assignment` — the mandate: the questions the court put to the expert\n"
        "- `method` — site visits, meetings held, documents reviewed, the methodology and the technical principles the expert explains before applying them\n"
        "- `parties_positions` — what each party told or submitted to the expert\n"
        "- `findings` — the expert's OWN observations and analysis, item by item, including the facts the expert establishes (contract value, amounts paid, dates, completion rates, delays, defects found)\n"
        "- `calculations` — tables and computations that produce amounts, quantities, percentages or responsibility shares\n"
        "- `conclusion` — the expert's answer to each question, the final opinion, final figures, recommendations\n"
        "- `photos` — photo captions and image placeholders\n"
        "- `attachment` — the documents attached to the report (the assignment letter, meeting minutes, the contract examined, an insurance policy, invoices…)\n"
        "- `signatures` — the expert's signature block and stamps"
    ),
    "contract": (
        "- `header_parties` — title, contract number, date and place, the parties' data blocks and representatives\n"
        "- `recitals_definitions` — preamble, recitals, definitions, the list of contract documents\n"
        "- `scope` — the subject of the contract and the works / services / goods\n"
        "- `price_payment` — price, rates, installments and their conditions, retention, deductions, VAT, bank account for payment\n"
        "- `duration_schedule` — start, duration, end, milestones, extensions, delay\n"
        "- `obligations` — what each party must do\n"
        "- `penalties_termination` — delay penalties and fines and their caps, termination and its grounds, notice / cure periods, effects of termination, compensation\n"
        "- `guarantees_insurance` — guarantees, bonds, warranties, defects liability, insurance\n"
        "- `force_majeure` — force majeure, suspension, excused delay\n"
        "- `disputes_jurisdiction` — governing law, amicable settlement, the competent court or arbitration\n"
        "- `general_terms` — notices, confidentiality, conflict of interest, assignment, amendments in writing, entire agreement, number of copies\n"
        "- `signatures` — signature boxes, stamps, witnesses\n"
        "- An annex, amendment or change order (ملحق، أمر تغيير): its title / number / date / parties → `header_parties`; its clauses and form fields → the contract labels above (`scope`, `price_payment`, `duration_schedule`, …) — never one label for its whole body\n"
        "- An invoice, quotation, payment claim or receipt: the financial labels — `header_account`, `instrument_terms`, `totals_balances`, `notable_transactions`, `transactions` (routine lines), `terms_notes`, `signatures_stamps`; a quotation's methodology / team / timeline text → `other:quotation_text`\n"
        "- A letter, email or notice: the letter labels — `header`, `subject`, `body`, `demands_deadlines`, `attachments_list`, `signatures` (incl. email footers, disclaimers and the header lines of a quoted earlier email)"
    ),
    "memo": (
        "- `header_parties` — court, case number, the judgment or decision concerned, the parties and their capacities, subject line, date\n"
        "- `facts` — the narrative of the facts and of the procedure so far\n"
        "- `requests` — every request the party makes in the body (original, alternative)\n"
        "- `arguments` — each distinct argument, plea, defence or ground of objection the party relies on, with the facts and legal text it cites, and each point-by-point reply to the other side\n"
        "- `legal_basis` — a separate list or quotation of articles, precedents or principles\n"
        "- `evidence_cited` — the list of exhibits / evidence relied on\n"
        "- `closing_requests` — the final requests («لذا نطلب…», «الطلبات»)\n"
        "- `signatures` — closing formula, name, signature, licence\n"
        "- `attachment` — exhibits attached after the memo"
    ),
    "reference": (
        "- `title_issuance` — title, issuing instrument (royal decree, decision, circular number), dates, gazette\n"
        "- `definitions` — defined terms\n"
        "- `provisions` — substantive articles / rules / principles (المبدأ)\n"
        "- `procedures` — procedural articles, steps, deadlines\n"
        "- `penalties` — penalties, fines, sanctions\n"
        "- `transitional_final` — entry into force, repeal, transitional and executive provisions\n"
        "- `explanatory` — preambles, explanatory notes, commentary, tables of contents\n"
        "- `annex` — forms, schedules, appendices"
    ),
    "letter": (
        "- `header` — sender, addressee, date, reference number of each letter\n"
        "- `subject` — the subject line\n"
        "- `body` — the facts and background the letter states, references to earlier letters and to the contract\n"
        "- `demands_deadlines` — what the letter demands, warns of, notifies or decides: a demand to pay or perform, a warning / إنذار, a notice of breach, suspension, termination or legal action, a deadline or grace period, an approval, rejection, acceptance or settlement offer, an amount claimed or acknowledged\n"
        "- `attachments_list` — the list of enclosures\n"
        "- `signatures` — greetings, closing formulas, signature, stamp, email footers and disclaimers, the header lines of a quoted earlier email"
    ),
    "financial": (
        "- `header_account` — issuer, holder, account / loan / note / invoice number, period\n"
        "- `instrument_terms` — face amount, due date, installment plan, interest or fees, payee, place of payment\n"
        "- `transactions` — the routine line items of a regular schedule\n"
        "- `notable_transactions` — rows that stand out: missed, late, returned, reversed, unusually large, settlement, write-off\n"
        "- `totals_balances` — totals paid / disbursed / outstanding, opening and closing balances, status\n"
        "- `terms_notes` — footnotes, conditions\n"
        "- `signatures_stamps` — signatures, stamps"
    ),
    "official_record": (
        "- `header_issuer` — issuing body, record type and number, date\n"
        "- `subject_data` — the person / property / entity the record is about (name, ID, deed or registration number, location)\n"
        "- `decision_text` — what the record decides, certifies, registers, grants or orders, with its date and effect\n"
        "- `conditions` — conditions, restrictions, validity, expiry\n"
        "- `table_rows` — listed entries\n"
        "- `legal_basis` — the articles, decisions or requests the record is based on\n"
        "- `signatures_stamps` — signatures, stamps, QR / barcode and verification text\n"
        "- `attachment` — documents attached to the record"
    ),
}
LABELS["claim"] = LABELS["memo"]
LABELS["hearing_minutes"] = LABELS["judgment"] + (
    "\n- `attendance` — who attended each session (judges, prosecutor, parties, lawyers)")
# Any other doc_type (chat, user_notes, statement, other, unset).
LABELS["generic"] = (
    "- `header` — who wrote it, to whom, when, about what\n"
    "- `facts` — narrative and background\n"
    "- `positions` — what a party claims, demands, admits, denies, decides or agrees, with amounts and dates\n"
    "- `signatures` — closing formulas, signatures, stamps"
)

PROMPT = """\
You are a document SEGMENTER inside a Saudi legal-AI system. You receive one OCR'd document (Arabic) split into numbered paragraphs `[P001]`, `[P002]`…, with its type, a short summary and what the user said, for context. You map it into labeled sections and rate each section. You NEVER summarize, paraphrase or rewrite content: code cuts the original text at your boundaries. The agents that investigate the user's case — they search the laws and the court rulings for it and answer the user's legal questions — receive ONLY the sections you rate `core`, word for word; the drafting agent also receives `supporting` and `metadata`. A passage you do not rate `core` is invisible to the investigation.

## What you know about these documents
- Appendices, quotations, invoices, photo captions, letters, stamps and repeated letterheads can appear ANYWHERE — at the start, in the middle, interleaved — not only at the end. One upload can hold several documents (a contract plus its annex, offer and invoices; an expert report plus the contract it examined).
- Label every embedded document with the labels of its own kind: an annex or change order with the contract labels, an invoice, quotation or payment claim with the financial labels, a letter or email with the letter labels, a certificate or registry extract with the official-record labels. Rate it by the same rule as everything else.

## What the user said
The message may hold the user's own words in this conversation, up to the moment the file was sent (the message the file came with first). Read them to know what the user cares about. Anything in the document the user points to, asks about, quotes or disputes is `core` — even a table, an appendix or a passage you would otherwise rate `supporting`. The user's words never make a passage less than `core`: what the user has not mentioned yet can still matter later.

## How to rate `value` — one question
Ask of every section: **is it worth investigating and searching on for this case?**
- `core` — yes. Everything about the case itself: what each party claims, asks, argues, admits or denies; the facts they rely on; the legal basis they cite; the proceedings; the evidence, the witnesses and the experts' findings; the court's grounds and its ruling; the clauses, demands, decisions, conditions and figures a lawyer would examine. A passage does not have to be decisive to be `core` — it only has to be part of the case. **When in doubt, `core`.**
- `supporting` — appendix-like data that holds nothing to investigate: it sits beside the case without adding to it — the routine rows of a long schedule, an EMPTY template or standard form with nothing filled in, a list of enclosures or exhibits, reference data copied in. A form whose fields carry this case's values (names, amounts, dates, a vehicle, a policy, signatures of the parties) is `core`. Never rate a passage `supporting` on a guess: use it only when you are sure nothing in it is worth searching on.
- A table that carries the decision or the figures in issue — final amounts, totals, balances, status, responsibility shares, a schedule of fees, rates or penalties, the entries a record decides or certifies — is `core`. Only its routine rows are `supporting`.
- `metadata` — the document's identifying information at its FIRST occurrence: dates (Hijri and Gregorian), issuing body / court / circuit, case, deed, contract, decision or reference numbers, sender and addressee, subject line, index or table of contents, the opening / preamble formula, the parties with their capacities and representatives. For a record, certificate, registry extract or report, `metadata` is only its OWN header (issuer, logo, record type, number, date); what it states about its subject — the owner, managers, status, a vehicle, a policy, the parties to an accident, amounts — is what the record is for, and is `core`.
- `boilerplate` — letterheads, page headers and footers, page numbers, stamps, signatures, standard formulas (greetings, «والله الموفق», bismillah), image placeholders, OCR garbage, and every REPEAT of a header, letterhead or party block already given once.
- Rate by what a passage is, never by how much of the document is already `core`: there is no target share — a document may be 60% or 95% core. The same document must always get the same ratings.
- A long passage that appears twice (e.g. a section copied into the file again): rate the second copy exactly as you rated the first — code detects copies, you do not.
- A span that mixes a `core` part and another part: split it at the paragraph where the part changes. If one paragraph holds both, rate that paragraph `core`.
- Tables: a table's column-title rows, at their first occurrence, take the value of the rows under them (the rows cannot be read without them); the same column titles repeated on later pages are `boilerplate`.

## Output fields
- `documents_in_file` — every distinct document inside the upload, in order: `from_p`, `to_p` (paragraph NUMBERS as integers: P012 → 12) and an Arabic `name`.
- `segments` — contiguous paragraph ranges, in document order, covering EVERY paragraph from 1 to the last one exactly once: no gaps, no overlaps. Every paragraph counts — image placeholders (`![img…]`), page numbers and one-character lines too. Check before answering: the first segment starts at 1, every next `from_p` = the previous `to_p` + 1, and the last `to_p` = the paragraph count in the first line of the input. A label may recur. Split as finely as the label or value changes — never bury a passage of the case inside a long boilerplate span; one paragraph can be its own segment.
  - `from_p`, `to_p` — integers, first and last paragraph of the segment (inclusive; equal for a one-paragraph segment).
  - `label` — one label from the list for this document type (below), or `page_letterhead` (repeated letterheads, footers, page numbers), or `attachment` (an embedded document outside the list), or `other:<short_english_name>`.
  - `what` — Arabic, at most 15 words: what this span IS (a label, not a summary of its content).
  - `value` — `metadata` · `core` · `supporting` · `boilerplate`, by the rule above.
- `key_paragraphs` — the 5–15 single paragraphs carrying the most decisive content (the operative ruling, the decisive ground, the expert's final figures, the price / penalty / termination / jurisdiction clause, the decision a record certifies, a passage the user pointed to…): `p` (integer) and a short Arabic `why` (at most 12 words).

## Labels for this document type ({doc_type})
{labels}
{type_notes}"""


_JUDGMENT_NOTES = """
## How to label a judgment or a hearing record — the speaker rule
Label every passage by WHO is speaking or acting, never by its topic or by a heading:
- the plaintiff, the prosecutor (المدعي العام / النيابة العامة) or their representative → `claim` / `claim_requests`;
- the defendant (المدعى عليه / المتهم) or their lawyer → `defense` / `defense_pleas`;
- either party answering the court's or the investigator's question about the facts or the charge → `answers`;
- someone OUTSIDE the parties — a witness, an expert, a laboratory, an authority's report, a document's own content as cited → `evidence`;
- the court acting on procedure → `proceedings`; the court reasoning → `grounds`; the court deciding → `ruling`.
- Quoted words keep their original speaker: when the court or the prosecutor reports what the defendant said or confessed, that passage is the defendant's (`answers` / `defense_pleas` / `defense`); when they report a witness or an expert, it is `evidence`. The court's short lead-in («وبسؤال المدعى عليه أجاب…», «وقدم وكيل المدعي مذكرة جاء فيها…») goes with the passage it introduces.
- A party's reasoning is NEVER `grounds`, even when it starts with «وحيث» or sits under a heading «الأسباب» (e.g. the prosecutor's «الأسباب» in the indictment are `claim`). `grounds` starts where the court itself reasons, usually after the pleadings are closed («وبعد دراسة الدعوى…», «فبناءً على ما تقدم…»), and runs until the operative part.
- An interim decision the court gives in session with its reasons is `grounds`; the decision itself, if it settles a request, is `ruling`. A procedural decision (adjourn, appoint an expert, ask for a document) is `proceedings`.

## Values in a judgment or a hearing record
- The case runs from the claims to the ruling, and all of it is `core`: what each party says and asks, the answers, the evidence, the proceedings, the court's grounds, the ruling and the objection terms.
- `supporting` is rare here: only documents appended after the judgment that hold nothing to investigate.
- `metadata` = the first header (court, circuit, case and deed numbers, dates, parties table, opening formula). Later page headers, the repeated party table and the signatures are `boilerplate`.
"""

_EXPERT_NOTES = """
## Values in an expert report
- The case is the whole of the expert's work and what was put to the expert — the mandate, the method as applied, what each party told or submitted to the expert, every finding, every calculation, the conclusion — all `core`.
- An attached document is `core` when it carries case content (the contract examined, a letter with a demand, minutes of a meeting with the parties) and `supporting` when it is appendix-like data with nothing to investigate (a standard certificate, a routine schedule, a page of photos without findings).
- Photo placeholders are `boilerplate`; a caption that states a finding is `core`.
- `metadata` = the cover / first header (court, case and appointment numbers, expert, dates, parties). Later page headers, a restated mandate and signature blocks are `boilerplate`.
"""

_MEMO_NOTES = """
## Values in a memo or a statement of claim / objection
- The body of the memo is the case: the facts, the requests, every argument and reply, the legal basis, the closing requests — all `core`.
- `supporting` = appendix-like data: an exhibit list, or an attached exhibit that holds nothing to investigate. An attached exhibit that carries case content (a contract clause, a letter, a prior ruling) is `core`.
- `metadata` = the first header: court, case number, the judgment or decision concerned, the parties and their capacities, the subject, the date. Repeated headers and the closing formula / signature are `boilerplate`.
- Headings such as «أولاً: من حيث الشكل» belong to the passage they open.
"""

_CONTRACT_NOTES = """
## Values in a contract file
- Every clause of the contract and of its annexes and change orders is part of the case — definitions included, since they decide what a clause means — and is `core`; so are the amounts, totals, VAT and balance in an invoice or payment claim, and the content of each letter in the file.
- `supporting` = appendix-like data: the routine lines of a long table, a quotation's marketing / methodology / team text, a standard form attached without case content.
- `metadata` = the first title / number / date / place and the parties' data blocks of each document (contract, annex, change order, invoice, letter). Repeated party blocks, logos, page numbers, signature boxes, email headers and disclaimers are `boilerplate`.
"""

_LETTER_NOTES = """
## Values in a letter or a bundle of correspondence
- Each letter's content is the case: the facts it states, its references to earlier letters or to the contract, and every demand, warning, notice, decision, deadline and amount — all `core`.
- `supporting` = appendix-like data: the list of enclosures, a standard text copied in that adds nothing.
- `metadata` = each letter's sender, addressee, date, reference number and subject (a bundle has one set per letter). Greetings, closing formulas, signatures, stamps, email footers and disclaimers are `boilerplate`.
- A letter sent twice with the same text: rate the second copy as the first (code finds copies).
"""

_FINANCIAL_NOTES = """
## Values in a financial document
Financial uploads are records, not arguments: bank or loan statements, promissory notes (سند لأمر), invoices, payment claims (مطالبة مالية), receipts, settlements (مخالصة), payroll sheets. A lawyer uses them to prove WHO owes WHAT, HOW MUCH was paid or is still outstanding, and WHEN.
- `metadata` — the first occurrence of: the institution or issuer, the account / loan / contract / invoice / note number, the holder, the debtor and the creditor, the issue date, the statement period.
- The account header + status block at the top of a statement (institution, holder, loan / account number, amount, total paid, remaining, amount due, status) is ONE unit: when it carries any total, balance or status, rate the whole block `core` — OCR often garbles its cell labels, so do not try to separate them. When the same block is repeated on every page, its first occurrence is `core` and the repeats are `boilerplate`.
- `core` — every line that states a TOTAL owed, paid, disbursed or outstanding; balances (opening, closing, remaining); status lines (fully paid, overdue, cancelled, protested); the instrument's terms (face amount, due date, installment plan, interest or fees, payee, place of payment); the conditions and notes that bear on them; and every row that stands out from the routine — a missed, late, returned or reversed payment, an unusually large or one-off entry, a settlement or write-off, an entry whose description names the other party or the dispute.
- `supporting` — the routine rows of a regular schedule (equal installments paid on time, recurring fees). Keep a long routine table as ONE segment; split out only the rows that stand out, each as its own `core` segment labelled `notable_transactions`.
- `boilerplate` — repeated page headers and footers, column-title rows repeated on every page, page numbers, bank disclaimers, QR / barcode text, and a second copy of the same table.
- Never rate the totals or the status `supporting`.
- `key_paragraphs` — the totals and balance lines, the status line, the instrument's face amount and due date, and the rows that stand out.
"""

_OFFICIAL_NOTES = """
## Values in an official record
- What the record decides, certifies, registers, grants or orders, its conditions, the legal basis it rests on, the listed entries it decides or certifies, and everything it states about its subject (`subject_data`: the person, entity, property or vehicle, their status, owners, managers, policies, amounts) are the case — `core`.
- The title line of a record or list that states what was decided or certified (a statement of the persons promoted / appointed / licensed on a date, «قرار رقم … بشأن …», «شهادة بأن …») is `decision_text` (`core`), not a header; the header is only the issuer's name, logo and reference number.
- `supporting` = appendix-like data: background rows with nothing to investigate, standard attachments.
- `metadata` = only the record's own header: the issuer, record type, number and date at first occurrence. Repeated headers, stamps, signatures, QR and verification text are `boilerplate`.
"""

_REFERENCE_NOTES = """
## Values in a reference text (law, regulation, circular, guide, published precedent, blank template)
- The rule text is what gets investigated — `core`: definitions, provisions, procedures, penalties, transitional and final provisions, schedules of fees, rates or penalties. A published judgment or precedent: its principle (المبدأ) and the whole case from the claims to the ruling. A blank template or form: its clauses and fields.
- `supporting` = appendix-like data: tables of contents, the filling instructions of a template, forms that hold no rule.
- `metadata` = the title and issuing instrument at first occurrence. Gazette headers, repeated titles, page numbers are `boilerplate`.
"""

_GENERIC_NOTES = """
## Values in this document
- Everything about the matter is `core`: the facts, what a party states, claims, demands, admits, denies, decides or agrees, with the amounts and dates.
- `supporting` = appendix-like data with nothing to investigate. `metadata` = the first header. Repeats, signatures and page furniture are `boilerplate`.
"""

TYPE_NOTES = {
    "judgment": _JUDGMENT_NOTES,
    "hearing_minutes": _JUDGMENT_NOTES,
    "expert_report": _EXPERT_NOTES,
    "memo": _MEMO_NOTES,
    "claim": _MEMO_NOTES,
    "contract": _CONTRACT_NOTES + _LETTER_NOTES + _FINANCIAL_NOTES,
    "letter": _LETTER_NOTES,
    "financial": _FINANCIAL_NOTES,
    "official_record": _OFFICIAL_NOTES,
    "reference": _REFERENCE_NOTES,
    "generic": _GENERIC_NOTES,
}

#: The label list / type notes used for any doc_type without its own (chat, statement,
#: user_notes, other, unset).
GENERIC_TYPE = "generic"
GENERIC_LABELS = LABELS[GENERIC_TYPE]

# ---------------------------------------------------------------------------
# Split mode — production addendum (experiments/run_split.py)
# ---------------------------------------------------------------------------

#: The whole-document coverage sentences → their part versions. A part maps a
#: paragraph range, so "starts at 1" and "ends at the paragraph count in the first line"
#: would contradict CHUNK_NOTE. Each old sentence must occur exactly once in PROMPT.
_PART_COVERAGE: tuple[tuple[str, str], ...] = (
    (
        "covering EVERY paragraph from 1 to the last one exactly once",
        "covering EVERY paragraph of YOUR PART exactly once",
    ),
    (
        "Check before answering: the first segment starts at 1, every next `from_p` = the "
        "previous `to_p` + 1, and the last `to_p` = the paragraph count in the first line of "
        "the input.",
        "Check before answering: the first segment starts at the first paragraph of YOUR PART, "
        "every next `from_p` = the previous `to_p` + 1, and the last `to_p` = the last "
        "paragraph of YOUR PART.",
    ),
)

CHUNK_NOTE = """

## You are mapping ONE PART of a longer document ({part} of 2)
Another agent maps the rest in parallel; code joins the parts afterwards.
- Map ONLY paragraphs {a}–{b} (the section "YOUR PART"). Your segments must cover every one of them exactly once, starting at {a} and ending at {b}. Never output a segment outside that range.
- Everything else in the message — the summary of the document, what the user said, the first page, and the context paragraphs before or after your part — is for reading only, so you know what the document is and what you are continuing.
- {first_page_rule}
- `documents_in_file`: only the documents that appear inside your part, with ranges clipped to your part.
- `key_paragraphs`: at most 8, all inside your part.
"""

_FIRST_PART_RULE = (
    "Your part starts at the beginning of the document: the first header / party block "
    "you meet is `metadata` as usual."
)
_SECOND_PART_RULE = (
    "The document's first page (paragraphs 1–{first_end}) is shown above and has ALREADY "
    "been mapped by the other agent: any repeat of that letterhead, header or party block "
    "inside your part is `boilerplate`, not `metadata`. Use `metadata` only for identifying "
    "information that appears for the first time in your part (e.g. the header of an "
    "embedded document)."
)

# ---------------------------------------------------------------------------
# Bundles — several small files of one message mapped as one input (owner, 2026-11-12)
# ---------------------------------------------------------------------------

BUNDLE_NOTE = """

## This upload is {n} files sent together
The user sent these {n} files in one message. They are mapped together as ONE input with ONE paragraph numbering. Before each file's paragraphs an UN-numbered boundary line «— الملف i من {n} · … —» marks where that file starts; it is not a paragraph — never map it.
- The files may be pages of one document or separate documents. List every document in `documents_in_file`; a document that continues into the next file is listed once per file, under the same name.
- Never let a segment run across a boundary line: the last paragraph before a boundary line ends a segment, the first one after it starts a new one.
- Label each file with the labels of its OWN doc_type, shown on its boundary line (the label lists above are given per type; a file with no doc_type uses the generic labels).
- Rate every file by the same rule as everything else. A file's own header is `metadata` the first time it appears; a header repeated from an earlier file is `boilerplate`.
"""

#: The un-numbered line before each file's paragraphs in a bundle message.
BOUNDARY_LINE = "— الملف {i} من {n} · doc_type: {doc_type} · {title} —"
BOUNDARY_CONTINUED = "— الملف {i} من {n} (تابع) · doc_type: {doc_type} · {title} —"
BUNDLE_SUMMARIES_HEADING = "## Summaries of the files (context only — never map them)"
BUNDLE_TITLE = "{n} ملفات أُرسلت معاً في رسالة واحدة"

NO_SUMMARY = "(لا يوجد ملخص)"
SUMMARY_HEADING = "## Summary of the document (context only — never map it)"
USER_WORDS_HEADING = "## What the user said (context only — never map it)"
PARAGRAPHS_HEADING = "## Paragraphs"


def type_key(doc_type: str | None) -> str:
    """The LABELS / TYPE_NOTES key for a doc_type (``generic`` when it has none)."""
    t = str(doc_type or "")
    return t if t in LABELS else GENERIC_TYPE


def labels_for(doc_type: str | None) -> str:
    return LABELS[type_key(doc_type)]


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"split-mode anchor not found exactly once in PROMPT: {old[:60]!r}")
    return text.replace(old, new)


def build_instructions(
    doc_type: str | None,
    *,
    part: int | None = None,
    a: int | None = None,
    b: int | None = None,
    first_end: int | None = None,
    bundle_files: int | None = None,
    bundle_types: Sequence[str | None] | None = None,
) -> str:
    """System prompt for a whole document (``part=None``) or for one of two parts.

    ``bundle_files`` (2+): the input is a bundle of that many files sent together —
    ``BUNDLE_NOTE`` goes after the type notes (and before ``CHUNK_NOTE`` in split
    mode). ``bundle_types``: the files' doc_types — the label lists and type notes
    of EVERY type present are given, so each file is labelled by its own kind (a
    registry extract bundled with two contracts keeps the official-record labels).
    ``None`` leaves the text exactly as for one document.
    """
    t = type_key(doc_type)
    prompt = PROMPT
    if part:
        for old, new in _PART_COVERAGE:
            prompt = _replace_once(prompt, old, new)
    keys = list(dict.fromkeys(type_key(x) for x in (bundle_types or [])))
    if len(keys) > 1:
        shown = ", ".join(keys)
        labels = "\n\n".join(f"### `{k}` files\n{LABELS[k]}" for k in keys)
        notes = "".join(dict.fromkeys(TYPE_NOTES.get(k, "") for k in keys))
    else:
        shown = str(doc_type or "") or "unknown"
        labels = LABELS[keys[0] if keys else t]
        notes = TYPE_NOTES.get(keys[0] if keys else t, "")
    text = (
        prompt.replace("{doc_type}", shown)
        .replace("{labels}", labels)
        .replace("{type_notes}", notes)
    )
    if bundle_files:
        text += BUNDLE_NOTE.replace("{n}", str(bundle_files))
    if part:
        rule = (
            _FIRST_PART_RULE
            if part == 1
            else _SECOND_PART_RULE.replace("{first_end}", str(first_end or 1))
        )
        text += (
            CHUNK_NOTE.replace("{part}", str(part))
            .replace("{a}", str(a))
            .replace("{b}", str(b))
            .replace("{first_page_rule}", rule)
        )
    return text


# ---------------------------------------------------------------------------
# User messages
# ---------------------------------------------------------------------------


def _head(doc_type: str | None, title: str, chars: int, n: int) -> str:
    """``make_paras.py``'s header line. The title is put on one line (the prompt
    points at "the paragraph count in the first line of the input")."""
    shown_type = str(doc_type or "") or "unknown"
    clean_title = " ".join(str(title or "").split())
    return f"# doc_type: {shown_type} | title: {clean_title} | {chars} chars | {n} paragraphs"


def _summary(summary: str) -> str:
    return (summary or "").strip() or NO_SUMMARY


def _user_words(user_words: str | None) -> list[str]:
    """The "What the user said" block — omitted when there is nothing."""
    text = (user_words or "").strip()
    return ["", USER_WORDS_HEADING, text] if text else []


def build_user_message(
    paragraphs: Sequence[Paragraph],
    *,
    doc_type: str | None,
    title: str,
    summary: str,
    chars: int,
    user_words: str | None = None,
) -> str:
    """Whole-document message: header line, the summary and the user's words (context
    only), the numbered paragraphs."""
    return "\n".join([
        _head(doc_type, title, chars, len(paragraphs)),
        "",
        SUMMARY_HEADING,
        _summary(summary),
        *_user_words(user_words),
        "",
        PARAGRAPHS_HEADING,
        render_numbered(paragraphs),
        "",
    ])


def build_part_message(
    paragraphs: Sequence[Paragraph],
    *,
    part: int,
    a: int,
    b: int,
    first_end: int,
    overlap: int,
    doc_type: str | None,
    title: str,
    summary: str,
    chars: int,
    user_words: str | None = None,
) -> str:
    """One part of a split document (``experiments/run_split.py`` layout).

    Part 1: summary, user's words, YOUR PART, ``overlap`` read-only paragraphs after it.
    Part 2: summary, user's words, the shared first page, ``overlap`` read-only
    paragraphs before it (never repeating first-page paragraphs), YOUR PART.
    """
    n = len(paragraphs)
    lines = [
        f"{_head(doc_type, title, chars, n)} | part {part} of 2 — you map {pid(a)}–{pid(b)}",
        "",
        SUMMARY_HEADING,
        _summary(summary),
        *_user_words(user_words),
    ]
    if part == 2:
        lines += [
            "",
            "## First page of the document (context only — already mapped)",
            render_numbered(paragraphs, 1, first_end),
        ]
        ctx_from = max(first_end + 1, a - overlap)
        if ctx_from <= a - 1:
            lines += [
                "",
                "## Context before your part (read only — do not map)",
                render_numbered(paragraphs, ctx_from, a - 1),
            ]
    lines += ["", f"## YOUR PART — map every paragraph {pid(a)}–{pid(b)}", render_numbered(paragraphs, a, b)]
    if part == 1 and b < n:
        lines += [
            "",
            "## Context after your part (read only — do not map)",
            render_numbered(paragraphs, b + 1, min(n, b + overlap)),
        ]
    return "\n".join(lines + [""])


# ---------------------------------------------------------------------------
# Bundle messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleFile:
    """One file of a bundle, as the message shows it. ``first_p`` / ``last_p`` are
    its paragraphs in the bundle's GLOBAL numbering."""

    index: int          # 1-based, upload order
    first_p: int
    last_p: int
    doc_type: str
    title: str
    summary: str


def boundary_line(f: BundleFile, n: int, *, continued: bool = False) -> str:
    tpl = BOUNDARY_CONTINUED if continued else BOUNDARY_LINE
    return (
        tpl.replace("{i}", str(f.index))
        .replace("{n}", str(n))
        .replace("{doc_type}", str(f.doc_type or "") or "unknown")
        .replace("{title}", " ".join(str(f.title or "").split()) or "—")
    )


def render_bundle(
    paragraphs: Sequence[Paragraph],
    files: Sequence[BundleFile],
    first: int = 1,
    last: int | None = None,
) -> str:
    """``[P001] text`` lines for global paragraphs ``first..last`` with a boundary line
    before each file's first paragraph — and a «(تابع)» line when the block starts
    inside a file."""
    last = len(paragraphs) if last is None else last
    starts = {f.first_p: f for f in files}
    out: list[str] = []
    for p in paragraphs[first - 1:last]:
        f = starts.get(p.n)
        if f is not None:
            out.append(boundary_line(f, len(files)))
        elif p.n == first:
            owner = next((x for x in files if x.first_p <= p.n <= x.last_p), None)
            if owner is not None:
                out.append(boundary_line(owner, len(files), continued=True))
        out.append(f"[{p.pid}] {p.text}")
    return "\n".join(out)


def _bundle_summaries(files: Sequence[BundleFile]) -> list[str]:
    lines = [BUNDLE_SUMMARIES_HEADING]
    for f in files:
        title = " ".join(str(f.title or "").split()) or "—"
        lines += [f"### الملف {f.index} من {len(files)} · {title}", _summary(f.summary)]
    return lines


def _bundle_head(doc_type: str | None, files: Sequence[BundleFile], chars: int, n: int) -> str:
    return _head(doc_type, BUNDLE_TITLE.replace("{n}", str(len(files))), chars, n)


def build_bundle_message(
    paragraphs: Sequence[Paragraph],
    files: Sequence[BundleFile],
    *,
    doc_type: str | None,
    chars: int,
    user_words: str | None = None,
) -> str:
    """A bundle in one call: header line (total chars and paragraphs), each file's
    summary, the user's words, then every file's numbered paragraphs under its
    boundary line."""
    return "\n".join([
        _bundle_head(doc_type, files, chars, len(paragraphs)),
        "",
        *_bundle_summaries(files),
        *_user_words(user_words),
        "",
        PARAGRAPHS_HEADING,
        render_bundle(paragraphs, files),
        "",
    ])


def build_bundle_part_message(
    paragraphs: Sequence[Paragraph],
    files: Sequence[BundleFile],
    *,
    part: int,
    a: int,
    b: int,
    first_end: int,
    overlap: int,
    doc_type: str | None,
    chars: int,
    user_words: str | None = None,
) -> str:
    """One part of a split bundle — ``build_part_message``'s layout with boundary lines."""
    n = len(paragraphs)
    lines = [
        f"{_bundle_head(doc_type, files, chars, n)} | part {part} of 2 — you map {pid(a)}–{pid(b)}",
        "",
        *_bundle_summaries(files),
        *_user_words(user_words),
    ]
    if part == 2:
        lines += [
            "",
            "## First page of the document (context only — already mapped)",
            render_bundle(paragraphs, files, 1, first_end),
        ]
        ctx_from = max(first_end + 1, a - overlap)
        if ctx_from <= a - 1:
            lines += [
                "",
                "## Context before your part (read only — do not map)",
                render_bundle(paragraphs, files, ctx_from, a - 1),
            ]
    lines += [
        "",
        f"## YOUR PART — map every paragraph {pid(a)}–{pid(b)}",
        render_bundle(paragraphs, files, a, b),
    ]
    if part == 1 and b < n:
        lines += [
            "",
            "## Context after your part (read only — do not map)",
            render_bundle(paragraphs, files, b + 1, min(n, b + overlap)),
        ]
    return "\n".join(lines + [""])


__all__ = [
    "BOUNDARY_LINE",
    "BUNDLE_NOTE",
    "BundleFile",
    "build_bundle_message",
    "build_bundle_part_message",
    "render_bundle",
    "CHUNK_NOTE",
    "GENERIC_LABELS",
    "GENERIC_TYPE",
    "LABELS",
    "NO_SUMMARY",
    "PROMPT",
    "PROMPT_VERSION",
    "TYPE_NOTES",
    "USER_WORDS_HEADING",
    "build_instructions",
    "build_part_message",
    "build_user_message",
    "labels_for",
    "type_key",
]
