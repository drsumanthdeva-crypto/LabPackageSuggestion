"""Turn the AI answer into a colour-coded PDF.

No third-party packages are needed. The answer is converted to an HTML page
and printed to PDF by Microsoft Edge or Google Chrome (headless). A browser
is used on purpose: it shapes Telugu and Hindi text correctly, which simple
Python PDF libraries do not.

If no browser can be driven automatically, an HTML file is saved and opened in
the default browser with the print dialog ("Save as PDF") already open.
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path

from catalogue import inr, norm     # price formatting / name comparison (catalogue.py sits next to this file)

# key, title shown in the PDF, heading pattern, border colour, background, text on header
PRIORITIES = [
    ("top", "Top priority", r"^(top|high)[\s\-]*priority", "#C62828", "#FDECEA"),
    ("moderate", "Moderate priority", r"^(moderate|medium)[\s\-]*priority", "#E65100", "#FFF3E0"),
    ("optional", "Optional", r"^optional", "#2E7D32", "#E8F5E9"),
]

_LEAD = re.compile(r"^[\s#>*_\-\d.)(:]+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)")
# A heading must be the WHOLE line: the heading words, optionally followed by
# "tests" / "laboratory tests" / "investigations" and/or a short "(note)".
# Lines such as "- Optional: Vitamin D panel" or "High priority follow-up: ..."
# are test items or narrative, not headings.
_HEAD_TAIL = r"(?:\s+(?:laboratory\s+)?(?:tests?|investigations?))?(?:\s*\([^)]*\))?"


# ---------------------------------------------------------------- parsing
def _heading_key(line):
    """Return 'top' / 'moderate' / 'optional' if the line is such a heading."""
    clean = _LEAD.sub("", line).strip().strip("*_:# ").lower()
    if not clean or len(clean) > 60:
        return None
    for key, _title, pattern, _c, _b in PRIORITIES:
        if re.fullmatch(pattern + _HEAD_TAIL, clean):
            return key
    return None


def split_response(text):
    """Split the answer into (narrative, {key: [lines]}).

    The list of tests starts at the LAST 'Top priority' heading, so a similar
    phrase earlier in the narrative is not mistaken for it.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    start = None
    for i, line in enumerate(lines):
        if _heading_key(line) == "top":
            start = i
    if start is None:
        return text.strip(), {}

    narrative = "\n".join(lines[:start]).rstrip()
    narrative = re.sub(r"(\n\s*(?:-{3,}|\*{3,}|_{3,}))+\s*$", "", narrative).rstrip()

    sections = {k: [] for k, *_ in PRIORITIES}
    current = "top"
    for line in lines[start + 1:]:
        key = _heading_key(line)
        if key:
            current = key
        elif line.strip() and not re.match(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", line):
            sections[current].append(line)
    return narrative, sections


# ---------------------------------------------------------------- markdown
def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    return s


def md_to_html(text):
    out, para, items, kind, table = [], [], [], None, []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % "<br>".join(_inline(p) for p in para))
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            out.append("<%s>%s</%s>" % (kind, "".join("<li>%s</li>" % _inline(i) for i in items), kind))
            items.clear()
        kind = None

    def flush_table():
        if not table:
            return
        rows = []
        for r in table:
            cells = [c.strip() for c in r.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append(cells)
        if rows:
            head = "".join("<th>%s</th>" % _inline(c) for c in rows[0])
            body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r) for r in rows[1:])
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, body))
        table.clear()

    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("|"):
            flush_para(); flush_list()
            table.append(s)
            continue
        flush_table()
        if not s:
            flush_para(); flush_list()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            flush_para(); flush_list()
            level = min(len(m.group(1)) + 2, 5)
            out.append("<h%d>%s</h%d>" % (level, _inline(m.group(2)), level))
            continue
        if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", s):
            flush_para(); flush_list()
            out.append("<hr>")
            continue
        b = _BULLET.match(line)
        if b:
            flush_para()
            new_kind = "ol" if re.match(r"^\s*\d", line) else "ul"
            if kind and kind != new_kind:
                flush_list()
            kind = new_kind
            items.append(b.group(1))
            continue
        flush_list()
        para.append(s)
    flush_para(); flush_list(); flush_table()
    return "\n".join(out)


# ---------------------------------------------------------------- html
_NO_CHARGE_NOTE = {
    "included": "Included (no separate charge)",
    "unpriced": "Price on request",
    "duplicate": "Listed above",
}


