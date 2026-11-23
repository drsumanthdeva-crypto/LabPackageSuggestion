"""Remove patient-identifying details from a client record before it is sent to the AI.

No GUI or network code, so it can be tested on its own.

  deidentify_record(record) -> (clean_record, redaction_count)

Rules
  * Fields that exist only to identify the client are dropped (client ID,
    consultation timestamps, name, mobile, location, date of birth).
  * Exact dates are turned into "N months/years ago".
  * EVERY remaining text value (occupation, surgeries, medication names,
    "other symptoms", lab results, ...) is scrubbed, whichever field it is in,
    so a field added to the form later is covered too.

The scrubber removes: phone numbers and long ID numbers, e-mail addresses,
web links, full dates, "Dr/Mr/Mrs ..." names, anything that follows by / at /
in / from / under / via / near / @ and starts with a capital letter (people,
hospitals, towns), names of hospitals and clinics, and the client's own name
and place wherever they appear in the text.

Limits: this is pattern based. A lower-case place or person name with no
introducing word ("rao did my surgery") can still get through. Keep free
text short and clinical.
"""
import copy
import re
from datetime import date, datetime

# Known hospital / lab brand names that appear without "Hospital" after them.
# Add your local ones here.
ORG_NAMES = ["Apollo", "Yashoda", "KIMS", "Fortis", "Manipal", "AIIMS", "NIMS",
             "Medicover", "Narayana", "Aster", "Kamineni", "Sunshine", "Max Healthcare",
             "Rainbow", "Continental", "Care Hospitals", "Metropolis", "Lal PathLabs",
             "SRL", "Thyrocare", "Dr Lal", "Vijaya Diagnostic"]

DROP_TOP = ("clientId", "consultationDate", "updatedAt", "schemaVersion")
DROP_BASIC = ("name", "mobile", "location", "dateOfBirth")

_MONTHS = (r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
           r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?")
_CAP = r"[A-Z][\w&'.-]*"

_EMAIL = re.compile(r"(?:(?i:\b(?:at|on|via))\s+)?[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_DATE_ISO = re.compile(r"\b(\d{4})-\d{1,2}-\d{1,2}\b")
_DATE_NUM = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](\d{4}|\d{2})\b")
_DATE_TXT = re.compile(r"(?i)\b\d{1,2}(?:st|nd|rd|th)?\s+(?:%s)[a-z]*\.?,?\s+(\d{4})\b" % _MONTHS)
_LONG_DIGITS = re.compile(r"(?<![\d.])\d{6,}(?![\d.])")          # IDs, MRNs, Aadhaar, phones
_SPACED_DIGITS = re.compile(r"(?<![\w.])\+?\d[\d\s\-()]{8,}\d(?![\w.])")  # 98765 43210, +91-98...

# "by Dr Rao", "at Apollo Hospital", "in Hyderabad", "from Mr Reddy", "@ KIMS"
_CLAUSE = re.compile(
    r"(?:(?i:\b(?:by|at|in|from|under|via|near))\s+|@\s*)"
    r"(?!(?:%s)\b)%s(?:(?:\s+(?:of|and|&))?\s+%s)*(?:,\s*%s(?:\s+%s)*)*" % (_MONTHS, _CAP, _CAP, _CAP, _CAP))
_TITLE = re.compile(
    r"\b(?i:dr|mr|mrs|ms|miss|prof|shri|smt|sri)\b\.?\s+[A-Za-z][\w'-]*\.?(?:\s+[A-Z][\w'.-]*)*")
_ORG_WORD = re.compile(
    r"\b(?:[A-Z][\w&'-]*\s+){1,3}(?i:hospitals?|clinic|polyclinic|nursing home|medical (?:centre|center|college)|"
    r"diagnostics?|laborator(?:y|ies)|labs?|institute|healthcare|health care)\b(?:\s+[A-Z][\w&'-]*)*")
_DANGLING = re.compile(r"(?i)\b(?:by|at|in|from|under|via|near|with|and|of)\b[\s,;:.\-]*$")


def _year_or_blank(m):
    y = m.group(1)
    return y if len(y) == 4 else ""


class _Scrubber:
    def __init__(self, known_terms):
        self.count = 0
        terms = sorted({t for t in known_terms if len(t) >= 3}, key=len, reverse=True)
        self._known = (re.compile(r"(?i)\b(?:%s)\b" % "|".join(re.escape(t) for t in terms))
                       if terms else None)
        names = sorted(ORG_NAMES, key=len, reverse=True)
        self._orgs = re.compile(r"(?i)\b(?:%s)\b(?:\s+[A-Z][\w&'-]*)*" % "|".join(re.escape(n) for n in names))

    def _sub(self, pattern, repl, s):
        s, n = pattern.subn(repl, s)
        self.count += n
        return s

    def text(self, s):
        s = self._sub(_EMAIL, "", s)
        s = self._sub(_URL, "", s)
        s = self._sub(_DATE_ISO, _year_or_blank, s)
        s = self._sub(_DATE_TXT, _year_or_blank, s)
        s = self._sub(_DATE_NUM, _year_or_blank, s)

        def spaced(m):
            return "" if sum(c.isdigit() for c in m.group(0)) >= 10 else m.group(0)
        s = self._sub(_SPACED_DIGITS, spaced, s)
        s = self._sub(_LONG_DIGITS, "", s)
        if self._known:
            s = self._sub(self._known, "", s)
        s = self._sub(_CLAUSE, "", s)
        s = self._sub(_TITLE, "", s)
        s = self._sub(_ORG_WORD, "", s)
        s = self._sub(self._orgs, "", s)
        return _tidy(s)


def _tidy(s):
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s+([,;:.])", r"\1", s)
    s = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    prev = None
    while prev != s:                      # "Appendectomy by" -> "Appendectomy"
        prev = s
        s = _DANGLING.sub("", s.strip())
    return s.strip(" ,;:-")


def _ago(d, today):
    days = (today - d).days
    if days < 0:
        return "date in the future"
    months = days // 30
    if months < 1:
        return "less than a month ago"
    if months < 24:
        return "%d month%s ago" % (months, "" if months == 1 else "s")
    return "%d years ago" % (days // 365)


def _known_terms(basic):
    terms = []
    for key in ("name", "location"):
        v = basic.get(key)
        if isinstance(v, str):
            terms += [p for p in re.split(r"[\s,;/.()-]+", v) if p]
    return terms


def deidentify_record(record, today=None):
    """Return (copy of the record that is safe to send, number of removals)."""
    today = today or date.today()
    rec = copy.deepcopy(record) if isinstance(record, dict) else {}
    basic = rec.get("basic") if isinstance(rec.get("basic"), dict) else {}
    scrub = _Scrubber(_known_terms(basic))

    for k in DROP_TOP:
        rec.pop(k, None)
    for k in DROP_BASIC:
        basic.pop(k, None)

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            s = v.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):      # a whole-date field
                try:
                    return _ago(datetime.strptime(s, "%Y-%m-%d").date(), today)
                except ValueError:
                    pass
            return scrub.text(v)
        return v

    clean = walk(rec)
    return clean, scrub.count