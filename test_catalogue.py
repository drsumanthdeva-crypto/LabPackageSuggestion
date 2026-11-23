"""Tests for catalogue.py and the price display in report_pdf.py.

Run:  python3 test_catalogue.py        (needs openpyxl; no tkinter, no internet)

It builds a small price list of its own, so it does not depend on your real
Preventive_Health_Test_Catalogue.xlsx.
"""
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openpyxl
import catalogue as c
import report_pdf

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(extra)[:500]) if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

TMP = Path(tempfile.mkdtemp())

# ---------------------------------------------------------------- a small price list
# Columns deliberately NOT in the usual order: they are found by header name.
HEAD = ["S.No", "Test Name", "Test Code", "Category", "Standard Name / Abbreviation", "MRP (Rs)", "Package Price (Rs)"]
ROWS = [
    # name, code, category, abbr, mrp, package price (None = a formula with no saved value)
    ("Complete Blood Count", "HEM001", "Hematology", "CBC", 250, 175),
    ("Glycated Hemoglobin", "DIA004", "Diabetes", "HbA1c", 450, None),          # 450 * 0.7 = 315
    ("Lipid Profile (Complete)", "LIP014", "Lipid", "Lipid Profile", 450, 315),
    ("Vitamin B12 (Cobalamin)", "VIT002", "Vitamins", "Vit B12", 800, 560),
    ("Vitamin B1 (Thiamine)", "VIT003", "Vitamins", "Vit B1", 700, 490),
    ("Hepatitis B Surface Antigen", "INF001", "Infection", "HBsAg", 300, 210),
    ("Hepatitis C Antibody", "INF002", "Infection", "HCV", 600, 420),
    ("Calcium, Total", "ELE006", "Electrolytes", "Ca", 90, 65),
    ("Calcium (Bone Panel)", "BON001", "Bone", "Ca", 0, 0),                    # same abbreviation -> ambiguous
    ("LDL Cholesterol (Calculated)", "LIP004", "Lipid", "LDL-C", 0, 0),        # derived: no charge
    ("Triglycerides", "LIP002", "Lipid", "TG", 120, 85),
    ("Thyroid Profile", "THY006", "Thyroid", "TFT", 1234567, 1000000),         # large, for grouping
]

def build(path, with_pkg_values=True):
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Test Catalogue"; ws.append(HEAD)
    for i, (name, code, cat, abbr, mrp, pkg) in enumerate(ROWS, start=1):
        if pkg is None or not with_pkg_values:
            pkg = "=ROUND(F%d*(1-Settings!$B$3)/5,0)*5" % (i + 1)              # formula, never calculated here
        ws.append([i, name, code, cat, abbr, mrp, pkg])
    ws.append([None] * 7)
    ws.append(["Total tests listed:", None, None, len(ROWS)])                   # footer row: must be ignored
    st = wb.create_sheet("Settings")
    st.append(["Pricing Settings (TEST DATA - placeholder prices only)"])
    st.append([])
    st.append(["Default individual-test package discount", 0.3, "note"])
    wb.save(str(path))

P = TMP / "list.xlsx"; build(P)
cat = c.load(P)

# ---------------------------------------------------------------- loading
check("loads every test row, ignores footer", len(cat.tests) == len(ROWS), len(cat.tests))
check("zero-charge rows are kept but not 'priced'", len(cat.priced_tests()) == len(ROWS) - 2)
check("columns found by header, not position", cat.match("CBC")["mrp"] == 250 and cat.match("CBC")["category"] == "Hematology")
check("package price with no saved value is worked out from Settings (30% off, nearest 5)", cat.match("HbA1c")["pkg"] == 315, cat.match("HbA1c"))
check("package price with a saved value is used as is", cat.match("CBC")["pkg"] == 175)
check("sheet note read, test-data flag set", cat.is_placeholder and "TEST DATA" in cat.note)
check("source is the file name", cat.source == "list.xlsx")
P2 = TMP / "formulas_only.xlsx"; build(P2, with_pkg_values=False)
check("all package prices rebuilt when none are saved", [t["pkg"] for t in c.load(P2).tests if t["code"] == "HEM001"] == [175])

for label, setup, text in [
    ("no such file", lambda: None, "Could not open"),
]:
    try: c.load(TMP / "nope.xlsx"); ok = False; msg = ""
    except c.CatalogueError as e: ok = True; msg = str(e)
    check("error: " + label, ok and text in msg, msg)