def _price_cells(line):
    """The MRP and package-price cells of one test row."""
    if line.get("status") == "priced":
        return '<td class="n">%s</td><td class="n">%s</td>' % (inr(line["mrp"]), inr(line["pkg"]))
    return '<td class="n note" colspan="2">%s</td>' % _NO_CHARGE_NOTE.get(line.get("status"), "Price on request")


def _tests_html(items, priced=None):
    """Tests from the structured answer (all text escaped).

    Without prices: a bullet list. With prices (one price line per test, from
    catalogue.price_analysis): a table with MRP and package price columns.
    """
    if not items:
        return ""
    if not priced or len(priced) != len(items):
        lis = []
        for t in items:
            reason = (" &ndash; " + html.escape(t["reason"])) if t.get("reason") else ""
            lis.append("<li><b>%s</b>%s</li>" % (html.escape(t["name"]), reason))
        return "<ul>%s</ul>" % "".join(lis)
    rows = []
    for t, line in zip(items, priced):
        reason = (" &ndash; " + html.escape(t["reason"])) if t.get("reason") else ""
        also = ""
        if line.get("matched") and norm(line["matched"]) != norm(t["name"]):
            also = '<div class="why">Priced as: %s</div>' % html.escape(line["matched"])
        rows.append("<tr><td><b>%s</b>%s%s</td>%s</tr>" % (
            html.escape(t["name"]), reason, also, _price_cells(line)))
    return ('<table class="px"><thead><tr><th>Test</th><th class="n">MRP (&#8377;)</th>'
            '<th class="n">Package price (&#8377;)</th></tr></thead><tbody>%s</tbody></table>'
            % "".join(rows))


def _price_summary_html(pricing):
    """Totals per priority band and overall; '' when nothing could be priced."""
    totals = (pricing or {}).get("totals") or {}
    allt = totals.get("all") or {}
    if not allt.get("priced"):
        return ""
    rows = []
    for key, title, _p, color, _bg in PRIORITIES:
        t = totals.get(key) or {}
        if t.get("priced"):
            rows.append('<tr><td><i class="dot" style="background:%s"></i>%s</td><td class="n">%d</td>'
                        '<td class="n">%s</td><td class="n">%s</td></tr>' % (
                            color, title, t["priced"], inr(t["mrp"]), inr(t["pkg"])))
    rows.append('<tr class="tot"><td>Total</td><td class="n">%d</td><td class="n">%s</td>'
                '<td class="n">%s</td></tr>' % (allt["priced"], inr(allt["mrp"]), inr(allt["pkg"])))
    notes = []
    save = allt["mrp"] - allt["pkg"]
    if save > 0 and allt["mrp"] > 0:
        notes.append("<b>You save &#8377;%s (%d%%)</b> with the package prices."
                     % (inr(save), round(100.0 * save / allt["mrp"])))
    if allt.get("unpriced"):
        notes.append("%d test(s) marked &ldquo;Price on request&rdquo; are not included in these totals."
                     % allt["unpriced"])
    if allt.get("duplicate"):
        notes.append("A test listed more than once is counted once.")
    notes.append("Prices are in Indian Rupees (&#8377;), from the laboratory price list.")
    return ('<section class="price-sum"><div class="price-sum-title">Cost of the recommended tests</div>'
            '<table class="px"><thead><tr><th>Priority</th><th class="n">Tests</th>'
            '<th class="n">MRP (&#8377;)</th><th class="n">Package price (&#8377;)</th></tr></thead>'
            '<tbody>%s</tbody></table><div class="price-notes">%s</div></section>'
            % ("".join(rows), "<br>".join(notes)))


def _sections_html(sections):
    """Headed sections from the structured answer: <h3> heading + <ul> of points."""
    out = []
    for s in sections or []:
        if s.get("heading"):
            out.append("<h3>%s</h3>" % _inline(s["heading"]))
        pts = s.get("points") or []
        if pts:
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % _inline(p) for p in pts))
    return "\n".join(out)


