"""Test price list (Preventive_Health_Test_Catalogue.xlsx) for the PDF report.

No GUI or network code, so it can be tested on its own. Needs openpyxl
(pip install openpyxl); without it the app still works, just without prices.

  load(path)                   -> Catalogue  (raises CatalogueError)
  Catalogue.match(name)        -> test entry (dict) or None
  price_analysis(cat, analysis)-> prices for every suggested test + totals
  prompt_block(cat)            -> text that lists the catalogue for the AI
  inr(n)                       -> "12,34,567" (Indian digit grouping)

Reads the "Test Catalogue" sheet by COLUMN HEADER (so columns may be moved):
Category, Test Code, Test Name, Standard Name / Abbreviation, MRP (Rs),
Package Price (Rs). If the Package Price cell has no saved value (a file
written by a program that does not calculate formulas), it is worked out
from the discount on the "Settings" sheet, the same way the sheet does it.

Matching a suggested test to the catalogue is EXACT only: the test name, its
code, its abbreviation, or its name without a trailing "(...)" (only when that
short name is unique), ignoring case, spacing and punctuation. There is
deliberately no "close enough" matching: "Vitamin B1" vs "Vitamin B12" or
"Hepatitis B" vs "Hepatitis C" would silently print the wrong price. A test
that is not matched is shown as "price on request".
"""
import math
import re
from pathlib import Path

PRIORITIES = ("top", "moderate", "optional")

_COLS = {
    "category": "Category",
    "code": "Test Code",
    "name": "Test Name",
    "abbr": "Standard Name / Abbreviation",
    "mrp": "MRP (Rs)",
    "pkg": "Package Price (Rs)",
}
_SHEET = "Test Catalogue"
_SETTINGS = "Settings"


class CatalogueError(Exception):
    """The price list could not be read (message is safe to show to the user)."""


def norm(s):
    """Lower case, '&' -> 'and', punctuation and extra spaces removed."""
    s = str(s or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return v
    try:
        x = float(str(v).replace(",", "").strip())
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def _round_to(x, step):
    return math.floor(x / step + 0.5) * step      # Excel ROUND: halves go up


def inr(n):
    """1234567 -> '12,34,567'; 250 -> '250'; 99.5 -> '99.50'."""
    n = float(n)
    neg = n < 0
    n = abs(n)
    whole = int(n)
    frac = round(n - whole, 2)
    if frac >= 1:
        whole, frac = whole + 1, 0.0
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        s = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head) + "," + tail
    if frac:
        s += ("%.2f" % frac)[1:]
    return ("-" if neg else "") + s


class Catalogue:
    def __init__(self, tests, source="", note="", skipped=()):
        self.tests = list(tests)          # dicts: category, code, name, abbr, mrp, pkg
        self.source = source
        self.note = note                  # e.g. "TEST DATA - placeholder prices only"
        self.skipped = list(skipped)      # rows that could not be used (for diagnostics)
        self._code, self._name, self._abbr = {}, {}, {}
        dup = set()
        for t in self.tests:
            self._code.setdefault(norm(t["code"]), t)
            self._name.setdefault(norm(t["name"]), t)
            a = norm(t["abbr"])
            if a:
                if a in self._abbr and self._abbr[a] is not t:
                    dup.add(a)
                self._abbr.setdefault(a, t)
        for a in dup:                      # ambiguous abbreviation: never guess
            del self._abbr[a]
        # "Vitamin B12 (Cobalamin)" can also be found as "Vitamin B12", but only when
        # that short name belongs to exactly one test with a separate charge.
        base, clash = {}, set()
        for t in self.tests:
            if t["mrp"] > 0 and "(" in t["name"]:
                b = norm(re.sub(r"\s*\([^)]*\)", "", t["name"]))
                if b in base:
                    clash.add(b)
                base.setdefault(b, t)
        self._base = {b: t for b, t in base.items() if b and b not in clash}

    @property
    def is_placeholder(self):
        n = self.note.lower()
        return "test data" in n or "placeholder" in n

    def match(self, text):
        """Catalogue entry for a suggested test, or None. Exact matches only."""
        raw = str(text or "").strip()
        if not raw:
            return None
        tries = [raw]
        no_paren = re.sub(r"\s*\([^)]*\)", "", raw).strip()      # "CBC (Complete Blood Count)"
        if no_paren and no_paren != raw:
            tries.append(no_paren)
        tries += [m.strip() for m in re.findall(r"\(([^)]*)\)", raw)]
        for t in tries:
            k = norm(t)
            if not k:
                continue
            for table in (self._name, self._code, self._abbr, self._base):
                if k in table:
                    return table[k]
        return None

    def priced_tests(self):
        """Tests that have a separate charge (zero-MRP rows are derived parameters)."""
        return [t for t in self.tests if t["mrp"] > 0]


# ---------------------------------------------------------------- loading
def _find_discount(wb):
    """'Default individual-test package discount' on the Settings sheet, or None."""
    if _SETTINGS not in wb.sheetnames:
        return None
    for row in wb[_SETTINGS].iter_rows(values_only=True):
        if row and isinstance(row[0], str) and "individual-test package discount" in row[0].lower():
            return _num(row[1]) if len(row) > 1 else None
    return None


