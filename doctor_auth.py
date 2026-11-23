"""Doctor review and password sign-off for AI recommendations.

- A doctor profile (name, qualification, registration number and a password
  hash) is stored in the assistant's config file. The password itself is
  never stored, only a salted PBKDF2 hash.
- review_and_sign() shows the AI text in an editable box. Nothing can be
  exported until the doctor has reviewed it and entered the password.

This is a password-verified electronic sign-off recorded with a content hash.
It is not a certified digital signature (no certificate authority involved).
"""
import hashlib
import hmac
import secrets
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import ai_schema

ITERATIONS = 200_000
MIN_PASSWORD = 8
MAX_ATTEMPTS = 5


# ---------------------------------------------------------------- pure logic
def _hash(password, salt_hex):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), ITERATIONS
    ).hex()


def make_profile(name, qualification, reg_no, password):
    salt = secrets.token_hex(16)
    return {
        "name": name.strip(),
        "qualification": qualification.strip(),
        "regNo": reg_no.strip(),
        "salt": salt,
        "hash": _hash(password, salt),
        "iterations": ITERATIONS,
    }


def verify_password(password, profile):
    try:
        return hmac.compare_digest(_hash(password, profile["salt"]), profile["hash"])
    except (KeyError, ValueError, TypeError):
        return False


def profile_ok(profile):
    return (isinstance(profile, dict)
            and all(profile.get(k) for k in ("name", "regNo", "salt", "hash")))


