"""Tests for the JSON-based flow. Run:  xvfb-run -a python3 test_v2.py   (needs tkinter)"""
import json, os, sys, tempfile, time, types
from pathlib import Path
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_schema, report_pdf, doctor_auth
import gemini_assistant as ga

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(extra)) if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

GOOD = {"narrative": "Advice for this client: please have regular check-up tests done.",
        "sections": [{"heading": "Medical Evaluation and Diagnostic Recommendations",
                      "points": ["Cardiovascular: check blood pressure <b>and lipids", "Endocrine: screen for diabetes"]},
                     {"heading": "Key Areas of Clinical Assessment", "points": ["- Fatigue work-up"]},
                     {"heading": "Non-Laboratory Diagnostic Investigations Recommended", "points": ["ECG"]}],
        "tests": [{"name": "CBC", "priority": "top", "reason": "anaemia screen"},
                  {"name": "Optional: Vitamin D panel", "priority": "moderate", "reason": "low sun exposure"},
                  {"name": "High priority follow-up: repeat HbA1c", "priority": "top", "reason": "borderline last time"},
                  {"name": "TSH <b>", "priority": "HIGH", "reason": ""},
                  {"name": "Vitamin B12", "priority": "optional", "reason": "vegetarian diet"}]}

# ---------------------------------------------------------------- ai_schema
d = ai_schema.parse_ai_json(json.dumps(GOOD, ensure_ascii=False))
check("schema: priority synonyms normalised (HIGH -> top)", [t["priority"] for t in d["tests"]] == ["top","moderate","top","top","optional"])
check("schema: code-fenced JSON accepted", ai_schema.parse_ai_json("```json\n" + json.dumps(GOOD) + "\n```")["tests"][0]["name"] == "CBC")
for label, raw in [("truncated JSON", json.dumps(GOOD)[:-15]), ("empty", ""), ("not an object", "[1,2]"),
                   ("no tests list", '{"narrative":"x"}'), ("bad priority", '{"narrative":"x","tests":[{"name":"A","priority":"urgent","reason":""}]}'),
                   ("test not object", '{"narrative":"x","tests":["CBC"]}')]:
    try: ai_schema.parse_ai_json(raw); ok = False
    except ValueError: ok = True
    check("schema: rejects " + label, ok)
check("schema: empty tests list allowed", ai_schema.parse_ai_json('{"narrative":"n","tests":[]}')["tests"] == [])
# round trip through the editable box format
txt = ai_schema.to_text(d)
back = ai_schema.analysis_from_boxes(ai_schema.narrative_to_text(d), {p: ai_schema.tests_to_lines(d["tests"], p) for p in ai_schema.PRIORITIES})
check("schema: boxes round-trip leaves text unchanged (no false 'edited')", ai_schema.to_text(back) == txt)
check("schema: sections survive the round trip exactly", back["sections"] == d["sections"] and back["narrative"] == d["narrative"])
check("schema: 3 sections, bullet char stripped from point", len(d["sections"]) == 3 and d["sections"][1]["points"] == ["Fatigue work-up"])
md = ai_schema.parse_ai_json(json.dumps({"narrative": "Summary line.\n\n## Heart\n- BP check\n- ECG", "tests": []}))
check("schema: '## Heading' inside the narrative becomes a section", md["narrative"] == "Summary line." and md["sections"] == [{"heading": "Heart", "points": ["BP check", "ECG"]}])
check("schema: answer without 'sections' still valid (older format)", ai_schema.parse_ai_json('{"narrative":"n","tests":[]}')["sections"] == [])
check("schema: narrative-only text -> no sections", ai_schema.text_to_narrative("Just a paragraph.\nSecond line.") == ("Just a paragraph.\nSecond line.", []))
for label, raw in [("sections not a list", '{"narrative":"n","sections":"x","tests":[]}'), ("section not an object", '{"narrative":"n","sections":["x"],"tests":[]}')]:
    try: ai_schema.parse_ai_json(raw); ok = False
    except ValueError: ok = True
    check("schema: rejects " + label, ok)
check("schema: to_text lists headings and points", "Key Areas of Clinical Assessment\n- Fatigue work-up" in txt)
check("schema: tricky names stay in their own box", [t["name"] for t in back["tests"] if t["priority"]=="moderate"] == ["Optional: Vitamin D panel"])
check("schema: dash/bullet/number prefixes parsed", [t["name"] for t in ai_schema.lines_to_tests("- A — why\n2) B\n\n  • C", "top")] == ["A","B","C"])

# ---------------------------------------------------------------- report_pdf
meta = {"client_id": "CL-1", "language": "English", "model": "m", "analysis": d,
        "signature": {"name": "Dr <i>X</i>", "qualification": "MD", "reg_no": "1", "signed_at": "2026-11-03T16:00:00+05:30", "digest": "a"*64, "edited": False}}