def load(path):
    """Read the price list. Raises CatalogueError with a readable message."""
    try:
        import openpyxl
    except ImportError:
        raise CatalogueError("The price list needs the 'openpyxl' package "
                             "(run: pip install openpyxl).")
    path = Path(path)
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception as e:                                   # missing, locked, not an xlsx...
        raise CatalogueError("Could not open the price list %s: %s" % (path.name, e))
    try:
        if _SHEET not in wb.sheetnames:
            raise CatalogueError("The price list has no sheet named '%s'." % _SHEET)
        rows = wb[_SHEET].iter_rows(values_only=True)
        header = next(rows, None) or ()
        pos = {}
        for key, title in _COLS.items():
            for i, h in enumerate(header):
                if isinstance(h, str) and h.strip().lower() == title.lower():
                    pos[key] = i
                    break
        missing = [_COLS[k] for k in ("code", "name", "mrp") if k not in pos]
        if missing:
            raise CatalogueError("The '%s' sheet has no column: %s." % (_SHEET, ", ".join(missing)))

        discount = _find_discount(wb)
        note = ""
        if _SETTINGS in wb.sheetnames:
            first = next(wb[_SETTINGS].iter_rows(max_row=1, values_only=True), None)
            note = str(first[0]) if first and first[0] else ""

        def cell(r, key):
            i = pos.get(key)
            return r[i] if i is not None and i < len(r) else None

        tests, skipped, seen = [], [], set()
        for n, r in enumerate(rows, start=2):
            code = str(cell(r, "code") or "").strip()
            if not code:
                continue                                      # blank or footer row
            name = " ".join(str(cell(r, "name") or "").split())
            mrp = _num(cell(r, "mrp"))
            if not name or mrp is None or mrp < 0 or code in seen:
                skipped.append("row %d (%s)" % (n, code))
                continue
            pkg = _num(cell(r, "pkg"))
            if pkg is None:                                   # formula was never calculated
                pkg = _round_to(mrp * (1 - (discount or 0)), 5) if mrp else 0
            seen.add(code)
            tests.append({"category": str(cell(r, "category") or "").strip(),
                          "code": code, "name": name,
                          "abbr": " ".join(str(cell(r, "abbr") or "").split()),
                          "mrp": mrp, "pkg": pkg})
    finally:
        wb.close()
    if not tests:
        raise CatalogueError("The price list has no tests.")
    return Catalogue(tests, source=path.name, note=note, skipped=skipped)


# ---------------------------------------------------------------- for the AI prompt
def prompt_block(cat):
    """The catalogue as text for the AI prompt (names only, no prices)."""
    by_cat = {}
    for t in cat.priced_tests():
        by_cat.setdefault(t["category"] or "Other", []).append(t["name"])
    lines = [
        "",
        "LABORATORY TEST LIST. For the \"tests\" array, choose tests from this list wherever "
        "possible and write each test's \"name\" EXACTLY as printed here, character for "
        "character (it is used to look up the price). If you need a test that is not in this "
        "list you may still advise it, using its usual name. Do not list the same test twice, "
        "and do not list a single test separately when it is already part of a panel you "
        "list (for example, do not add Total Cholesterol when you list Lipid Profile).",
    ]
    for category, names in by_cat.items():
        lines.append("[%s]" % category)
        lines += names
    return "\n".join(lines)


# ---------------------------------------------------------------- pricing an answer
def _total():
    return {"mrp": 0, "pkg": 0, "priced": 0, "included": 0, "unpriced": 0, "duplicate": 0}


def price_analysis(cat, analysis):
    """Price every suggested test of a structured answer.

    Returns {"lines": {priority: [line, ...]}, "totals": {priority/"all": {...}},
             "unpriced": [names], "duplicates": [names], "source": file name}
    where each line (same order as the tests of that priority in the answer) is
      {"name", "code", "matched", "mrp", "pkg", "status"}
    and status is "priced", "included" (derived parameter, no separate charge),
    "unpriced" (not in the catalogue) or "duplicate" (same catalogue test already
    listed in this or a higher priority band; counted once in the totals).
    """
    tests = (analysis or {}).get("tests") or []
    lines = {p: [] for p in PRIORITIES}
    totals = {p: _total() for p in PRIORITIES}
    totals["all"] = _total()
    seen = set()
    unpriced, duplicates = [], []
    for p in PRIORITIES:
        for t in tests:
            if t.get("priority") != p:
                continue
            name = t.get("name", "")
            e = cat.match(name)
            line = {"name": name, "code": None, "matched": None, "mrp": None, "pkg": None}
            if e is None:
                line["status"] = "unpriced"
                unpriced.append(name)
            else:
                line.update(code=e["code"], matched=e["name"], mrp=e["mrp"], pkg=e["pkg"])
                if e["code"] in seen:
                    line["status"] = "duplicate"
                    duplicates.append(name)
                else:
                    seen.add(e["code"])
                    line["status"] = "priced" if e["mrp"] > 0 else "included"
            lines[p].append(line)
            for tot in (totals[p], totals["all"]):
                tot[line["status"]] += 1
                if line["status"] == "priced":
                    tot["mrp"] += line["mrp"]
                    tot["pkg"] += line["pkg"]
    return {"lines": lines, "totals": totals, "unpriced": unpriced,
            "duplicates": duplicates, "source": cat.source}