def text_digest(text, client_id, signed_at):
    """SHA-256 over what was signed, so later changes can be detected."""
    payload = "\n".join([str(client_id or ""), signed_at, text.strip()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- dialogs
def setup_profile(parent, existing=None):
    """Create (or, with the current password, change) the doctor profile.

    Returns the new profile dict, or None if cancelled.
    """
    win = tk.Toplevel(parent)
    win.title("Doctor profile")
    win.transient(parent)
    win.resizable(False, False)
    win.grab_set()
    result = {"profile": None}

    body = ttk.Frame(win, padding=16)
    body.pack(fill="both", expand=True)

    ttk.Label(
        body,
        text="Details printed with your sign-off" if not existing else "Change doctor profile",
        font=("Segoe UI", 12, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

    fields = {}
    rows = [
        ("name", "Name (as printed, e.g. Dr. A. Kumar)", False),
        ("qual", "Qualification (e.g. MD Pathology)", False),
        ("reg", "Medical registration number", False),
    ]
    r = 1
    if existing:
        ttk.Label(body, text="Current password").grid(row=r, column=0, sticky="w", pady=3)
        fields["cur"] = tk.StringVar()
        ttk.Entry(body, textvariable=fields["cur"], show="*", width=34).grid(row=r, column=1, pady=3)
        r += 1
    for key, label, _ in rows:
        ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
        fields[key] = tk.StringVar(value={
            "name": (existing or {}).get("name", ""),
            "qual": (existing or {}).get("qualification", ""),
            "reg": (existing or {}).get("regNo", ""),
        }[key])
        ttk.Entry(body, textvariable=fields[key], width=34).grid(row=r, column=1, pady=3)
        r += 1
    for key, label in (("pw1", "New password" if existing else "Password"),
                       ("pw2", "Repeat password")):
        ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=3)
        fields[key] = tk.StringVar()
        ttk.Entry(body, textvariable=fields[key], show="*", width=34).grid(row=r, column=1, pady=3)
        r += 1
    ttk.Label(
        body,
        text="At least %d characters. The password is not stored, only a hash. "
             "If you forget it, it cannot be recovered." % MIN_PASSWORD,
        foreground="#666666", wraplength=420, justify="left",
    ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 10))

    def save():
        if existing and not verify_password(fields["cur"].get(), existing):
            messagebox.showerror("Doctor profile", "Current password is incorrect.", parent=win)
            return
        name, reg = fields["name"].get().strip(), fields["reg"].get().strip()
        pw1, pw2 = fields["pw1"].get(), fields["pw2"].get()
        if not name or not reg:
            messagebox.showwarning("Doctor profile", "Name and registration number are required.", parent=win)
            return
        if len(pw1) < MIN_PASSWORD:
            messagebox.showwarning("Doctor profile", "Password must be at least %d characters." % MIN_PASSWORD, parent=win)
            return
        if pw1 != pw2:
            messagebox.showwarning("Doctor profile", "The two passwords do not match.", parent=win)
            return
        result["profile"] = make_profile(name, fields["qual"].get(), reg, pw1)
        win.destroy()

    btns = ttk.Frame(body)
    btns.grid(row=r + 1, column=0, columnspan=2, sticky="e")
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(btns, text="Save profile", command=save).pack(side="right")

    win.wait_window()
    return result["profile"]


def review_and_sign(parent, ai_payload, get_profile, set_profile, client_id=None):
    """Let the doctor edit the AI answer and sign it with a password.

    ai_payload is either the structured answer (dict from ai_schema) or plain
    text. get_profile() returns the stored profile (or None); set_profile(p)
    saves a new one. Returns a dict with the final text, the final structured
    answer (None for plain text) and signature details, or None if the doctor
    cancelled or failed to authenticate.
    """
    if not profile_ok(get_profile()):
        new = setup_profile(parent)
        if new is None:
            return None
        set_profile(new)
    return _review_window(parent, ai_payload, get_profile, set_profile, client_id)


def _review_window(parent, payload, get_profile, set_profile, client_id):
    structured = isinstance(payload, dict)
    original_text = ai_schema.to_text(payload) if structured else payload.strip()
    bold = ("Segoe UI", 10, "bold")

    win = tk.Toplevel(parent)
    win.title("Doctor review and sign-off")
    if structured:
        win.geometry("860x%d" % min(820, max(560, win.winfo_screenheight() - 90)))
        win.minsize(680, 540)
    else:
        win.geometry("820x700")
        win.minsize(640, 520)
    win.transient(parent)
    win.grab_set()
    result = {"value": None}
    attempts = {"n": 0}

    top = ttk.Frame(win, padding=(14, 12, 14, 4))
    top.pack(fill="x")
    ttk.Label(top, text="Review the AI recommendations", font=("Segoe UI", 13, "bold")).pack(anchor="w")
    hint = ("Edit the summary and the headed sections in the first box: the summary comes first, then each "
            "section starts with a line \"## Heading\" followed by one point per line. Add, change or delete "
            "tests in the three boxes below it. One test per line; you may add a reason after a dash, e.g. "
            "\"HbA1c \u2014 diabetes screening\". A test goes into the colour band of the box it is in."
            if structured else
            "Edit, add or delete anything below. Keep the three headings "
            "(Top priority, Moderate priority, Optional) on their own lines so "
            "the PDF can colour-code the test list.")
    ttk.Label(top, text=hint, wraplength=800, justify="left", foreground="#555555").pack(anchor="w", pady=(2, 0))

    # The bottom block (tools, signer, password, buttons) is packed BEFORE the
    # text boxes, so a small screen shrinks the boxes, never the password row.
    footer = ttk.Frame(win)
    footer.pack(side="bottom", fill="x")

    mid = ttk.Frame(win, padding=(14, 6))
    mid.pack(fill="both", expand=True)

    def new_text(height):
        return tk.Text(mid, wrap="word", font=("Segoe UI", 11), undo=True, padx=8, pady=6, height=height)

    if structured:
        mid.columnconfigure(0, weight=1)
        ttk.Label(mid, text="Summary and clinical assessment (## Heading, then - points)",
                  font=bold).grid(row=0, column=0, sticky="w")
        narrative_box = new_text(8)
        narrative_box.grid(row=1, column=0, sticky="nsew")
        mid.rowconfigure(1, weight=3)
        boxes = {}
        row = 2
        for key, title, color in (("top", "Top priority", "#C62828"),
                                  ("moderate", "Moderate priority", "#E65100"),
                                  ("optional", "Optional", "#2E7D32")):
            ttk.Label(mid, text=title, font=bold, foreground=color).grid(row=row, column=0, sticky="w", pady=(8, 0))
            t = new_text(3)
            t.grid(row=row + 1, column=0, sticky="nsew")
            mid.rowconfigure(row + 1, weight=1)
            boxes[key] = t
            row += 2
        all_boxes = [narrative_box] + list(boxes.values())
    else:
        text = new_text(10)
        sb = ttk.Scrollbar(mid, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        all_boxes = [text]

    def load_original():
        if structured:
            narrative_box.delete("1.0", "end")
            narrative_box.insert("1.0", ai_schema.narrative_to_text(payload))
            for key, t in boxes.items():
                t.delete("1.0", "end")
                t.insert("1.0", ai_schema.tests_to_lines(payload.get("tests", []), key))
        else:
            text.delete("1.0", "end")
            text.insert("1.0", original_text)
        for t in all_boxes:
            t.edit_reset()

    def get_final():
        """(final text, final structured answer or None)"""
        if structured:
            analysis = ai_schema.analysis_from_boxes(
                narrative_box.get("1.0", "end-1c"),
                {k: t.get("1.0", "end-1c") for k, t in boxes.items()})
            return ai_schema.to_text(analysis), analysis
        return text.get("1.0", "end-1c").strip(), None

    load_original()

    tools = ttk.Frame(footer, padding=(14, 0))
    tools.pack(fill="x")

    def reset():
        if messagebox.askyesno("Reset", "Discard your edits and restore the AI text?", parent=win):
            load_original()

    ttk.Button(tools, text="Restore AI original", command=reset).pack(side="left")

    def change_profile():
        new = setup_profile(win, get_profile())
        if new:
            set_profile(new)
            refresh_signer()

    ttk.Button(tools, text="Doctor profile\u2026", command=change_profile).pack(side="left", padx=8)

    signer = ttk.Label(footer, padding=(14, 8, 14, 0), font=("Segoe UI", 10))
    signer.pack(fill="x")

    def refresh_signer():
        p = get_profile() or {}
        signer.config(text="Signing as: %s  |  %s  |  Reg. No. %s" % (
            p.get("name", "?"), p.get("qualification") or "-", p.get("regNo", "?")))

    refresh_signer()

    bottom = ttk.Frame(footer, padding=14)
    bottom.pack(fill="x")
    ttk.Label(bottom, text="Password:").pack(side="left")
    pw = tk.StringVar()
    entry = ttk.Entry(bottom, textvariable=pw, show="*", width=26)
    entry.pack(side="left", padx=8)

    def sign(event=None):
        final, analysis = get_final()
        if not final:
            messagebox.showwarning("Sign-off", "The text is empty.", parent=win)
            return
        profile = get_profile()
        if not verify_password(pw.get(), profile):
            attempts["n"] += 1
            pw.set("")
            left = MAX_ATTEMPTS - attempts["n"]
            if left <= 0:
                messagebox.showerror("Sign-off", "Too many incorrect attempts. Nothing was signed.", parent=win)
                win.destroy()
                return
            messagebox.showerror("Sign-off", "Incorrect password. %d attempt(s) left." % left, parent=win)
            entry.focus_set()
            return
        signed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        result["value"] = {
            "text": final,
            "analysis": analysis,
            "edited": final != original_text,
            "signed_at": signed_at,
            "digest": text_digest(final, client_id, signed_at),
            "name": profile["name"],
            "qualification": profile.get("qualification", ""),
            "reg_no": profile["regNo"],
        }
        win.destroy()

    entry.bind("<Return>", sign)
    ttk.Button(bottom, text="Cancel", command=win.destroy).pack(side="right")
    ttk.Button(bottom, text="Approve, sign and create PDF", command=sign).pack(side="right", padx=8)

    entry.focus_set()
    win.wait_window()
    return result["value"]