bad = TMP / "bad.xlsx"; bad.write_bytes(b"this is not an xlsx")
try: c.load(bad); ok = False
except c.CatalogueError: ok = True
check("error: corrupt file gives CatalogueError (not a crash)", ok)
wb = openpyxl.Workbook(); wb.active.title = "Other"; wb.save(str(TMP / "nosheet.xlsx"))
try: c.load(TMP / "nosheet.xlsx"); ok = False; msg = ""
except c.CatalogueError as e: ok = True; msg = str(e)
check("error: missing sheet named", ok and "Test Catalogue" in msg, msg)
wb = openpyxl.Workbook(); wb.active.title = "Test Catalogue"; wb.active.append(["Name", "Price"]); wb.save(str(TMP / "nocols.xlsx"))
try: c.load(TMP / "nocols.xlsx"); ok = False; msg = ""
except c.CatalogueError as e: ok = True; msg = str(e)
check("error: missing columns named", ok and "Test Code" in msg, msg)

# ---------------------------------------------------------------- matching (exact only)
def code(q):
    m = cat.match(q); return m and m["code"]
check("match: exact name", code("Complete Blood Count") == "HEM001")
check("match: ignores case, spacing and punctuation", code("  complete   BLOOD count. ") == "HEM001")
check("match: abbreviation", code("HbA1c") == "DIA004" and code("hba1c") == "DIA004")
check("match: test code", code("hem001") == "HEM001")
check("match: abbreviation in brackets", code("Complete Blood Count (CBC)") == "HEM001" and code("Blood test (CBC)") == "HEM001")
check("match: name without the trailing (...)", code("Lipid Profile") == "LIP014" and code("Vitamin B12") == "VIT002")
check("match: 'Vitamin B1' is NOT mistaken for B12 (and the reverse)", code("Vitamin B1") == "VIT003" and code("Vitamin B12") == "VIT002")
check("match: near-miss names get NO price (no fuzzy matching)", code("Vitamin B-12 level") is None and code("Hepatitis A Antibody") is None and code("Hepatitis") is None)
check("match: B vs C hepatitis kept apart", code("HBsAg") == "INF001" and code("HCV") == "INF002")
check("match: ambiguous abbreviation is not guessed", code("Ca") is None)
check("match: a no-charge variant is not found by its short name", code("Calcium") is None and code("Calcium, Total") == "ELE006")
check("match: unknown / empty", code("Nonexistent") is None and code("") is None and cat.match(None) is None)

# ---------------------------------------------------------------- number format
check("inr: Indian digit grouping", [c.inr(x) for x in (0, 999, 1000, 12345, 100000, 1234567)] == ["0", "999", "1,000", "12,345", "1,00,000", "12,34,567"])
check("inr: paise only when present", c.inr(99.5) == "99.50" and c.inr(250.0) == "250" and c.inr(99.999) == "100")

# ---------------------------------------------------------------- the AI prompt list
blk = c.prompt_block(cat)
check("prompt: lists charged tests by category", "[Hematology]\nComplete Blood Count" in blk and "[Vitamins]" in blk)
check("prompt: leaves out no-charge derived rows", "LDL Cholesterol (Calculated)" not in blk and "Calcium (Bone Panel)" not in blk)
check("prompt: contains no prices", "250" not in blk and "175" not in blk)
check("prompt: asks for exact names", "EXACTLY" in blk)

# ---------------------------------------------------------------- pricing an answer
def T(name, prio, reason=""): return {"name": name, "priority": prio, "reason": reason}
A = {"narrative": "n", "sections": [], "tests": [
    T("Complete Blood Count", "top", "anaemia"), T("HbA1c", "top"),
    T("Vitamin D panel", "moderate"), T("CBC", "moderate", "again"), T("LDL Cholesterol (Calculated)", "moderate"),
    T("Vitamin B12", "optional"), T("Triglycerides", "optional")]}