h = report_pdf.build_html(ai_schema.to_text(d), meta)
top_html = h.split("Top priority</div>")[1].split("</section>")[0]
mod_html = h.split("Moderate priority</div>")[1].split("</section>")[0]
check("pdf: tricky 'High priority follow-up' item is in the TOP box", "High priority follow-up: repeat HbA1c" in top_html and "CBC" in top_html)
check("pdf: tricky 'Optional: Vitamin D' item is in the MODERATE box", "Optional: Vitamin D panel" in mod_html)
check("pdf: English narrative present", "regular check-up tests" in h)
check("pdf: section headings rendered as <h3> with bullet points", "<h3>Key Areas of Clinical Assessment</h3><ul><li>Fatigue work-up</li></ul>" in h.replace("\n", ""), h[:3000])
check("pdf: section text is HTML-escaped", "check blood pressure &lt;b&gt;and lipids" in h and "<b>and lipids" not in h)
check("pdf: sections come before the test bands", h.index("Key Areas of Clinical Assessment") < h.index("Recommended laboratory tests"))
check("pdf: test names HTML-escaped", "TSH &lt;b&gt;" in h and "<b>TSH <b>" not in h)
check("pdf: signer name escaped", "Dr &lt;i&gt;X&lt;/i&gt;" in h)
old = report_pdf.build_html("Intro\n\nTop priority\n- CBC\nOptional\n- X", {"client_id": "C"})
check("pdf: free-text (no analysis) path still works", "Recommended laboratory tests" in old and "CBC" in old)

# ---------------------------------------------------------------- GUI helpers
def walk(w):
    for c in w.winfo_children():
        yield c
        yield from walk(c)

def schedule_review(root, edit=None, password="secret123", click="Approve, sign and create PDF"):
    def go():
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        if not tops: root.after(100, go); return
        win = tops[-1]
        widgets = list(walk(win))
        texts = [w for w in widgets if isinstance(w, tk.Text)]
        entry = [w for w in widgets if isinstance(w, ttk.Entry)][0]
        if edit: edit(texts)
        entry.insert(0, password)
        [w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == click][0].invoke()
    root.after(300, go)

