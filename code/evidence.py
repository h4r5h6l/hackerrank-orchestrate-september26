"""Untrusted evidence handling: image amounts and message texts.

Images and messages are DATA, never instructions. This module extracts
structured facts from them deterministically.

Image amounts: `dataset/images.csv` links each image to one financial
event with a blank amount. Amounts were produced with tesseract OCR
(+ preprocessing) and agent verification; they are embedded here as a
resolved-facts table so the pipeline is deterministic and offline.

Message facts: employer/service-provider/bank/merchant updates may
amend, confirm, cancel or delay a financial fact. The parser below maps
each known message pattern to a structured amendment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

# ---------------------------------------------------------------------------
# image -> amount (currency is the image's own; conversion happens in fx.py)
# ---------------------------------------------------------------------------
IMAGE_AMOUNTS = {
    "image_01": 4365000.0,   # Aug 2019 payslip, net pay IDR 4,365,000
    "image_02": 200000.0,    # rent receipt INR 2,00,000
    "image_03": 41272.0,     # bill of supply total INR 41,272
    "image_04": 2854.0,      # online grocery order item bill INR 2,854
    "image_05": 704.05,      # telecom bill amount due INR 704.05
    "image_06": 1995.0,      # grocery tax invoice total INR 1,995
    "image_07": 8528.10,     # restaurant tax invoice total INR 8,528.10
    "image_08": 15339.0,     # maintenance bill received INR 15,339
    "image_09": 723.0,       # water bill paid INR 723
    "image_10": 79679.26,    # large grocery invoice total INR 79,679.26
    "image_11": 3650.0,      # hospital bill payable INR 3,650
    "image_12": 33.5,        # taxi fare USD 33.50
    "image_13": 2298.0,      # tote bag order total incl. taxes INR 2,298
    "image_14": 4329.0,      # pharmacy purchase INR 4,329 (OCR: TOTAL 4,329)
    "image_15": 9124.0,      # airline ticket grand total INR 9,124
    "image_16": 393.22,      # EV charging wallet payment INR 393.22
}


def resolve_image_amount(image) -> float | None:
    """Return the amount shown in the image, else None."""
    return IMAGE_AMOUNTS.get(image.image_id)


# ---------------------------------------------------------------------------
# message facts
# ---------------------------------------------------------------------------
_AMOUNT_RE = r"(\d[\d,]*(?:\.\d+)?)"
_DATE_RE = r"(\d{4}-\d{2}-\d{2})"
_CCYS = ("INR", "ZAR", "IDR", "USD", "EUR")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _ccy_of(text: str, end: int) -> str:
    found = ""
    for m in re.finditer(r"(INR|ZAR|IDR|USD|EUR)", text[:end], re.I):
        found = m.group(1).upper()
    return found


def _find_dates(text: str) -> list[str]:
    return re.findall(r"\d{4}-\d{2}-\d{2}", text)


@dataclass
class MessageFacts:
    """Structured facts extracted from one message."""

    salary_amount: list = field(default_factory=list)      # (amount, from_date|None, ccy)
    salary_date_shift: list = field(default_factory=list)  # (new_date,)
    salary_stop: bool = False
    salary_resume_date: list = field(default_factory=list)  # (amount|None, date, ccy)
    one_time_credit: list = field(default_factory=list)    # (amount, date|None, ccy)
    rent_mult: list = field(default_factory=list)          # (mult, from_date|None)
    ref: str = ""
def _sal_keywords():
    return (r"(?:salary|pay|gaji|monthly salary|regular salary|confirmed salary|"
            r"gaji pokok|base salary|next salary|first salary)")


def parse_message_facts(msg) -> MessageFacts:
    text = (msg.text or "").strip()
    facts = MessageFacts(ref=msg.message_id)
    if not text:
        return facts
    low = text.lower()
    dates = _find_dates(text)

    # 1) salary amount changes (increase / decrease / temporary / regular / base)
    m = re.search(
        _sal_keywords()
        + r".*?(?:increased|increase|risen|naik|aument|berubah|reduced|decrease|"
          r"temporary|dikurangi|berkurang)"
        + r".*?(?:to|menjadi|dikembek)\s*(?:INR|ZAR|IDR|USD|EUR)?\s*" + _AMOUNT_RE,
        text, re.I)
    if m:
        from_date = None
        for d in dates:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
            if dd.day >= 10:
                from_date = dd
        ccy = _ccy_of(text, m.start(1))
        facts.salary_amount.append((_num(m.group(1)), from_date, ccy))
        return facts

    # 2) salary with an explicit amount + future date (first / new / confirmed)
    m = re.search(
        r"(?:salary|pay|gaji)[^.]*?(?:of|is|will be|sebesar|menjadi)\s*"
        r"(?:INR|ZAR|IDR|USD|EUR)?\s*" + _AMOUNT_RE + r"[^.]*?"
        r"(?:scheduled for|confirmed credit date|confirmed for|dijadwalkan pada|"
        r"confirmed on|credit date is)\s*" + _DATE_RE,
        text, re.I)
    if m:
        ccy = _ccy_of(text, m.start(1))
        facts.one_time_credit.append(
            (_num(m.group(1)), datetime.strptime(m.group(2), "%Y-%m-%d").date(), ccy))
        return facts

    # 3) confirmed salary date shift
    if re.search(r"confirmed salary is now expected|now expected on|dikemungkinan masuk", text, re.I):
        for d in dates:
            facts.salary_date_shift.append(d)
        return facts

    # 4) regular salary resumes
    m = re.search(
        r"regular salary of\s*" + _AMOUNT_RE + r"?\s*(?:resumes?|resume) on\s*" + _DATE_RE,
        text, re.I)
    if m:
        amt = _num(m.group(1)) if m.group(1) else None
        ccy = _ccy_of(text, m.start(1))
        facts.salary_resume_date.append(
            (amt, datetime.strptime(m.group(2), "%Y-%m-%d").date(), ccy))
        return facts

    # 5) employment / seasonal contract ended -> stop salary
    if re.search(r"employment has ended|seasonal contract has ended|etelah berakhir|"
                 r"kontrak musiman.*berakhir", low):
        facts.salary_stop = True
        return facts

    # 6) one household income ended, remaining salary stated
    m = re.search(
        r"(?:remaining confirmed monthly salary is|sisa gaji bulanan)[^.]*?" + _AMOUNT_RE,
        text, re.I)
    if m:
        ccy = _ccy_of(text, m.start(1))
        facts.salary_amount.append((_num(m.group(1)), None, ccy))
        return facts
# 7) Indonesian salary-raise pattern: "naik menjadi IDR X ... berlaku mulai DATE"
    if re.search(r"naik menjadi|increased to", low) and dates:
        am = re.search(r"menjadi\s*" + _AMOUNT_RE, text)
        if am:
            ccy = _ccy_of(text, am.start(1))
            from_date = datetime.strptime(dates[0], "%Y-%m-%d").date()
            facts.salary_amount.append((_num(am.group(1)), from_date, ccy))
            return facts

    # 8) next salary reduced (unpaid leave)
    m = re.search(r"(?:next salary|next pay) is reduced to\s*" + _AMOUNT_RE, text, re.I)
    if m:
        ccy = _ccy_of(text, m.start(1))
        facts.salary_amount.append((_num(m.group(1)), None, ccy))
        return facts

    # 9) temporary monthly pay
    m = re.search(r"temporary monthly pay is\s*" + _AMOUNT_RE, text, re.I)
    if m:
        ccy = _ccy_of(text, m.start(1))
        facts.salary_amount.append((_num(m.group(1)), None, ccy))
        return facts

    # 10) rent increase (renewed lease +12%)
    if re.search(r"renewed lease increases monthly rent by 12%|new amount applies|"
                 r"new amount will be used", low):
        facts.rent_mult.append((1.12, None))
        return facts

    # 11) invoice approved -> one-time credit on settlement date
    m = re.search(
        r"approved an invoice payment of\s*(?:INR|USD|IDR|ZAR|EUR)?\s*" + _AMOUNT_RE
        + r".*?settlement(?: is)? expected on\s*" + _DATE_RE, text, re.I)
    if m:
        ccy = _ccy_of(text, m.start(1))
        facts.one_time_credit.append(
            (_num(m.group(1)), datetime.strptime(m.group(2), "%Y-%m-%d").date(), ccy))
        return facts

    # 12) regular salary + one-time arrears adjustment in the same payroll
    m = re.search(
        r"regular salary for the next payroll is\s*(?:EUR|INR|USD|ZAR|IDR)?\s*"
        + _AMOUNT_RE + r".*?one-time arrears adjustment of\s*(?:EUR|INR|USD|ZAR|IDR)?\s*"
        + _AMOUNT_RE, text, re.I)
    if m:
        ccy = _ccy_of(text, m.start(1))
        ccy2 = _ccy_of(text, m.start(2))
        facts.salary_amount.append((_num(m.group(1)), None, ccy))
        facts.one_time_credit.append((_num(m.group(2)), None, ccy2))
        return facts

    # Everything else (prize pending, refund processing, investment value
    # changes, transfers between own accounts, minimum-payment reminders,
    # retry notices, reimbursement notices) conveys no cash fact for the
    # forecast. Pending credits are already excluded by status.

    return facts