pr = c.price_analysis(cat, A)
st = {p: [l["status"] for l in pr["lines"][p]] for p in c.PRIORITIES}
check("pricing: one line per test, in order", [l["name"] for l in pr["lines"]["top"]] == ["Complete Blood Count", "HbA1c"])
check("pricing: statuses", st == {"top": ["priced", "priced"], "moderate": ["unpriced", "duplicate", "included"], "optional": ["priced", "priced"]}, st)
check("pricing: top total", pr["totals"]["top"]["mrp"] == 700 and pr["totals"]["top"]["pkg"] == 490)
check("pricing: duplicate / unpriced / included add nothing", pr["totals"]["moderate"]["mrp"] == 0 and pr["totals"]["moderate"]["pkg"] == 0)
check("pricing: overall total = sum of bands", pr["totals"]["all"]["mrp"] == 700 + 920 and pr["totals"]["all"]["pkg"] == 490 + 645, pr["totals"]["all"])
check("pricing: counts", pr["totals"]["all"]["priced"] == 4 and pr["totals"]["all"]["unpriced"] == 1 and pr["totals"]["all"]["duplicate"] == 1 and pr["totals"]["all"]["included"] == 1)
check("pricing: lists of problem tests", pr["unpriced"] == ["Vitamin D panel"] and pr["duplicates"] == ["CBC"])
check("pricing: same test in a lower band is the duplicate, higher band keeps the price", pr["lines"]["top"][0]["status"] == "priced")
check("pricing: empty answer is fine", c.price_analysis(cat, {"tests": []})["totals"]["all"]["priced"] == 0 and c.price_analysis(cat, None)["lines"]["top"] == [])

# ---------------------------------------------------------------- the PDF
meta = {"client_id": "CL-1", "analysis": A, "pricing": pr}
h = report_pdf.build_html("x", meta)
check("pdf: price columns in every band", h.count("MRP (&#8377;)") == 4 and h.count("Package price (&#8377;)") == 4)  # 3 bands + totals
check("pdf: per-test MRP and package price", '<td class="n">250</td><td class="n">175</td>' in h and '<td class="n">450</td><td class="n">315</td>' in h)
check("pdf: unpriced / duplicate / included wording", "Price on request" in h and "Listed above" in h and "Included (no separate charge)" in h)
check("pdf: 'Priced as' shown only when the name differs", "Priced as: Glycated Hemoglobin" in h and "Priced as: Complete Blood Count" in h and "Priced as: Triglycerides" not in h)
check("pdf: totals block", "Cost of the recommended tests" in h and '<td class="n">1,620</td><td class="n">1,135</td>' in h, h[h.find("price-sum"):][:900])
check("pdf: savings line", "You save &#8377;485 (30%)" in h)
check("pdf: notes about unpriced and duplicates", "1 test(s) marked" in h and "counted once" in h)
check("pdf: totals block comes before the sign-off and after the bands", h.index("Optional</div>") < h.index("Cost of the recommended tests"))
check("pdf: moderate row left out of totals (nothing priced there)", "Moderate priority</td>" not in h)
big = {"tests": [T("Thyroid Profile", "top")]}
hb = report_pdf.build_html("x", {"analysis": big, "pricing": c.price_analysis(cat, big)})
check("pdf: large amounts use Indian grouping", "12,34,567" in hb and "10,00,000" in hb)
tricky = {"tests": [T("Complete <b>Blood</b> Count", "top", "<script>x</script>")]}
ht = report_pdf.build_html("x", {"analysis": tricky, "pricing": c.price_analysis(cat, tricky)})
check("pdf: names and reasons stay HTML-escaped", "&lt;script&gt;" in ht and "<script>x" not in ht and "Complete <b>Blood" not in ht)
# without prices: exactly the old layout
hn = report_pdf.build_html("x", {"analysis": A})
check("pdf: no pricing -> bullet list, no price table, no totals", "<li><b>Complete Blood Count</b>" in hn and "Cost of the recommended tests" not in hn and '<table class="px">' not in hn)
# prices that do not line up with the tests are ignored, never mis-printed
bad_pr = {"lines": {"top": [pr["lines"]["top"][0]], "moderate": [], "optional": []}, "totals": pr["totals"]}
hm = report_pdf.build_html("x", {"analysis": A, "pricing": bad_pr})
check("pdf: misaligned prices -> printed without prices", "Cost of the recommended tests" not in hm and '<table class="px">' not in hm)
# nothing priced at all: no empty totals box
none = {"tests": [T("Unknown thing", "top")]}
hz = report_pdf.build_html("x", {"analysis": none, "pricing": c.price_analysis(cat, none)})
check("pdf: all 'price on request' -> no totals box", "Price on request" in hz and "Cost of the recommended tests" not in hz)
hf = report_pdf.build_html("Intro\n\nTop priority\n- CBC", {"client_id": "C", "pricing": pr})
check("pdf: free-text answer is unaffected by pricing", "Recommended laboratory tests" in hf and "Cost of the recommended tests" not in hf)

print("\n%d failure(s)" % len(FAILS)); sys.exit(1 if FAILS else 0)