def pump(root, cond, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        root.update()
        if cond(): return True
        time.sleep(0.02)
    return False

profile = doctor_auth.make_profile("Dr Test", "MD Pathology", "TS-1234", "secret123")
root = tk.Tk()
shown = []
doctor_auth.messagebox.showerror = lambda *a, **k: shown.append(("err", a))
doctor_auth.messagebox.showwarning = lambda *a, **k: shown.append(("warn", a))

# ---------------------------------------------------------------- doctor_auth review window (real widgets)
get_p, set_p = (lambda: profile), (lambda p: None)
def edit_add(texts):
    nar, top, mod, opt = texts
    nar.insert("end", "\n- Doctor added point")
    top.insert("end", "\nLFT — doctor added")
    opt.delete("1.0", "end")
schedule_review(root, edit=edit_add)
r = doctor_auth.review_and_sign(root, d, get_p, set_p, client_id="CL-1")
check("review: signed, structured result returned", bool(r) and isinstance(r["analysis"], dict))
check("review: doctor's added test lands in TOP band", any(t["name"] == "LFT" and t["priority"] == "top" and t["reason"] == "doctor added" for t in r["analysis"]["tests"]))
check("review: emptied Optional box removes that test", not any(t["priority"] == "optional" for t in r["analysis"]["tests"]))
check("review: edited flag True after edits", r["edited"] is True)
check("review: point added in the summary box lands in the last section", r["analysis"]["sections"][-1]["points"][-1] == "Doctor added point" and len(r["analysis"]["sections"]) == 3)
schedule_review(root)
r2 = doctor_auth.review_and_sign(root, d, get_p, set_p, client_id="CL-1")
check("review: untouched answer -> edited False", r2 and r2["edited"] is False)
check("review: untouched answer keeps all tests", r2 and len(r2["analysis"]["tests"]) == len(d["tests"]))
shown.clear()
schedule_review(root, password="wrongpass")   # wrong password, then close via Cancel
def cancel_later():
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    if tops:
        [w for w in walk(tops[-1]) if isinstance(w, ttk.Button) and w.cget("text") == "Cancel"][0].invoke()
    else: root.after(100, cancel_later)
root.after(900, cancel_later)
r3 = doctor_auth.review_and_sign(root, d, get_p, set_p, client_id="CL-1")
check("review: wrong password -> nothing signed", r3 is None and any(k == "err" for k, _ in shown))
schedule_review(root, edit=lambda t: t[0].delete("1.0", "end") or [b.delete("1.0", "end") for b in t[1:]])
shown.clear()
root.after(900, cancel_later)
r4 = doctor_auth.review_and_sign(root, d, get_p, set_p, client_id="CL-1")
check("review: everything empty -> refuses to sign", r4 is None and any(k == "warn" for k, _ in shown))
schedule_review(root)
rt = doctor_auth.review_and_sign(root, "Plain text answer\n\nTop priority\n- CBC", get_p, set_p)
check("review: plain-text mode still works", rt and rt["analysis"] is None and "CBC" in rt["text"])

# ---------------------------------------------------------------- full app flow with a fake Gemini
tmp = Path(tempfile.mkdtemp()); ga.CLIENTS_DIR = tmp
ga.CATALOGUE_FILE = tmp / "no-price-list.xlsx"   # these tests run without the price list (its test names would
                                                 # appear in the prompt; prices are covered by test_catalogue.py)
ga.load_config = lambda: {"doctor": profile}
ga.save_config = lambda **k: None
rec = tmp / "CL-T.json"
rec.write_text(json.dumps({"clientId": "CL-T", "basic": {"name": "Ravi", "mobile": "999", "location": "Hyd", "dateOfBirth": "1980-01-01", "ageYears": 45}}))
class Resp:
    def __init__(s, t): s.text = t
class FakeChat:
    reply = json.dumps(GOOD, ensure_ascii=False)
    def __init__(s): s.calls = []
    def send_message(s, q, config=None): s.calls.append((q, config)); return Resp(FakeChat.reply)
    def send_message_stream(s, q): yield Resp("Hello "); yield Resp("world")
    def get_history(s): return []
fake = FakeChat()
class FakeClient: chats = types.SimpleNamespace(create=lambda model, history=None: fake)
sys.argv = ["x"]
app = ga.GeminiApp(root)
app.client, app.chat = FakeClient(), fake
asks = []
ga.messagebox.showwarning = lambda *a, **k: asks.append(("warn", a))
ga.messagebox.showinfo = lambda *a, **k: asks.append(("info", a))
ga.messagebox.showerror = lambda *a, **k: asks.append(("err", a))
ga.messagebox.askyesno = lambda *a, **k: True

app.send_client_file(str(rec))
check("app: analysis finishes", pump(root, lambda: not app.busy and app.analysis is not None), asks)
shown_text = app.output.get("1.0", "end")
check("app: chat shows tests grouped under headings", "Top priority" in shown_text and "CBC" in shown_text and "Vitamin B12" in shown_text)
check("app: prompt asks for JSON and removed identifiers", "Reply ONLY with JSON" in fake.calls[-1][0] and "Ravi" not in fake.calls[-1][0] and "Hyd" not in fake.calls[-1][0])
check("app: JSON mode + schema requested from Gemini", fake.calls[-1][1].response_mime_type == "application/json" and fake.calls[-1][1].response_schema is not None)

out_pdf = str(tmp / "reports" / "CL-T_advice.pdf")
ga.filedialog.asksaveasfilename = lambda **k: out_pdf
report_pdf.find_browser = lambda: None            # no Chrome here: use the HTML fallback
import webbrowser; webbrowser.open = lambda *a, **k: True
schedule_review(root, edit=lambda t: t[2].insert("end", "\nESR — added by doctor"))
app.export_pdf()
html_path = Path(out_pdf).with_suffix(".html"); audit = Path(out_pdf).with_suffix(".signed.json")
check("app: report file written", html_path.exists(), asks)
hh = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
check("app: report has colour bands, doctor edit and sign-off", "prio-head" in hh and "ESR" in hh and "Reg. No" not in hh and "TS-1234" in hh and "Edited by the physician" in hh)
a = json.loads(audit.read_text(encoding="utf-8")) if audit.exists() else {}
check("app: audit has raw AI JSON, signed analysis and hash", a.get("aiOriginalJson") and a.get("signedAnalysis") and len(a.get("sha256","")) == 64 and a.get("editedByDoctor") is True)

# failure paths
FakeChat.reply = json.dumps(GOOD)[:-20]
app.send_client_file(str(rec))
check("app: truncated JSON -> error, nothing exportable", pump(root, lambda: not app.busy) and app.analysis is None and app.response_ok is False and "Error" in app.output.get("1.0", "end"))
asks.clear(); app.export_pdf()
check("app: export blocked after a failed answer", any(k == "warn" for k, _ in asks))
FakeChat.reply = ""
app.send_client_file(str(rec)); pump(root, lambda: not app.busy)
check("app: blocked/empty reply -> clear error", app.response_ok is False and "no text" in app.output.get("1.0", "end"))
FakeChat.reply = json.dumps(GOOD, ensure_ascii=False)
app.send_client_file(str(rec)); pump(root, lambda: not app.busy and app.analysis is not None)
check("app: retry after failure works", app.analysis is not None and app.response_ok)
app.clear_chat()
check("app: Clear Chat resets the stored answer (export bug)", app.analysis is None and app.response_parts == [] and app.response_client_id is None)
asks.clear(); app.export_pdf()
check("app: nothing to export after Clear Chat", any(k == "info" for k, _ in asks))
# ordinary chat (streaming) still works and exports through the text path
app.entry.insert(0, "hello"); app.send_message()
check("app: normal streaming chat still works", pump(root, lambda: not app.busy) and "Hello world" in app.output.get("1.0", "end") and app.analysis is None)

root.destroy()
print("\n%d failure(s)" % len(FAILS)); sys.exit(1 if FAILS else 0)
