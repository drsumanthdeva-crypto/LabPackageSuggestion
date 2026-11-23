#!/usr/bin/env python3
"""Client Screening Intake (tkinter).

Collects the preventive-screening intake sections A to H plus a typed
medication history, and stores every client as one JSON file in a
"clients" folder next to this script.

Run:  python client_intake.py
Needs Python 3.8+ with tkinter (included with the standard Windows/macOS
installers; on Debian/Ubuntu: sudo apt install python3-tk).
"""
import json
import os
import random
import re
import socket
import subprocess
import sys
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(os.environ.get("INTAKE_DATA_DIR") or Path(__file__).resolve().parent / "clients")

# Local port of an open gemini_assistant.py window (must match that file)
ASSISTANT_PORT = 49731

# ---------------------------------------------------------------- options
CONDS = [("diabetes", "Diabetes mellitus"), ("hypertension", "Hypertension"),
         ("thyroid", "Thyroid disorders"), ("dyslipidemia", "Dyslipidemia"),
         ("heartDisease", "Heart disease"), ("ckd", "Chronic kidney disease"),
         ("liverDisease", "Liver disease"), ("obesity", "Obesity"),
         ("asthmaCopd", "Asthma / COPD"), ("anemia", "Anemia"),
         ("stroke", "Previous stroke"), ("cancer", "Cancer")]
FAM = [("diabetes", "Diabetes", "dx"), ("hypertension", "Hypertension", "dx"),
       ("heartAttack", "Heart attack", "cvd"), ("stroke", "Stroke", "cvd"),
       ("thyroidDisease", "Thyroid disease", "dx"), ("kidneyDisease", "Kidney disease", "dx"),
       ("liverDisease", "Liver disease", "dx"), ("cancer", "Cancer", "ca")]
REL = [("father", "Father"), ("mother", "Mother"), ("brother", "Brother"),
       ("sister", "Sister"), ("grandparent", "Grandparent"), ("other", "Other relative")]
SYMP = [("fatigue", "Fatigue"), ("unexplainedWeightLoss", "Unexplained weight loss"),
        ("excessiveThirst", "Excessive thirst"), ("increasedUrination", "Increased urination"),
        ("hairLoss", "Hair loss"), ("jointPain", "Joint pain"),
        ("abdominalDiscomfort", "Abdominal discomfort"), ("bowelHabitChange", "Changes in bowel habits")]
LABS = [("cbc", "CBC", "e.g. Hb 12.4 g/dL"), ("fastingBloodGlucose", "Fasting blood glucose", "mg/dL"),
        ("hba1c", "HbA1c", "%"), ("lipidProfile", "Lipid profile", "TC, LDL, HDL, TG"),
        ("liverFunction", "Liver function tests", "SGPT, SGOT, bilirubin"),
        ("renalFunction", "Renal function tests", "creatinine, urea"),
        ("tsh", "TSH", "uIU/mL"), ("vitaminB12", "Vitamin B12", "pg/mL"),
        ("vitaminD", "Vitamin D", "ng/mL"), ("urineExamination", "Urine examination", "albumin, sugar")]
YN = [("true", "Yes"), ("false", "No")]
FREQ = ["Once daily", "Twice daily", "Three times daily", "At night", "As needed", "Weekly", "Other"]
MED_STATUS = [("current", "Current"), ("past", "Past")]
MED_COLS = [("name", "Medication", 200), ("dose", "Dose", 90), ("frequency", "Frequency", 120),
            ("duration", "Duration / since", 120), ("indication", "Indication", 150), ("status", "Status", 70)]

RANGES = {"anthropometrics.heightCm": (50, 250), "anthropometrics.weightKg": (2, 400),
          "anthropometrics.waistCm": (30, 250), "anthropometrics.bpSystolic": (60, 260),
          "anthropometrics.bpDiastolic": (30, 160), "genderSpecific.men.lastPsaValue": (0, None)}
REQUIRED = {"basic.name", "basic.dateOfBirth", "basic.sex", "basic.mobile", "basic.location"}
MOBILE_RE = re.compile(r"^\+?[0-9 \-]{10,15}$")

BG = "#F3F6F7"
INK = "#13262B"
MUTED = "#56696F"
ACCENT = "#0A6B7C"
FLAG_BG, FLAG_FG = "#FCEFDD", "#98450A"


# ---------------------------------------------------------------- helpers
def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def age_from(dob_s):
    d = parse_date(dob_s)
    if not d or d > date.today():
        return None
    t = date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def gen_id():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "CL-%s-%s" % (date.today().strftime("%y%m%d"), "".join(random.choice(alphabet) for _ in range(4)))


def set_path(obj, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


def get_path(obj, path):
    for p in path.split("."):
        if not isinstance(obj, dict) or p not in obj:
            return None
        obj = obj[p]
    return obj


def early_cvd(rec):
    for k in ("heartAttack", "stroke"):
        f = get_path(rec, "familyHistory." + k) or {}
        if f.get("present") is not True or f.get("ageAtOnset") is None:
            continue
        rel, age = f.get("relationship"), f["ageAtOnset"]
        if rel in ("father", "brother") and age < 55:
            return True
        if rel in ("mother", "sister") and age < 65:
            return True
    return False


def atomic_write(path, text):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Scrollable(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=BG)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._wheel, add="+")

    def _wheel(self, e):
        w = e.widget
        # only scroll when the pointer is over this form, and leave tables to scroll themselves
        if not isinstance(w, tk.Misc) or not str(w).startswith(str(self.canvas)) or isinstance(w, ttk.Treeview):
            return
        if e.num == 4:
            step = -1
        elif e.num == 5:
            step = 1
        else:
            step = -1 if e.delta > 0 else 1
            if sys.platform == "win32" and abs(e.delta) >= 120:
                step = -int(e.delta / 120)
        self.canvas.yview_scroll(step, "units")

    def scroll_to(self, widget):
        self.update_idletasks()
        total = max(self.inner.winfo_height(), 1)
        y = widget.winfo_rooty() - self.inner.winfo_rooty() - 80
        self.canvas.yview_moveto(max(0, y / total))


