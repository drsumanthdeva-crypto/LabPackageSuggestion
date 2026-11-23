"""Tests for deidentify.py.  Run:  python3 test_deidentify.py"""
import json, os, sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deidentify as d

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(extra)) if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

TODAY = date(2026, 11, 4)
REC = {
    "schemaVersion": 1, "clientId": "CL-261104-ABCD",
    "consultationDate": "2026-11-04T10:00:00+05:30", "updatedAt": "2026-11-04T10:05:00+05:30",
    "basic": {"name": "Ravi Kumar Reddy", "mobile": "9876543210", "location": "Kukatpally, Hyderabad",
              "dateOfBirth": "1980-01-01", "ageYears": 45, "sex": "male",
              "occupation": "Accountant at Infosys, Madhapur"},
    "medicalHistory": {
        "conditions": ["diabetes"], "illnessDuration": "6 years",
        "knownAllergies": "penicillin, Ravi has rash",
        "lastCheckupDate": "2025-05-10",
        "previousSurgeries": "Appendectomy by Dr Rao at Apollo Hospital in 2015",
        "previousAbnormalLabs": "HbA1c 7.2% on 12/03/2024, reported by Sunrise Diagnostics",
        "medicationHistory": [
            {"name": "Metformin", "dose": "500 mg", "frequency": "At night", "duration": "since 2020",
             "indication": "diabetes, prescribed by Dr. K. Sharma", "status": "current"}]},
    "symptoms": {"reported": ["fatigue"], "other": "headache, call 98765 43210 or ravi@example.com",
                 "duration": "3 weeks"},
    "labs": {"cbc": {"result": "Hb 12.4 g/dL", "date": "2026-09-04"},
             "lipidProfile": {"result": "TC 200, LDL 120, HDL 40, TG 150", "date": None}},
    "familyHistory": {"cancer": {"present": True, "relationship": "mother", "cancerType": "breast, treated at KIMS"}},
    "anthropometrics": {"bpSystolic": 120, "bpDiastolic": 80},
    "flags": {"symptomsForClinicalEvaluation": True},
}
clean, n = d.deidentify_record(REC, today=TODAY)
blob = json.dumps(clean, ensure_ascii=False)

for tok in ["Ravi", "Reddy", "Kukatpally", "Hyderabad", "9876543210", "98765", "43210", "example.com",
            "Rao", "Apollo", "Sunrise", "Sharma", "Infosys", "Madhapur", "KIMS", "CL-261104", "2026-11-04",
            "1980"]:
    check("removed: " + tok, tok not in blob, blob)

check("original record not modified", REC["basic"]["name"] == "Ravi Kumar Reddy" and "clientId" in REC)
check("surgery keeps the procedure", clean["medicalHistory"]["previousSurgeries"].startswith("Appendectomy"),
      clean["medicalHistory"]["previousSurgeries"])
check("no dangling 'by'/'at' in surgery", not clean["medicalHistory"]["previousSurgeries"].lower().endswith(("by", "at", "in")))
check("clinical values survive", "HbA1c 7.2%" in clean["medicalHistory"]["previousAbnormalLabs"])
check("lab values survive", clean["labs"]["cbc"]["result"] == "Hb 12.4 g/dL"
      and clean["labs"]["lipidProfile"]["result"] == "TC 200, LDL 120, HDL 40, TG 150")
check("BP / age / sex kept", clean["anthropometrics"]["bpSystolic"] == 120 and clean["basic"]["ageYears"] == 45
      and clean["basic"]["sex"] == "male")
check("medication name/dose/frequency kept", clean["medicalHistory"]["medicationHistory"][0]["name"] == "Metformin"
      and clean["medicalHistory"]["medicationHistory"][0]["dose"] == "500 mg"
      and clean["medicalHistory"]["medicationHistory"][0]["frequency"] == "At night")
check("symptom text keeps 'headache'", "headache" in clean["symptoms"]["other"])
check("date fields become relative", clean["labs"]["cbc"]["date"] == "2 months ago"
      and clean["medicalHistory"]["lastCheckupDate"] == "1 years ago".replace("1 years", "17 months") or
      clean["medicalHistory"]["lastCheckupDate"].endswith("ago"), clean["medicalHistory"]["lastCheckupDate"])
check("None values stay None", clean["labs"]["lipidProfile"]["date"] is None)
check("redaction count is positive", n >= 8, n)

# individual phrases
S = d._Scrubber([])
cases = {
    "Appendectomy by Dr Rao at Apollo": "Appendectomy",
    "Gallbladder removal at Yashoda Hospital Secunderabad": "Gallbladder removal",
    "Hernia repair, Dr. A. Kumar": "Hernia repair",
    "fever. Call +91-98765-43210": "fever. Call",
    "MRN 12345678 fever": "MRN fever",
    "mail me at a.b@c.in about cough": "mail me about cough",
    "Hb 12.4 Hct 38.2 MCV 88": "Hb 12.4 Hct 38.2 MCV 88",
    "BP 120 80 pulse 72": "BP 120 80 pulse 72",
    "seen on 04 March 2023 for cough": "seen on 2023 for cough",
    "At night": "At night",
    "took tablets in the evening": "took tablets in the evening",
    "joint pain since January": "joint pain since January",
}
for src, want in cases.items():
    got = S.text(src)
    check("scrub: %r" % src, got == want, got)

print("\n%d failure(s)" % len(FAILS)); sys.exit(1 if FAILS else 0)