def build_html(text, meta, auto_print=False):
    analysis = meta.get("analysis") if isinstance(meta.get("analysis"), dict) else None
    if analysis:                      # structured (JSON) answer: no heading parsing needed
        narrative, sections = analysis.get("narrative", ""), {}
        narrative_html = md_to_html(narrative) + _sections_html(analysis.get("sections"))
    else:                             # free-text answer (e.g. a typed question)
        narrative, sections = split_response(text)
        narrative_html = md_to_html(narrative)
    when = datetime.now().strftime("%d %b %Y, %H:%M")

    meta_bits = []
    if meta.get("client_id"):
        meta_bits.append("Client ID: <b>%s</b>" % html.escape(str(meta["client_id"])))
    meta_bits.append("Generated: %s" % when)
    if meta.get("language"):
        meta_bits.append("Language: %s" % html.escape(meta["language"]))
    if meta.get("model"):
        meta_bits.append("Model: %s" % html.escape(meta["model"]))

    pricing = None
    if analysis and isinstance(meta.get("pricing"), dict):
        pricing = meta["pricing"]
        lines = pricing.get("lines") or {}
        for k, *_ in PRIORITIES:          # prices must line up with the tests, else print none
            n = len([t for t in analysis.get("tests", []) if t.get("priority") == k])
            if len(lines.get(k, [])) != n:
                pricing = None
                break

    if analysis:
        bodies = {k: _tests_html([t for t in analysis.get("tests", []) if t.get("priority") == k],
                                 ((pricing or {}).get("lines") or {}).get(k))
                  for k, *_ in PRIORITIES}
        has_tests = bool(analysis.get("tests"))
    else:
        bodies = {k: md_to_html("\n".join(sections.get(k, []))) for k, *_ in PRIORITIES}
        has_tests = any(sections.values())

    blocks = []
    if has_tests:
        blocks.append('<h2 class="list-title">Recommended laboratory tests</h2>')
        blocks.append('<div class="legend">%s</div>' % "".join(
            '<span><i style="background:%s"></i>%s</span>' % (color, title)
            for _k, title, _p, color, _bg in PRIORITIES))
        for key, title, _p, color, bg in PRIORITIES:
            body = bodies.get(key) or "<p><i>None listed.</i></p>"
            blocks.append(
                '<section class="prio" style="border-color:%s;background:%s">'
                '<div class="prio-head" style="background:%s">%s</div>'
                '<div class="prio-body">%s</div></section>' % (color, bg, color, title, body))
        if pricing:
            blocks.append(_price_summary_html(pricing))

    sig = meta.get("signature")
    if sig:
        status = "Edited by the physician" if sig.get("edited") else "Approved without changes"
        quals = html.escape(sig.get("qualification") or "")
        foot = (
            '<div class="sign"><div class="sign-title">&#10004; Reviewed and approved by the physician</div>'
            '<div class="sign-name">%s</div>'
            '<div>%sRegistration No.: <b>%s</b></div>'
            '<div>Signed electronically (password verified) on <b>%s</b> &middot; %s</div>'
            '<div class="sign-id">Document ID: %s</div>'
            '<div class="sign-note">Drafted with AI assistance. The recommendations above are those '
            'approved by the physician named here.</div></div>' % (
                html.escape(sig.get("name", "")),
                (quals + " &middot; ") if quals else "",
                html.escape(sig.get("reg_no", "")),
                html.escape(sig.get("signed_at", "").replace("T", " ")),
                status,
                html.escape(sig.get("digest", "")[:24])))
    else:
        foot = ('<div class="foot">AI-generated suggestions for review by a qualified physician. '
                'This is not a diagnosis.</div>')

    script = "<script>window.onload=function(){setTimeout(function(){window.print()},400)}</script>" if auto_print else ""
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Preventive health checkup advice</title>
<style>
@page {{ size: A4; margin: 14mm 14mm 16mm 14mm; }}
* {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; box-sizing: border-box; }}
body {{ font-family: "Nirmala UI","Segoe UI","Noto Sans Telugu","Noto Sans Devanagari","Mangal",Arial,sans-serif;
        font-size: 11pt; line-height: 1.5; color: #202124; margin: 0; }}
h1 {{ font-size: 18pt; margin: 0 0 2px; color: #263B63; }}
.meta {{ font-size: 9pt; color: #5f6368; border-bottom: 2px solid #263B63; padding-bottom: 6px; margin-bottom: 12px; }}
.meta span + span::before {{ content: "  |  "; }}
h3 {{ font-size: 13pt; color: #263B63; margin: 14px 0 4px; page-break-after: avoid; }}
h4, h5 {{ font-size: 11.5pt; margin: 10px 0 4px; }}
p {{ margin: 4px 0 8px; }}
ul, ol {{ margin: 4px 0 10px; padding-left: 22px; }}
li {{ margin: 2px 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0 12px; font-size: 10pt; }}
th, td {{ border: 1px solid #c9ced6; padding: 4px 6px; text-align: left; vertical-align: top; }}
th {{ background: #eef1f6; }}
hr {{ border: 0; border-top: 1px solid #d0d4da; margin: 10px 0; }}
code {{ background: #f1f3f4; padding: 0 3px; border-radius: 3px; }}
.list-title {{ font-size: 14pt; color: #263B63; margin: 18px 0 6px; page-break-after: avoid; }}
.legend {{ font-size: 9pt; margin-bottom: 8px; }}
.legend span {{ margin-right: 16px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }}
.prio {{ border: 1.5px solid; border-radius: 6px; margin: 0 0 10px; overflow: hidden; page-break-inside: avoid; }}
.prio-head {{ color: #fff; font-weight: 700; padding: 5px 10px; font-size: 11.5pt; }}
.prio-body {{ padding: 4px 10px 2px; }}
.prio-body ul, .prio-body ol {{ margin: 4px 0 6px; }}
.px {{ margin: 4px 0 6px; font-size: 10pt; }}
.px th, .px td {{ border: 0; border-bottom: 1px solid rgba(0,0,0,.13); padding: 4px 6px; }}
.px th {{ background: transparent; font-size: 9pt; color: #5f6368; }}
.px tr:last-child td {{ border-bottom: 0; }}
.px tr {{ page-break-inside: avoid; }}
.px .n {{ text-align: right; white-space: nowrap; width: 1%; padding-left: 16px; }}
.px .note {{ color: #5f6368; font-style: italic; }}
.why {{ font-size: 9pt; color: #5f6368; }}
.price-sum {{ margin: 14px 0 0; border: 1.5px solid #263B63; border-radius: 6px; padding: 6px 12px 8px;
              background: #F4F6FA; page-break-inside: avoid; }}
.price-sum-title {{ font-weight: 700; color: #263B63; font-size: 11.5pt; }}
.price-sum .tot td {{ font-weight: 700; border-top: 1.5px solid #263B63; }}
.price-notes {{ font-size: 9pt; color: #5f6368; margin-top: 4px; }}
.dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 6px; }}
.sign {{ margin-top: 18px; border: 1.5px solid #263B63; border-radius: 6px; background: #F4F6FA;
         padding: 8px 14px; font-size: 10pt; page-break-inside: avoid; }}
.sign-title {{ font-weight: 700; color: #263B63; margin-bottom: 2px; }}
.sign-name {{ font-size: 13pt; font-weight: 700; }}
.sign-id {{ font-family: Consolas, monospace; font-size: 8.5pt; color: #5f6368; margin-top: 3px; }}
.sign-note {{ font-size: 8.5pt; color: #5f6368; margin-top: 4px; }}
.foot {{ margin-top: 16px; font-size: 8.5pt; color: #5f6368; border-top: 1px solid #d0d4da; padding-top: 6px; }}
</style></head><body>
<h1>Preventive Health Checkup &ndash; Advice</h1>
<div class="meta">{meta}</div>
<div class="narrative">{narrative}</div>
{blocks}
{foot}
{script}
</body></html>""".format(
        meta="".join("<span>%s</span>" % b for b in meta_bits),
        narrative=narrative_html,
        blocks="\n".join(blocks),
        foot=foot,
        script=script)


# ---------------------------------------------------------------- pdf
def find_browser():
    """Path of Edge or Chrome, or None."""
    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                ]
    elif sys.platform == "darwin":
        candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for c in candidates:
        if os.path.isfile(c):
            return c
    for name in ("msedge", "microsoft-edge", "google-chrome", "chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def html_to_pdf(html_path, pdf_path, browser):
    """Print an HTML file to PDF with a headless browser. True on success."""
    pdf_path = Path(pdf_path)
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except OSError:
            return False
    with tempfile.TemporaryDirectory() as profile:   # separate profile: works even if the browser is already open
        cmd = [browser, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
               "--user-data-dir=" + profile, "--print-to-pdf=" + str(pdf_path),
               Path(html_path).resolve().as_uri()]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return pdf_path.is_file() and pdf_path.stat().st_size > 0


def export(text, meta, pdf_path):
    """Create the report. Returns ('pdf', path) or ('html', path)."""
    pdf_path = Path(pdf_path)
    browser = find_browser()
    if browser:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "report.html"
            page.write_text(build_html(text, meta), encoding="utf-8")
            if html_to_pdf(page, pdf_path, browser):
                return "pdf", pdf_path
    # fallback: HTML that opens the print dialog
    page = pdf_path.with_suffix(".html")
    page.write_text(build_html(text, meta, auto_print=True), encoding="utf-8")
    webbrowser.open(page.resolve().as_uri())
    return "html", page