# ---------------------------------------------------------------- app
class App:
    def __init__(self, root):
        self.root = root
        root.title("Client Screening Intake")
        root.geometry("1060x800")
        root.minsize(860, 600)
        root.configure(bg=BG)
        APP_DIR.mkdir(parents=True, exist_ok=True)

        self.reg = []                      # ordered (path, kind)
        self.vars, self.groups, self.bools, self.texts = {}, {}, {}, {}
        self.widgets, self.labels, self.combo_maps = {}, {}, {}
        self.cond = {}                     # target path -> controlling radio path
        self.cond_widgets = []             # (widget, predicate)
        self.meds = []
        self.sections = {}
        self.dirty = False
        self._loading = False
        self._refresh_job = None
        self.records = []
        self.client_id = gen_id()
        self.consult_date = datetime.now().astimezone().isoformat(timespec="seconds")

        self._styles()
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)
        self.nb = nb
        self.tab_new = ttk.Frame(nb)
        self.tab_rec = ttk.Frame(nb)
        nb.add(self.tab_new, text="  New intake  ")
        nb.add(self.tab_rec, text="  Saved records  ")
        self._build_form()
        self._build_records()
        self._attach_traces()
        self.new_client(force=True)
        self.load_records()

    # ------------------------------------------------------------ styles
    def _styles(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Segoe UI", 10) if sys.platform == "win32" else ("Helvetica", 11)
        s.configure(".", background=BG, foreground=INK, font=base)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=INK)
        s.configure("Field.TLabel", foreground=MUTED, font=(base[0], base[1] - 1))
        s.configure("Note.TLabel", foreground=MUTED, font=(base[0], base[1] - 1))
        s.configure("Hint.TLabel", foreground=MUTED, font=(base[0], base[1] - 1, "italic"))
        s.configure("Name.TLabel", font=(base[0], base[1], "bold"))
        s.configure("Banner.TLabel", background=FLAG_BG, foreground=FLAG_FG, padding=10,
                    font=(base[0], base[1] - 1, "bold"))
        s.configure("TLabelframe", background=BG, bordercolor="#D3DDE0", relief="solid")
        s.configure("TLabelframe.Label", background=BG, foreground=ACCENT,
                    font=(base[0], base[1] + 1, "bold"))
        s.configure("TCheckbutton", background=BG)
        s.configure("TRadiobutton", background=BG)
        s.configure("TEntry", fieldbackground="white", padding=4)
        s.configure("TCombobox", fieldbackground="white", padding=4)
        s.configure("Pri.TButton", background=ACCENT, foreground="white", padding=(14, 7),
                    font=(base[0], base[1], "bold"))
        s.map("Pri.TButton", background=[("active", "#085868"), ("disabled", "#8FB5BC")])
        s.configure("TButton", padding=(12, 6))
        s.configure("Treeview", rowheight=26, fieldbackground="white")
        s.configure("TNotebook", background=BG)
        self.font_base = base

    # ------------------------------------------------------------ registry
    def _reg(self, path, kind, label):
        self.reg.append((path, kind))
        self.labels[path] = label

    def _cell(self, parent, label, r, c, span=1, req=False):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, columnspan=span, sticky="new", padx=(0, 14), pady=(0, 8))
        ttk.Label(f, text=label + (" *" if req else ""), style="Field.TLabel").pack(anchor="w")
        return f

    def entry(self, parent, label, path, r, c, span=1, kind="str", readonly=False, hint=None):
        f = self._cell(parent, label, r, c, span, path in REQUIRED)
        var = tk.StringVar()
        e = ttk.Entry(f, textvariable=var, state="readonly" if readonly else "normal")
        e.pack(fill="x")
        if hint:
            ttk.Label(f, text=hint, style="Hint.TLabel").pack(anchor="w")
        self.vars[path] = var
        self.widgets[path] = e
        self._reg(path, kind, label)
        return f

    def combo(self, parent, label, path, options, r, c, span=1):
        f = self._cell(parent, label, r, c, span)
        var = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=var, values=[l for _, l in options], state="readonly")
        cb.pack(fill="x")
        self.vars[path] = var
        self.widgets[path] = cb
        self.combo_maps[path] = dict((l, v) for v, l in options)
        self._reg(path, "combo", label)
        return f

    def radio(self, parent, label, path, options, r, c, span=1):
        f = self._cell(parent, label, r, c, span, path in REQUIRED)
        var = tk.StringVar(value="")
        row = ttk.Frame(f)
        row.pack(anchor="w")
        first = None
        for code, text in options:
            rb = ttk.Radiobutton(row, text=text, value=code, variable=var)
            rb.pack(side="left", padx=(0, 12))
            first = first or rb
        self.vars[path] = var
        self.widgets[path] = first
        self._reg(path, "radio", label)
        return f

    def text(self, parent, label, path, r, c, span=1, height=3):
        f = self._cell(parent, label, r, c, span)
        t = tk.Text(f, height=height, wrap="word", relief="solid", bd=1, font=self.font_base,
                    highlightthickness=1, highlightcolor=ACCENT, highlightbackground="#C5D0D3")
        t.pack(fill="x")
        t.bind("<KeyRelease>", lambda e: self.on_change())
        self.texts[path] = t
        self.widgets[path] = t
        self._reg(path, "text", label)
        return f

    def checks(self, parent, path, options, r, c, span=3, cols=3):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, columnspan=span, sticky="w", pady=(0, 8))
        self.groups[path] = {}
        for i, (code, text) in enumerate(options):
            v = tk.BooleanVar()
            ttk.Checkbutton(f, text=text, variable=v).grid(row=i // cols, column=i % cols,
                                                          sticky="w", padx=(0, 22), pady=2)
            self.groups[path][code] = v
        self._reg(path, "group", path)
        return f

    def section(self, parent, letter, title, note=None):
        lf = ttk.LabelFrame(parent, text="  %s   %s  " % (letter, title), padding=12)
        lf.pack(fill="x", padx=14, pady=8)
        if note:
            ttk.Label(lf, text=note, style="Note.TLabel", wraplength=800, justify="left").pack(anchor="w", pady=(0, 8))
        body = ttk.Frame(lf)
        body.pack(fill="x")
        for i in range(3):
            body.columnconfigure(i, weight=1, uniform="c")
        self.sections[letter] = lf
        return body

    # ------------------------------------------------------------ form
    def _build_form(self):
        sc = Scrollable(self.tab_new)
        sc.pack(fill="both", expand=True)
        self.scroll = sc
        p = sc.inner

        # A
        b = self.section(p, "A", "Basic client information",
                         "Client ID and consultation date are generated automatically.")
        self.id_var, self.cd_var = tk.StringVar(), tk.StringVar()
        for col, (lab, var) in enumerate((("Client ID", self.id_var), ("Date of consultation", self.cd_var))):
            f = self._cell(b, lab, 0, col)
            ttk.Entry(f, textvariable=var, state="readonly").pack(fill="x")
        self.entry(b, "Full name", "basic.name", 1, 0, span=2)
        self.entry(b, "Date of birth (YYYY-MM-DD)", "basic.dateOfBirth", 1, 2, kind="date")
        self.entry(b, "Age (years)", "basic.ageYears", 2, 0, kind="num", readonly=True, hint="Calculated from date of birth")
        self.radio(b, "Sex", "basic.sex", [("male", "Male"), ("female", "Female"), ("other", "Other")], 2, 1)
        self.entry(b, "Mobile number", "basic.mobile", 2, 2)
        self.entry(b, "Location (city / area)", "basic.location", 3, 0)
        self.entry(b, "Occupation (optional)", "basic.occupation", 3, 1)

        # B
        b = self.section(p, "B", "Medical history",
                         "Record existing diagnoses rather than asking only whether the client is healthy.")
        ttk.Label(b, text="Existing medical conditions", style="Field.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self.checks(b, "medicalHistory.conditions", CONDS, 1, 0)
        none_var = tk.BooleanVar()
        ttk.Checkbutton(b, text="None of the above", variable=none_var).grid(row=2, column=0, sticky="w", pady=(0, 10))
        self.bools["medicalHistory.noneOfTheAbove"] = none_var
        self._reg("medicalHistory.noneOfTheAbove", "bool", "None of the above")
        self.entry(b, "Duration of existing illness", "medicalHistory.illnessDuration", 3, 0, hint="e.g. 6 years")
        self.entry(b, "Known allergies (if relevant)", "medicalHistory.knownAllergies", 3, 1)
        self.entry(b, "Date of last health checkup (YYYY-MM-DD)", "medicalHistory.lastCheckupDate", 3, 2, kind="date")
        self.text(b, "Previous surgeries", "medicalHistory.previousSurgeries", 4, 0, span=3, height=2)
        self.text(b, "Previous abnormal laboratory results", "medicalHistory.previousAbnormalLabs", 5, 0, span=3, height=2)
        self._build_meds(b, 6)

        # C
        b = self.section(p, "C", "Family history",
                         "A first-degree relative with premature cardiovascular disease or certain cancers can change "
                         "screening advice. For each condition, record the closest or earliest affected relative.")
        for i, (key, name, typ) in enumerate(FAM):
            base = "familyHistory." + key
            row = ttk.Frame(b)
            row.grid(row=i * 2, column=0, columnspan=3, sticky="ew", pady=(6, 0))
            ttk.Label(row, text=name, style="Name.TLabel", width=18).pack(side="left")
            var = tk.StringVar(value="")
            for code, text in YN:
                ttk.Radiobutton(row, text=text, value=code, variable=var).pack(side="left", padx=(0, 12))
            self.vars[base + ".present"] = var
            self.widgets[base + ".present"] = row
            self._reg(base + ".present", "radio", name)
            det = ttk.Frame(b)
            det.grid(row=i * 2 + 1, column=0, columnspan=3, sticky="ew", pady=(4, 2))
            for cc in range(3):
                det.columnconfigure(cc, weight=1, uniform="d")
            self.combo(det, "Relationship", base + ".relationship", REL, 0, 0)
            col = 1
            if typ == "ca":
                self.entry(det, "Type of cancer", base + ".cancerType", 0, 1)
                col = 2
            age_key = ".ageAtOnset" if typ == "cvd" else ".ageAtDiagnosis"
            self.entry(det, "Age at onset" if typ == "cvd" else "Age at diagnosis", base + age_key, 0, col, kind="num")
            for sub in (".relationship", ".cancerType", age_key):
                if base + sub in self.vars:
                    self.cond[base + sub] = base + ".present"
            self.cond_widgets.append((det, (lambda v=var: v.get() == "true")))
        self.fam_banner = ttk.Label(b, style="Banner.TLabel", wraplength=780, justify="left",
                                    text="Early cardiovascular disease in a first-degree relative: heart attack or stroke "
                                         "before 55 in a father or brother, or before 65 in a mother or sister. "
                                         "Review screening recommendations.")
        self.fam_banner.grid(row=20, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        # D
        b = self.section(p, "D", "Lifestyle information")
        self.radio(b, "Smoking", "lifestyle.smoking", [("never", "Never"), ("former", "Former"), ("current", "Current")], 0, 0)
        self.radio(b, "Alcohol", "lifestyle.alcohol", [("never", "Never"), ("occasionally", "Occasionally"), ("regularly", "Regularly")], 0, 1)
        self.radio(b, "Physical activity", "lifestyle.physicalActivity", [("sedentary", "Sedentary"), ("moderate", "Moderate"), ("active", "Active")], 0, 2)
        self.radio(b, "Diet", "lifestyle.diet", [("vegetarian", "Vegetarian"), ("mixed", "Mixed"), ("other", "Other")], 1, 0)
        self.radio(b, "Sleep per night", "lifestyle.sleep", [("lt6", "< 6 h"), ("6to8", "6-8 h"), ("gt8", "> 8 h")], 1, 1)
        self.radio(b, "Exercise", "lifestyle.exercise", [("none", "None"), ("1to3", "1-3 days/wk"), ("4plus", "4+ days/wk")], 1, 2)
        self.radio(b, "Tobacco chewing", "lifestyle.tobaccoChewing", YN, 2, 0)

        # E
        b = self.section(p, "E", "Anthropometric measurements", "Used for metabolic risk assessment.")
        self.entry(b, "Height (cm)", "anthropometrics.heightCm", 0, 0, kind="num")
        self.entry(b, "Weight (kg)", "anthropometrics.weightKg", 0, 1, kind="num")
        self.entry(b, "BMI (kg/m2)", "anthropometrics.bmi", 0, 2, kind="num", readonly=True, hint="Weight / height squared, calculated")
        self.entry(b, "Waist circumference (cm)", "anthropometrics.waistCm", 1, 0, kind="num")
        self.entry(b, "Blood pressure, systolic (mmHg)", "anthropometrics.bpSystolic", 1, 1, kind="num")
        self.entry(b, "Blood pressure, diastolic (mmHg)", "anthropometrics.bpDiastolic", 1, 2, kind="num")

        # F
        b = self.section(p, "F", "Symptoms and clinical complaints",
                         "Symptoms separate screening from diagnostic evaluation. They are flagged for clinical review "
                         "and do not add screening tests.")
        self.checks(b, "symptoms.reported", SYMP, 0, 0)
        self.entry(b, "Other symptoms", "symptoms.other", 1, 0, span=2)
        self.entry(b, "Duration", "symptoms.duration", 1, 2, hint="e.g. 3 weeks")
        self.symp_banner = ttk.Label(b, style="Banner.TLabel", wraplength=780, justify="left",
                                     text="Flag for clinical evaluation: this client reported symptoms. Refer for clinical "
                                          "assessment before choosing screening tests.")
        self.symp_banner.grid(row=2, column=0, columnspan=3, sticky="ew")

        # G
        b = self.section(p, "G", "Previous laboratory investigations",
                         "Enter the result and the date, not only whether the test was done. Leave blank if not available.")
        for i, h in enumerate(("Test", "Result", "Date (YYYY-MM-DD)")):
            ttk.Label(b, text=h, style="Field.TLabel").grid(row=0, column=i, sticky="w")
        for i, (key, name, ph) in enumerate(LABS, start=1):
            nm = ttk.Frame(b)
            nm.grid(row=i, column=0, sticky="w", pady=4)
            ttk.Label(nm, text=name, style="Name.TLabel").pack(anchor="w")
            ttk.Label(nm, text=ph, style="Hint.TLabel").pack(anchor="w")
            for col, (sub, kind) in enumerate((("result", "str"), ("date", "date")), start=1):
                path = "labs.%s.%s" % (key, sub)
                var = tk.StringVar()
                e = ttk.Entry(b, textvariable=var)
                e.grid(row=i, column=col, sticky="ew", padx=(0, 14), pady=4)
                self.vars[path] = var
                self.widgets[path] = e
                self._reg(path, kind, "%s %s" % (name, sub))

        # H
        b = self.section(p, "H", "Gender-specific information",
                         "Age-appropriate breast, cervical and colorectal screening is considered separately from "
                         "routine laboratory packages.")
        self.sex_note = ttk.Label(b, text="Select the client's sex in section A to show the relevant questions.",
                                  style="Hint.TLabel")
        self.sex_note.grid(row=0, column=0, columnspan=3, sticky="w")

        wf = ttk.Frame(b)
        wf.grid(row=1, column=0, columnspan=3, sticky="ew")
        for i in range(3):
            wf.columnconfigure(i, weight=1, uniform="w")
        ttk.Label(wf, text="Women", style="Name.TLabel").grid(row=0, column=0, sticky="w", pady=(6, 6))
        w = "genderSpecific.women."
        self.combo(wf, "Menstrual status", w + "menstrualStatus", [("regular", "Regular"), ("irregular", "Irregular"), ("absent", "Absent")], 1, 0)
        self.combo(wf, "Menopause status", w + "menopauseStatus", [("premenopausal", "Premenopausal"), ("perimenopausal", "Perimenopausal"), ("postmenopausal", "Postmenopausal")], 1, 1)
        self.radio(wf, "Possibility of pregnancy", w + "pregnancyPossible", YN, 1, 2)
        self.radio(wf, "PCOS history", w + "pcosHistory", YN, 2, 0)
        self.radio(wf, "Pregnancy-related diabetes", w + "gestationalDiabetes", YN, 2, 1)
        self.radio(wf, "Breast cancer in family", w + "breastCancerFamilyHistory", YN, 2, 2)
        self.radio(wf, "Cervical cancer screening done before", w + "cervicalScreeningDone", YN, 3, 0)
        c1 = self.entry(wf, "Last Pap smear / HPV test (YYYY-MM-DD)", w + "lastCervicalScreeningDate", 3, 1, kind="date")
        c2 = self.combo(wf, "Test type", w + "lastCervicalScreeningType", [("pap", "Pap smear"), ("hpv", "HPV test"), ("both", "Both")], 3, 2)
        for tp in (w + "lastCervicalScreeningDate", w + "lastCervicalScreeningType"):
            self.cond[tp] = w + "cervicalScreeningDone"
        for cw in (c1, c2):
            self.cond_widgets.append((cw, lambda: self.vars[w + "cervicalScreeningDone"].get() == "true"))
        self.women_frame = wf

        mf = ttk.Frame(b)
        mf.grid(row=2, column=0, columnspan=3, sticky="ew")
        for i in range(3):
            mf.columnconfigure(i, weight=1, uniform="m")
        ttk.Label(mf, text="Men", style="Name.TLabel").grid(row=0, column=0, sticky="w", pady=(6, 6))
        m = "genderSpecific.men."
        self.radio(mf, "Lower urinary tract symptoms", m + "lutsPresent", YN, 1, 0)
        self.radio(mf, "Prostate cancer in family", m + "prostateCancerFamilyHistory", YN, 1, 1)
        self.radio(mf, "Previous PSA test", m + "psaTestedBefore", YN, 1, 2)
        p1 = self.entry(mf, "Date of last PSA (YYYY-MM-DD)", m + "lastPsaDate", 2, 0, kind="date")
        p2 = self.entry(mf, "Last PSA value (ng/mL)", m + "lastPsaValue", 2, 1, kind="num")
        for tp in (m + "lastPsaDate", m + "lastPsaValue"):
            self.cond[tp] = m + "psaTestedBefore"
        for cw in (p1, p2):
            self.cond_widgets.append((cw, lambda: self.vars[m + "psaTestedBefore"].get() == "true"))
        self.radio(mf, "Erectile or reproductive symptoms", m + "reproductiveSymptoms",
                   [("none", "None"), ("present", "Present"), ("declined", "Prefers not to say")], 3, 0, span=3)
        ttk.Label(mf, text="Ask only if relevant and voluntarily disclosed.", style="Hint.TLabel").grid(row=4, column=0, columnspan=3, sticky="w")
        self.men_frame = mf
        self.cond_widgets.append((wf, lambda: self.vars["basic.sex"].get() in ("female", "other")))
        self.cond_widgets.append((mf, lambda: self.vars["basic.sex"].get() in ("male", "other")))
        self.cond_widgets.append((self.sex_note, lambda: not self.vars["basic.sex"].get()))
        self.cond_widgets.append((self.fam_banner, lambda: self.flags_now.get("familyPrematureCardiovascular")))
        self.cond_widgets.append((self.symp_banner, lambda: self.flags_now.get("symptomsForClinicalEvaluation")))
        self.flags_now = {}

        # save bar
        bar = ttk.Frame(self.tab_new, padding=(14, 8))
        bar.pack(fill="x", side="bottom")
        ttk.Separator(self.tab_new).pack(fill="x", side="bottom")
        left = ttk.Frame(bar)
        left.pack(side="left")
        self.bar_id = ttk.Label(left, text="", font=("Courier", 10))
        self.bar_id.pack(anchor="w")
        self.status_lbl = ttk.Label(left, text="Not saved yet", style="Note.TLabel")
        self.status_lbl.pack(anchor="w")
        self.btn_save = ttk.Button(bar, text="Save record", style="Pri.TButton", command=self.save)
        self.btn_save.pack(side="right")
        ttk.Button(bar, text="Save & ask AI", command=self.save_and_ask_ai).pack(side="right", padx=8)
        ttk.Button(bar, text="Preview JSON", command=self.preview).pack(side="right", padx=8)
        ttk.Button(bar, text="New client", command=self.new_client).pack(side="right")

    # ------------------------------------------------------------ medications
    def _build_meds(self, parent, row):
        lf = ttk.LabelFrame(parent, text="  Medication history  ", padding=10)
        lf.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(lf, text="Type each medication the client takes or has taken, then press Add. "
                           "Select a row to edit or remove it.", style="Note.TLabel").pack(anchor="w", pady=(0, 6))
        form = ttk.Frame(lf)
        form.pack(fill="x")
        for i in range(6):
            form.columnconfigure(i, weight=1 if i in (0, 4) else 0)
        self.mv = {k: tk.StringVar() for k, _, _ in MED_COLS}
        self.mv["status"].set("Current")
        specs = [("name", "Medication name", 0, 22), ("dose", "Dose", 1, 10), ("frequency", "Frequency", 2, 16),
                 ("duration", "Duration / since", 3, 14), ("indication", "Indication", 4, 18)]
        for key, lab, col, width in specs:
            f = ttk.Frame(form)
            f.grid(row=0, column=col, sticky="ew", padx=(0, 8))
            ttk.Label(f, text=lab, style="Field.TLabel").pack(anchor="w")
            if key == "frequency":
                w = ttk.Combobox(f, textvariable=self.mv[key], values=FREQ, width=width)
            else:
                w = ttk.Entry(f, textvariable=self.mv[key], width=width)
            w.pack(fill="x")
            if key == "name":
                self.med_name_entry = w
            w.bind("<Return>", lambda e: self.med_add())
        f = ttk.Frame(form)
        f.grid(row=0, column=5, sticky="ew")
        ttk.Label(f, text="Status", style="Field.TLabel").pack(anchor="w")
        ttk.Combobox(f, textvariable=self.mv["status"], values=[l for _, l in MED_STATUS],
                     state="readonly", width=9).pack(fill="x")
        btns = ttk.Frame(lf)
        btns.pack(fill="x", pady=(8, 6))
        ttk.Button(btns, text="Add", command=self.med_add).pack(side="left")
        ttk.Button(btns, text="Update selected", command=self.med_update).pack(side="left", padx=6)
        ttk.Button(btns, text="Remove selected", command=self.med_remove).pack(side="left")
        ttk.Button(btns, text="Clear fields", command=self.med_clear).pack(side="left", padx=6)
        tf = ttk.Frame(lf)
        tf.pack(fill="x")
        self.med_tree = ttk.Treeview(tf, columns=[k for k, _, _ in MED_COLS], show="headings", height=5,
                                     selectmode="browse")
        for k, h, w in MED_COLS:
            self.med_tree.heading(k, text=h)
            self.med_tree.column(k, width=w, anchor="w")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.med_tree.yview)
        self.med_tree.configure(yscrollcommand=sb.set)
        self.med_tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        self.med_tree.bind("<<TreeviewSelect>>", self.med_select)
        self.med_empty = ttk.Label(lf, text="No medications recorded.", style="Hint.TLabel")
        self.med_empty.pack(anchor="w", pady=(4, 0))
        self.widgets["medicalHistory.medicationHistory"] = self.med_name_entry

    def _med_from_fields(self):
        d = {k: (self.mv[k].get().strip() or None) for k, _, _ in MED_COLS}
        d["status"] = dict((l, v) for v, l in MED_STATUS).get(self.mv["status"].get(), "current")
        return d

    def med_refresh(self):
        self.med_tree.delete(*self.med_tree.get_children())
        for i, m in enumerate(self.meds):
            vals = [(m.get(k) or "") for k, _, _ in MED_COLS]
            vals[-1] = dict(MED_STATUS).get(m.get("status"), "Current")
            self.med_tree.insert("", "end", iid=str(i), values=vals)
        if self.meds:
            self.med_empty.pack_forget()
        else:
            self.med_empty.pack(anchor="w", pady=(4, 0))

    def med_clear(self):
        for k in self.mv:
            self.mv[k].set("")
        self.mv["status"].set("Current")
        self.med_tree.selection_remove(self.med_tree.selection())

    def med_add(self):
        d = self._med_from_fields()
        if not d["name"]:
            messagebox.showinfo("Medication", "Type the medication name first.")
            self.med_name_entry.focus_set()
            return
        self.meds.append(d)
        self.med_refresh()
        self.med_clear()
        self.med_name_entry.focus_set()
        self.on_change()

    def med_select(self, _e=None):
        sel = self.med_tree.selection()
        if not sel:
            return
        m = self.meds[int(sel[0])]
        for k, _, _ in MED_COLS:
            self.mv[k].set(m.get(k) or "")
        self.mv["status"].set(dict(MED_STATUS).get(m.get("status"), "Current"))

    def med_update(self):
        sel = self.med_tree.selection()
        if not sel:
            messagebox.showinfo("Medication", "Select a medication row to update.")
            return
        d = self._med_from_fields()
        if not d["name"]:
            messagebox.showinfo("Medication", "The medication name cannot be empty.")
            return
        self.meds[int(sel[0])] = d
        self.med_refresh()
        self.med_clear()
        self.on_change()

    def med_remove(self):
        sel = self.med_tree.selection()
        if not sel:
            messagebox.showinfo("Medication", "Select a medication row to remove.")
            return
        del self.meds[int(sel[0])]
        self.med_refresh()
        self.med_clear()
        self.on_change()

    # ------------------------------------------------------------ live behaviour
    def _attach_traces(self):
        for v in list(self.vars.values()) + [v for g in self.groups.values() for v in g.values()] + list(self.bools.values()):
            v.trace_add("write", lambda *a: self.on_change())
        for code, v in self.groups["medicalHistory.conditions"].items():
            v.trace_add("write", lambda *a, v=v: self._cond_changed(v))
        self.bools["medicalHistory.noneOfTheAbove"].trace_add("write", self._none_changed)

    def _cond_changed(self, v):
        if v.get() and not self._loading:
            self.bools["medicalHistory.noneOfTheAbove"].set(False)

    def _none_changed(self, *a):
        if self.bools["medicalHistory.noneOfTheAbove"].get() and not self._loading:
            for v in self.groups["medicalHistory.conditions"].values():
                v.set(False)

    def on_change(self):
        if self._loading:
            return
        if not self.dirty:
            self.dirty = True
            self.set_status("Unsaved changes")
        if self._refresh_job is None:
            self._refresh_job = self.root.after(60, self.refresh)

    def refresh(self):
        self._refresh_job = None
        age = age_from(self.vars["basic.dateOfBirth"].get().strip())
        try:
            h = float(self.vars["anthropometrics.heightCm"].get())
            w = float(self.vars["anthropometrics.weightKg"].get())
            bmi = "%.1f" % (w / ((h / 100.0) ** 2)) if h > 0 and w > 0 else ""
        except ValueError:
            bmi = ""
        was = self._loading
        self._loading = True          # computed fields must not count as user edits
        self.vars["basic.ageYears"].set("" if age is None else str(age))
        self.vars["anthropometrics.bmi"].set(bmi)
        self._loading = was
        errs = []
        rec = self.collect(errs)
        self.flags_now = rec["flags"]
        for widget, pred in self.cond_widgets:
            if pred():
                widget.grid()
            else:
                widget.grid_remove()

    def set_status(self, text, kind=None):
        self.status_lbl.configure(text=text, foreground={"err": "#B42318", "ok": "#1B7448"}.get(kind, MUTED))

    # ------------------------------------------------------------ data
    def visible(self, path):
        sex = self.vars["basic.sex"].get()
        if path.startswith("genderSpecific.women") and sex not in ("female", "other"):
            return False
        if path.startswith("genderSpecific.men") and sex not in ("male", "other"):
            return False
        ctrl = self.cond.get(path)
        if ctrl and self.vars[ctrl].get() != "true":
            return False
        return True

    def collect(self, errors):
        today = date.today()
        rec = {"schemaVersion": 1, "clientId": self.client_id,
               "consultationDate": self.consult_date, "updatedAt": None}
        for path, kind in self.reg:
            vis = self.visible(path)
            label = self.labels[path]
            if kind == "group":
                set_path(rec, path, [c for c, v in self.groups[path].items() if v.get()])
            elif kind == "bool":
                set_path(rec, path, bool(self.bools[path].get()))
            elif kind == "text":
                s = self.texts[path].get("1.0", "end-1c").strip()
                set_path(rec, path, s or None)
            elif kind == "radio":
                s = self.vars[path].get()
                val = None if (not vis or not s) else (True if s == "true" else False if s == "false" else s)
                if path in REQUIRED and not s:
                    errors.append((path, "%s is required" % label))
                set_path(rec, path, val)
            elif kind == "combo":
                s = self.vars[path].get()
                set_path(rec, path, self.combo_maps[path].get(s) if vis and s else None)
            else:
                s = self.vars[path].get().strip()
                if not vis or s == "":
                    if path in REQUIRED and vis:
                        errors.append((path, "%s is required" % label))
                    set_path(rec, path, None)
                    continue
                val = s
                if kind == "num":
                    try:
                        val = float(s)
                        val = int(val) if val.is_integer() else val
                        is_rel_age = path.endswith(".ageAtOnset") or path.endswith(".ageAtDiagnosis")
                        lo, hi = RANGES.get(path, (0, 120) if is_rel_age else (None, None))
                        if (lo is not None and val < lo) or (hi is not None and val > hi):
                            errors.append((path, "%s is out of range" % label))
                    except ValueError:
                        errors.append((path, "%s must be a number" % label))
                        val = None
                elif kind == "date":
                    d = parse_date(s)
                    if not d:
                        errors.append((path, "%s must be a date as YYYY-MM-DD" % label))
                        val = None
                    elif d > today:
                        errors.append((path, "%s cannot be in the future" % label))
                elif path == "basic.mobile" and not MOBILE_RE.match(s):
                    errors.append((path, "Mobile number needs 10 to 15 digits"))
                set_path(rec, path, val)
        set_path(rec, "medicalHistory.medicationHistory", [dict(m) for m in self.meds])
        sy = get_path(rec, "symptoms.reported") or []
        rec["flags"] = {"symptomsForClinicalEvaluation": bool(sy) or bool(get_path(rec, "symptoms.other")),
                        "familyPrematureCardiovascular": early_cvd(rec)}
        return rec

    def fill(self, rec):
        self._loading = True
        try:
            for path, kind in self.reg:
                v = get_path(rec, path)
                if kind == "group":
                    for c, var in self.groups[path].items():
                        var.set(isinstance(v, list) and c in v)
                elif kind == "bool":
                    self.bools[path].set(bool(v))
                elif kind == "text":
                    t = self.texts[path]
                    t.delete("1.0", "end")
                    if v:
                        t.insert("1.0", str(v))
                elif kind == "radio":
                    self.vars[path].set("" if v is None else ("true" if v is True else "false" if v is False else str(v)))
                elif kind == "combo":
                    inv = dict((c, l) for l, c in self.combo_maps[path].items())
                    self.vars[path].set(inv.get(v, ""))
                else:
                    self.vars[path].set("" if v is None else str(v))
            meds = get_path(rec, "medicalHistory.medicationHistory") or []
            self.meds = [dict(m) for m in meds if isinstance(m, dict)]
            self.med_refresh()
            self.med_clear()
        finally:
            self._loading = False

    # ------------------------------------------------------------ actions
    def new_client(self, force=False):
        if not force and self.dirty and not messagebox.askyesno("New client", "Discard the unsaved changes?"):
            return
        self._loading = True
        for v in self.vars.values():
            v.set("")
        for g in self.groups.values():
            for v in g.values():
                v.set(False)
        for v in self.bools.values():
            v.set(False)
        for t in self.texts.values():
            t.delete("1.0", "end")
        self.meds = []
        self.med_refresh()
        self.med_clear()
        self._loading = False
        self.client_id = gen_id()
        self.consult_date = datetime.now().astimezone().isoformat(timespec="seconds")
        self.id_var.set(self.client_id)
        self.cd_var.set(datetime.now().strftime("%d %b %Y"))
        self.bar_id.configure(text=self.client_id)
        self.dirty = False
        self.set_status("Not saved yet")
        self.refresh()
        self.scroll.canvas.yview_moveto(0)

    def save(self):
        self.refresh()
        errs = []
        rec = self.collect(errs)
        if errs:
            seen, lines = set(), []
            for _, m in errs:
                if m not in seen:
                    seen.add(m)
                    lines.append("- " + m)
            self.set_status("Check the highlighted entries", "err")
            messagebox.showerror("Cannot save yet", "Please fix:\n\n" + "\n".join(lines))
            w = self.widgets.get(errs[0][0])
            if w is not None:
                self.scroll.scroll_to(w)
                try:
                    w.focus_set()
                except tk.TclError:
                    pass
            return None
        rec["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            out_path = APP_DIR / (self.client_id + ".json")
            atomic_write(out_path, json.dumps(rec, indent=2, ensure_ascii=False))
        except OSError as e:
            self.set_status("Could not save", "err")
            messagebox.showerror("Could not save", str(e))
            return None
        self.dirty = False
        self.set_status("Saved %s" % datetime.now().strftime("%H:%M:%S"), "ok")
        self.load_records()
        return out_path

    def save_and_ask_ai(self):
        """Save the record, then open the Gemini assistant with it."""
        path = self.save()
        if not path:
            return
        script = Path(__file__).resolve().parent / "gemini_assistant.py"
        if not script.is_file():
            messagebox.showerror("AI assistant", "gemini_assistant.py was not found next to this script:\n%s" % script)
            return
        # Reuse an assistant window that is already open
        try:
            with socket.create_connection(("127.0.0.1", ASSISTANT_PORT), timeout=1.5) as s:
                s.sendall((str(path) + "\n").encode("utf-8"))
                s.settimeout(3)
                if s.recv(16).strip() == b"OK":
                    self.set_status("Saved and sent to the open AI assistant", "ok")
                    return
        except OSError:
            pass
        try:
            subprocess.Popen([sys.executable, str(script), str(path)])
            self.set_status("Saved and sent to AI assistant", "ok")
        except OSError as e:
            messagebox.showerror("AI assistant", "Could not start the assistant:\n%s" % e)

    def preview(self):
        errs = []
        rec = self.collect(errs)
        top = tk.Toplevel(self.root)
        top.title("Record preview (JSON)")
        top.geometry("620x640")
        txt = tk.Text(top, wrap="none", font=("Courier", 10))
        sb = ttk.Scrollbar(top, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        bar = ttk.Frame(top, padding=6)
        bar.pack(fill="x", side="bottom")
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        body = json.dumps(rec, indent=2, ensure_ascii=False)
        txt.insert("1.0", body)
        txt.configure(state="disabled")

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(body)
        ttk.Button(bar, text="Copy JSON", command=copy).pack(side="left")
        if errs:
            ttk.Label(bar, text="  Some entries are invalid and appear as null.", style="Note.TLabel").pack(side="left")

    # ------------------------------------------------------------ records tab
    def _build_records(self):
        t = self.tab_rec
        top = ttk.Frame(t, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *a: self.render_records())
        ttk.Label(top, text="Search").pack(side="left")
        ttk.Entry(top, textvariable=self.q, width=34).pack(side="left", padx=8)
        ttk.Button(top, text="Open folder", command=self.open_folder).pack(side="right")
        ttk.Button(top, text="Export all (JSON)", command=self.export_all).pack(side="right", padx=8)
        self.count_lbl = ttk.Label(top, text="", style="Note.TLabel")
        self.count_lbl.pack(side="right", padx=8)
        ttk.Label(t, text="Saved in: %s" % APP_DIR, style="Hint.TLabel").pack(anchor="w", padx=14)
        mid = ttk.Frame(t, padding=(14, 6))
        mid.pack(fill="both", expand=True)
        cols = [("id", "Client ID", 150), ("name", "Name", 200), ("agesex", "Age / sex", 90),
                ("mobile", "Mobile", 120), ("date", "Consultation", 110), ("flags", "Flags", 170)]
        self.rec_tree = ttk.Treeview(mid, columns=[c for c, _, _ in cols], show="headings", height=9, selectmode="browse")
        for c, h, w in cols:
            self.rec_tree.heading(c, text=h)
            self.rec_tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.rec_tree.yview)
        self.rec_tree.configure(yscrollcommand=sb.set)
        self.rec_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.rec_tree.bind("<<TreeviewSelect>>", self.rec_select)
        self.rec_tree.bind("<Double-1>", lambda e: self.rec_open())
        btn = ttk.Frame(t, padding=(14, 0))
        btn.pack(fill="x")
        ttk.Button(btn, text="Open for editing", command=self.rec_open).pack(side="left")
        ttk.Button(btn, text="Copy JSON", command=self.rec_copy).pack(side="left", padx=8)
        ttk.Button(btn, text="Delete", command=self.rec_delete).pack(side="left")
        pv = ttk.Frame(t, padding=(14, 8, 14, 14))
        pv.pack(fill="both", expand=True)
        self.rec_text = tk.Text(pv, height=12, wrap="none", font=("Courier", 10), state="disabled")
        sb2 = ttk.Scrollbar(pv, command=self.rec_text.yview)
        self.rec_text.configure(yscrollcommand=sb2.set)
        self.rec_text.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")

    def load_records(self):
        recs = []
        for f in sorted(APP_DIR.glob("*.json")):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(r, dict) and r.get("clientId"):
                    recs.append(r)
            except (OSError, ValueError):
                continue
        recs.sort(key=lambda r: str(r.get("consultationDate") or ""), reverse=True)
        self.records = recs
        self.render_records()

    def render_records(self):
        q = self.q.get().strip().lower()
        self.rec_tree.delete(*self.rec_tree.get_children())
        shown = 0
        for r in self.records:
            b = r.get("basic") or {}
            hay = " ".join(str(x or "") for x in (r.get("clientId"), b.get("name"), b.get("mobile"))).lower()
            if q and q not in hay:
                continue
            fl = r.get("flags") or {}
            flags = ", ".join(x for x, on in (("Symptoms", fl.get("symptomsForClinicalEvaluation")),
                                              ("Family CVD", fl.get("familyPrematureCardiovascular"))) if on)
            agesex = " / ".join(str(x) for x in (b.get("ageYears"), (b.get("sex") or "")[:1].upper()) if x not in (None, ""))
            d = parse_date(str(r.get("consultationDate") or "")[:10])
            self.rec_tree.insert("", "end", iid=r["clientId"],
                                 values=(r["clientId"], b.get("name") or "Unnamed", agesex, b.get("mobile") or "",
                                         d.strftime("%d %b %Y") if d else "", flags))
            shown += 1
        self.count_lbl.configure(text="%d of %d records" % (shown, len(self.records)))
        self._show_text("")

    def _show_text(self, s):
        self.rec_text.configure(state="normal")
        self.rec_text.delete("1.0", "end")
        self.rec_text.insert("1.0", s)
        self.rec_text.configure(state="disabled")

    def _selected(self):
        sel = self.rec_tree.selection()
        if not sel:
            return None
        return next((r for r in self.records if r["clientId"] == sel[0]), None)

    def rec_select(self, _e=None):
        r = self._selected()
        self._show_text(json.dumps(r, indent=2, ensure_ascii=False) if r else "")

    def rec_open(self):
        r = self._selected()
        if not r:
            messagebox.showinfo("Saved records", "Select a record first.")
            return
        if self.dirty and not messagebox.askyesno("Open record", "Discard the unsaved changes in the form?"):
            return
        self.fill(r)
        self.client_id = r["clientId"]
        self.consult_date = r.get("consultationDate") or self.consult_date
        d = parse_date(str(self.consult_date)[:10])
        self.id_var.set(self.client_id)
        self.cd_var.set(d.strftime("%d %b %Y") if d else "")
        self.bar_id.configure(text=self.client_id)
        self.dirty = False
        self.refresh()
        self.set_status("Editing saved record")
        self.nb.select(self.tab_new)
        self.scroll.canvas.yview_moveto(0)

    def rec_copy(self):
        r = self._selected()
        if r:
            self.root.clipboard_clear()
            self.root.clipboard_append(json.dumps(r, indent=2, ensure_ascii=False))

    def rec_delete(self):
        r = self._selected()
        if not r:
            return
        name = (r.get("basic") or {}).get("name") or r["clientId"]
        if messagebox.askyesno("Delete record", "Delete %s (%s)? This cannot be undone." % (name, r["clientId"])):
            try:
                (APP_DIR / (r["clientId"] + ".json")).unlink()
            except OSError as e:
                messagebox.showerror("Delete", str(e))
            self.load_records()

    def export_all(self):
        if not self.records:
            messagebox.showinfo("Export", "There are no saved records yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")],
                                            initialfile="screening-records-%s.json" % date.today().isoformat())
        if path:
            atomic_write(Path(path), json.dumps(self.records, indent=2, ensure_ascii=False))
            messagebox.showinfo("Export", "Exported %d records." % len(self.records))

    def open_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(str(APP_DIR))  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(APP_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(APP_DIR)])
        except Exception as e:
            messagebox.showinfo("Folder", "Saved in:\n%s\n\n(%s)" % (APP_DIR, e))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()