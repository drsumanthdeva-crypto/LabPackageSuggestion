import json
import os
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import socket
import queue
import threading
from typing import List
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel
from keyring.backends.Windows import WinVaultKeyring

import report_pdf   # PDF export (report_pdf.py must sit next to this file)
import doctor_auth  # doctor review and password sign-off (doctor_auth.py too)
import ai_schema    # JSON form of the AI answer (ai_schema.py too)
import deidentify   # removes patient identifiers before sending (deidentify.py too)
import catalogue    # test price list for the PDF (catalogue.py too; needs: pip install openpyxl)

# ---------------- CONFIGURATION ----------------

APP_NAME = "My Gemini Assistant"

# Models with a free tier on the Gemini API (checked Oct 2026).
# Google changes this list often, so the dropdown is also editable:
# you can type any other model ID and it will be used as-is.
FREE_TIER_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
DEFAULT_MODEL = "gemini-3.1-flash-lite"

CREDENTIAL_SERVICE = "MyGeminiDesktopApp"
CREDENTIAL_USER = "GeminiAPIKey"

# The selected model is stored here (the API key stays in Credential Manager)
CONFIG_DIR = os.path.join(
    os.getenv("APPDATA", os.path.expanduser("~")), "MyGeminiAssistant"
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Languages for the first (explanatory) part of the answer.
# The closing list of laboratory tests is always requested in English.
LANGUAGES = ["Telugu", "Hindi", "English"]
DEFAULT_LANGUAGE = "English"

# Text placed in front of every client record sent to the model
CLIENT_PROMPT_BASE = (
    "Here is a client information, who came to a diagnostic laboratory for "
    "a preventive health checkup. Taking the role of an expert physician, what "
    "laboratory tests and other investigations would you advise, based on the "
    "client's history?"
)


class _TestItem(BaseModel):
    name: str        # test name, English
    priority: str    # top | moderate | optional
    reason: str      # one short English phrase


class _Section(BaseModel):
    heading: str          # section heading, in the chosen language
    points: List[str]     # one short point per entry, in the chosen language


class _Analysis(BaseModel):
    narrative: str        # short summary, in the chosen language
    sections: List[_Section]
    tests: List[_TestItem]


def client_prompt_text(language, cat=None):
    """Instruction text sent in front of the client record.

    With a price list (catalogue.Catalogue) the list of available tests is
    added, so the model picks tests the PDF can put a price on.
    """
    return (
        f"{CLIENT_PROMPT_BASE}\n"
        "Reply ONLY with JSON (no markdown, no code fences) in this form:\n"
        '{"narrative": "...", "sections": [{"heading": "...", "points": ["...", "..."]}], '
        '"tests": [{"name": "...", "priority": "top", "reason": "..."}]}\n'
        f'"narrative": a short summary (2 to 4 sentences) of the client\'s overall risk '
        f'picture and your main advice, written in {language}.\n'
        f'"sections": the detailed advice as separate headed sections, in this order, with '
        f'the headings and all points written in {language}:\n'
        '  1. Medical Evaluation and Diagnostic Recommendations: one point for each relevant '
        'body system, starting with the system name and a colon (for example '
        '"Cardiovascular: ...").\n'
        '  2. Key Areas of Clinical Assessment.\n'
        '  3. Non-Laboratory Diagnostic Investigations Recommended.\n'
        'Each point is one short, self-contained line. Do not use markdown, numbering or '
        'bullet characters inside the points.\n'
        '"tests": the laboratory tests you advise, one object per test. "name": the test '
        'name in English. "priority": exactly one of "top", "moderate", "optional". '
        '"reason": one short phrase in English. Total tests advised should not be less than 15'
        + (catalogue.prompt_block(cat) if cat else "")
    )


# Folder where client_intake.py saves its JSON files
CLIENTS_DIR = Path(
    os.environ.get("INTAKE_DATA_DIR")
    or Path(__file__).resolve().parent / "clients"
)

# Test price list (MRP and package price) printed in the PDF. Keep the file next
# to this script, or point the CATALOGUE_FILE environment variable at it. It is
# re-read whenever the file changes, so price edits apply without a restart.
# Without it (or without openpyxl) the app works as before, with no prices.
CATALOGUE_FILE = Path(
    os.environ.get("CATALOGUE_FILE")
    or Path(__file__).resolve().parent / "Preventive_Health_Test_Catalogue.xlsx"
)
_cat_state = {"key": None, "cat": None, "message": None}


def get_catalogue():
    """The loaded price list, or None. Re-reads the file when it has changed."""
    path = Path(CATALOGUE_FILE)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        _cat_state.update(key=None, cat=None)
        return None
    if _cat_state["key"] != key:
        try:
            cat = catalogue.load(path)
            msg = "Price list loaded: %d tests from %s." % (len(cat.priced_tests()), cat.source)
            if cat.is_placeholder:
                msg += " The file says its prices are test data."
            _cat_state.update(key=key, cat=cat, message=msg)
        except Exception as e:           # keep the previous list if the new file is unreadable
            _cat_state.update(key=key, message="Price list not used: %s" % e)
    return _cat_state["cat"]


# Single-window support: the first assistant window listens on this local
# port; client_intake.py (or a second launch) hands new records to it instead
# of opening another window. Only reachable from this computer.
SINGLE_INSTANCE_PORT = 49731

BG_COLOR = "#F4F6FA"
USER_COLOR = "#1565C0"
AI_COLOR = "#673AB7"
TEXT_COLOR = "#202124"

# Windows Credential Manager
vault = WinVaultKeyring()


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(**updates):
    """Merge the given settings into the config file."""
    try:
        data = load_config()
        data.update(updates)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print("Could not save settings:", e)


def load_saved_model():
    return str(load_config().get("model", "")).strip() or DEFAULT_MODEL


def load_saved_language():
    lang = load_config().get("language")
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def save_model_choice(model):
    save_config(model=model)


def forward_to_running_instance(path):
    """Ask an already open assistant window to analyze this record.

    Returns True if a running window accepted it.
    """
    try:
        with socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=1.5) as s:
            s.sendall((str(path) + "\n").encode("utf-8"))
            s.settimeout(3)
            return s.recv(16).strip() == b"OK"
    except OSError:
        return False


def is_allowed_client_file(path):
    """Only .json files inside the clients folder may be received remotely."""
    try:
        p = Path(path).resolve()
        return (p.suffix.lower() == ".json" and p.is_file()
                and CLIENTS_DIR.resolve() in p.parents)
    except OSError:
        return False


def build_client_prompt(json_path, language):
    """Read a client JSON and return (client_id, full prompt text, removals).

    The record is passed through deidentify.deidentify_record(): identifier
    fields are dropped (client ID, timestamps, name, mobile, location, date
    of birth), exact dates become "N months ago", and every free-text value
    (occupation, surgeries, medications, symptoms, ...) is scrubbed of phone
    numbers, names, hospitals, places, e-mails and similar. Age is kept.
    The client ID is kept locally only; it is never sent to the model.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        record = json.load(f)

    client_id = record.get("clientId", "client")
    clean, removed = deidentify.deidentify_record(record)
    body = json.dumps(clean, indent=2, ensure_ascii=False)
    return client_id, client_prompt_text(language, get_catalogue()) + "\n\n" + body, removed


class GeminiApp:

    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("850x650")
        self.root.minsize(600, 450)
        self.root.configure(bg=BG_COLOR)

        self.client = None
        self.chat = None
        self.busy = False
        self.current_ai_start = None
        self.model = load_saved_model()
        self.language = load_saved_language()
        self.response_parts = []        # text of the answer being / last received
        self.response_client_id = None  # client record that answer belongs to
        self.analysis = None            # structured answer (dict) of the last client analysis
        self.analysis_raw = None        # the raw JSON text the model returned
        self.response_ok = True         # False if the last answer failed or is incomplete
        self.events = queue.Queue()     # worker thread -> UI thread

        self.load_api_key()
        self.create_interface()

        if self.client:
            self.write_system(
                f"Welcome! Your Gemini Assistant is ready (model: {self.model})."
            )
        else:
            self.write_system(
                "Welcome! Please open Settings and enter your API key."
            )

        self.pending_path = None
        self.start_listener()
        self.root.after(60, self.poll_events)

        # Launched by client_intake.py with a JSON file path
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self.root.after(600, lambda: self.send_client_file(sys.argv[1]))

    # ---------------- SINGLE WINDOW ----------------

    def start_listener(self):
        """Listen (localhost only) for client records sent by other launches."""
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
            srv.listen(5)
        except OSError:
            return  # port busy: another window owns it, or sockets unavailable

        def serve():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                with conn:
                    try:
                        conn.settimeout(3)
                        data = conn.recv(4096).decode("utf-8", "ignore").strip()
                        if is_allowed_client_file(data):
                            self.root.after(0, lambda p=data: self.receive_client_file(p))
                            conn.sendall(b"OK\n")
                        else:
                            conn.sendall(b"NO\n")
                    except OSError:
                        pass

        threading.Thread(target=serve, daemon=True).start()

    def receive_client_file(self, path):
        """A record arrived from the intake form: bring window up and analyze."""
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

        if self.busy:
            self.pending_path = path
            self.write_system(
                "A response is still being written. The new client record "
                "will be analyzed as soon as it finishes."
            )
        else:
            self.send_client_file(path)

    # ---------------- API KEY / MODEL MANAGEMENT ----------------

    def load_api_key(self):
        try:
            api_key = vault.get_password(
                CREDENTIAL_SERVICE, CREDENTIAL_USER
            )

            if api_key:
                self.client = genai.Client(api_key=api_key)
                self.chat = self.client.chats.create(model=self.model)

        except Exception as e:
            self.client = None
            self.chat = None
            print("Credential loading error:", e)

    def save_api_key(self, api_key):
        vault.set_password(
            CREDENTIAL_SERVICE,
            CREDENTIAL_USER,
            api_key
        )
        self.client = genai.Client(api_key=api_key)

    def apply_model(self, model):
        """Switch to a new model, keeping the current conversation history."""
        history = None
        if self.chat is not None:
            try:
                history = self.chat.get_history()
            except Exception:
                history = None

        self.model = model
        save_model_choice(model)

        if self.client:
            try:
                self.chat = self.client.chats.create(
                    model=model, history=history
                )
            except Exception:
                # Fall back to a fresh chat if history can't be carried over
                self.chat = self.client.chats.create(model=model)

        self.model_label.config(text=f"Model: {self.model}")

    def open_settings(self):
        if self.busy:
            messagebox.showinfo(
                "Please wait",
                "Wait until the current response finishes."
            )
            return

        window = tk.Toplevel(self.root)
        window.title("Gemini Settings")
        window.geometry("470x370")
        window.resizable(False, False)
        window.transient(self.root)
        window.grab_set()

        ttk.Label(
            window,
            text="Gemini Configuration",
            font=("Segoe UI", 14, "bold")
        ).pack(pady=(18, 10))

        # ---- Model selection ----
        ttk.Label(
            window,
            text="Model (free tier):"
        ).pack(pady=(5, 2))

        models = list(FREE_TIER_MODELS)
        if self.model not in models:
            models.insert(0, self.model)

        model_var = tk.StringVar(value=self.model)

        model_box = ttk.Combobox(
            window,
            textvariable=model_var,
            values=models,
            width=45
            # editable on purpose: lets you type a newer model ID
        )
        model_box.pack(padx=20, pady=4)

        ttk.Label(
            window,
            text="Pick from the list or type any other model ID.",
            foreground="#666666"
        ).pack(pady=(0, 8))

        # ---- API key ----
        ttk.Label(
            window,
            text="Gemini API key:"
        ).pack(pady=(8, 2))

        key_var = tk.StringVar()

        entry = ttk.Entry(
            window,
            textvariable=key_var,
            show="*",
            width=48
        )
        entry.pack(padx=20, pady=4)

        hint = (
            "Leave blank to keep your saved key."
            if self.client
            else "Your key is stored in Windows Credential Manager."
        )
        ttk.Label(
            window,
            text=hint,
            foreground="#666666"
        ).pack(pady=5)

        def save():
            api_key = key_var.get().strip()
            model = model_var.get().strip()

            if not model:
                messagebox.showwarning(
                    "Missing Model",
                    "Please select or enter a model.",
                    parent=window
                )
                return

            if not api_key and not self.client:
                messagebox.showwarning(
                    "Missing API Key",
                    "Please enter your API key.",
                    parent=window
                )
                return

            try:
                if api_key:
                    self.save_api_key(api_key)

                model_changed = model != self.model
                self.apply_model(model)

                if api_key and model_changed:
                    self.write_system(
                        f"API key saved. Model set to {model}."
                    )
                elif api_key:
                    self.write_system("Gemini API key saved successfully.")
                elif model_changed:
                    self.write_system(f"Model changed to {model}.")
                else:
                    self.write_system("Settings unchanged.")

                window.destroy()

            except Exception as e:
                messagebox.showerror(
                    "Error",
                    str(e),
                    parent=window
                )

        ttk.Button(
            window,
            text="Save",
            command=save
        ).pack(pady=15)

        entry.focus_set()

    # ---------------- INTERFACE ----------------

    def create_interface(self):

        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # Header
        header = tk.Frame(
            self.root,
            bg="#263B63",
            height=65
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)

        tk.Label(
            header,
            text=APP_NAME,
            bg="#263B63",
            fg="white",
            font=("Segoe UI", 17, "bold")
        ).pack(side=tk.LEFT, padx=20, pady=15)

        ttk.Button(
            header,
            text="Settings",
            command=self.open_settings
        ).pack(side=tk.RIGHT, padx=12)

        ttk.Button(
            header,
            text="Clear Chat",
            command=self.clear_chat
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            header,
            text="Export PDF",
            command=self.export_pdf
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            header,
            text="Analyze Client JSON",
            command=self.pick_client_file
        ).pack(side=tk.RIGHT, padx=5)

        # Language of the explanatory part of the client analysis
        self.language_var = tk.StringVar(value=self.language)
        lang_box = ttk.Combobox(
            header,
            textvariable=self.language_var,
            values=LANGUAGES,
            state="readonly",
            width=9
        )
        lang_box.pack(side=tk.RIGHT, padx=(5, 0))
        lang_box.bind("<<ComboboxSelected>>", self.on_language_change)

        tk.Label(
            header,
            text="Response language:",
            bg="#263B63",
            fg="white",
            font=("Segoe UI", 9)
        ).pack(side=tk.RIGHT, padx=(10, 4))

        # Main chat area
        self.output = tk.Text(
            self.root,
            wrap=tk.WORD,
            font=("Segoe UI", 11),
            bg="white",
            fg=TEXT_COLOR,
            relief=tk.FLAT,
            padx=15,
            pady=15,
            state=tk.DISABLED
        )

        self.output.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=12,
            pady=12
        )

        # Speaker colors
        self.output.tag_config(
            "user_speaker",
            foreground=USER_COLOR,
            font=("Segoe UI", 11, "bold")
        )

        self.output.tag_config(
            "ai_speaker",
            foreground=AI_COLOR,
            font=("Segoe UI", 11, "bold")
        )

        self.output.tag_config(
            "message",
            foreground=TEXT_COLOR,
            font=("Segoe UI", 11)
        )

        self.output.tag_config(
            "heading",
            foreground="#263B63",
            font=("Segoe UI", 11, "bold")
        )

        self.output.tag_config(
            "system",
            foreground="#777777",
            font=("Segoe UI", 9, "italic")
        )

        self.output.tag_config(
            "status",
            foreground="#888888",
            font=("Segoe UI", 10, "italic")
        )

        # Input area - always visible
        bottom = tk.Frame(
            self.root,
            bg="#E8ECF3",
            padx=12,
            pady=12
        )

        bottom.grid(
            row=2,
            column=0,
            sticky="ew"
        )

        bottom.grid_columnconfigure(0, weight=1)

        self.entry = tk.Entry(
            bottom,
            font=("Segoe UI", 12),
            relief=tk.SOLID,
            bd=1
        )

        self.entry.grid(
            row=0,
            column=0,
            sticky="ew",
            ipady=9,
            padx=(0, 10)
        )

        self.entry.bind("<Return>", self.send_message)

        self.send_button = tk.Button(
            bottom,
            text="Send",
            command=self.send_message,
            bg="#263B63",
            fg="white",
            activebackground="#36558A",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            width=12,
            relief=tk.FLAT,
            cursor="hand2"
        )

        self.send_button.grid(
            row=0,
            column=1,
            sticky="e",
            ipady=5
        )

        # Status bar (status text on the left, active model on the right)
        status_bar = tk.Frame(self.root, bg=BG_COLOR)
        status_bar.grid(row=3, column=0, sticky="ew", padx=15, pady=5)

        self.status = tk.Label(
            status_bar,
            text="Ready",
            anchor="w",
            bg=BG_COLOR,
            fg="#777777",
            font=("Segoe UI", 9)
        )
        self.status.pack(side=tk.LEFT)

        self.model_label = tk.Label(
            status_bar,
            text=f"Model: {self.model}",
            anchor="e",
            bg=BG_COLOR,
            fg="#777777",
            font=("Segoe UI", 9)
        )
        self.model_label.pack(side=tk.RIGHT)

    # ---------------- CHAT DISPLAY ----------------

    def insert_text(self, text, tag=None):
        self.output.config(state=tk.NORMAL)

        if tag:
            self.output.insert(tk.END, text, tag)
        else:
            self.output.insert(tk.END, text)

        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

    def write_system(self, text):
        self.insert_text("\n" + text + "\n", "system")

    def write_user(self, text):
        self.insert_text("\nYou:\n", "user_speaker")
        self.insert_text(text + "\n", "message")

    def start_ai_response(self):
        self.output.config(state=tk.NORMAL)

        self.output.insert(
            tk.END,
            "\nAI Assistant:\n",
            "ai_speaker"
        )

        self.current_ai_start = self.output.index(tk.END)

        self.output.insert(
            tk.END,
            "Generating response...",
            "status"
        )

        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

    def append_ai_response(self, text):
        self.output.config(state=tk.NORMAL)

        if self.current_ai_start:
            self.output.delete(
                self.current_ai_start, tk.END
            )
            self.current_ai_start = None

        self.output.insert(tk.END, text, "message")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)
        self.response_parts.append(text)

    # ---------------- GEMINI STREAMING ----------------

    def send_message(self, event=None):

        question = self.entry.get().strip()

        if not question:
            return

        if self.submit(question, question):
            self.entry.delete(0, tk.END)

    def submit(self, display_text, prompt, client_id=None, structured=False):
        """Show display_text in the chat and send prompt to Gemini.

        Returns True if the request was started.
        """
        if self.busy:
            return False

        if not self.client or not self.chat:
            messagebox.showwarning(
                "API Key Required",
                "Please open Settings and enter your Gemini API key."
            )
            return False

        self.response_parts = []
        self.response_client_id = client_id
        self.analysis = None
        self.analysis_raw = None
        self.response_ok = True

        self.write_user(display_text)
        self.start_ai_response()

        self.busy = True
        self.send_button.config(state=tk.DISABLED)
        self.status.config(text="Gemini is processing your question...")

        threading.Thread(
            target=self.ask_gemini,
            args=(prompt, structured),
            daemon=True
        ).start()
        return True

    # ---------------- CLIENT RECORDS ----------------

    def pick_client_file(self):
        if self.busy:
            messagebox.showinfo(
                "Please wait",
                "Wait until the current response finishes."
            )
            return

        initial = CLIENTS_DIR if CLIENTS_DIR.is_dir() else Path.home()
        path = filedialog.askopenfilename(
            title="Select a client JSON file",
            initialdir=str(initial),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.send_client_file(path)

    def on_language_change(self, event=None):
        self.language = self.language_var.get()
        save_config(language=self.language)
        self.write_system(
            f"Client analysis language set to {self.language} "
            "(the test list at the end stays in English)."
        )

    def send_client_file(self, path):
        language = self.language
        try:
            client_id, prompt, removed = build_client_prompt(path, language)
        except Exception as e:
            messagebox.showerror(
                "Could not read client file",
                f"{path}\n\n{e}"
            )
            return

        get_catalogue()
        if _cat_state["key"] is None:      # no price list file at all: say so once
            if not _cat_state.get("warned"):
                _cat_state["warned"] = True
                self.write_system(f"No price list found ({CATALOGUE_FILE.name} next to the program), "
                                  "so the PDF will have no prices.")
        elif _cat_state["message"] and _cat_state.get("shown") != _cat_state["key"]:
            _cat_state["shown"] = _cat_state["key"]     # loaded (or unreadable): say so once per change
            self.write_system(_cat_state["message"])

        # Each client gets a fresh conversation so one client's details
        # never leak into the advice for the next.
        if self.client:
            try:
                self.chat = self.client.chats.create(model=self.model)
            except Exception:
                pass
        if self.response_client_id is not None or self.response_parts:
            self.write_system("---------- New client record ----------")

        self.submit(
            f"Analyze client record {client_id} (response language: {language}). "
            "Identifiers were removed before sending (name, mobile, location, date of "
            "birth, client ID; names, phone numbers, hospitals and places in free text: "
            f"{removed} item(s) scrubbed).",
            prompt,
            client_id=client_id,
            structured=True
        )

    # ---------------- PDF EXPORT ----------------

    def export_pdf(self):
        if self.busy:
            messagebox.showinfo(
                "Please wait",
                "Wait until the current response finishes."
            )
            return

        if not self.response_ok:
            messagebox.showwarning(
                "Incomplete response",
                "The last response failed or is incomplete, so it cannot be "
                "signed or exported. Please generate it again."
            )
            return

        text = "".join(self.response_parts).strip()
        if not text:
            messagebox.showinfo(
                "Nothing to export",
                "There is no AI response yet. Analyze a client record first."
            )
            return

        # Doctor must review (and may edit) the AI text and sign with a password
        signed = doctor_auth.review_and_sign(
            self.root,
            self.analysis if self.analysis else text,
            get_profile=lambda: load_config().get("doctor"),
            set_profile=lambda p: save_config(doctor=p),
            client_id=self.response_client_id,
        )
        if not signed:
            self.write_system("PDF export cancelled: not signed by a doctor.")
            return
        original_text = text
        text = signed["text"]

        reports_dir = CLIENTS_DIR / "reports"
        try:
            reports_dir.mkdir(parents=True, exist_ok=True)
            initial = reports_dir
        except OSError:
            initial = Path.home()

        name = f"{self.response_client_id or 'ai_response'}_advice.pdf"
        path = filedialog.asksaveasfilename(
            title="Save response as PDF",
            initialdir=str(initial),
            initialfile=name,
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")]
        )
        if not path:
            return

        self.status.config(text="Creating PDF...")
        self.root.update_idletasks()

        # Prices for the tests the doctor signed (never blocks the PDF if it fails)
        pricing = None
        cat = get_catalogue()
        if cat and signed.get("analysis"):
            try:
                pricing = catalogue.price_analysis(cat, signed["analysis"])
            except Exception as e:
                self.write_system(f"Prices could not be added to the PDF: {e}")

        meta = {
            "client_id": self.response_client_id,
            "language": self.language if self.response_client_id else None,
            "model": self.model,
            "signature": signed,
            "analysis": signed.get("analysis"),
            "pricing": pricing,
        }
        try:
            kind, out = report_pdf.export(text, meta, path)
        except Exception as e:
            self.status.config(text="Ready")
            messagebox.showerror("PDF export failed", str(e))
            return

        # Audit record next to the PDF: AI original, signed text, who and when
        try:
            audit = Path(path).with_suffix(".signed.json")
            with open(audit, "w", encoding="utf-8") as f:
                json.dump({
                    "clientId": self.response_client_id,
                    "model": self.model,
                    "language": meta["language"],
                    "aiOriginalText": original_text,
                    "aiOriginalJson": self.analysis_raw,
                    "signedAnalysis": signed.get("analysis"),
                    "signedText": signed["text"],
                    "editedByDoctor": signed["edited"],
                    "signedBy": {
                        "name": signed["name"],
                        "qualification": signed["qualification"],
                        "registrationNo": signed["reg_no"],
                    },
                    "signedAt": signed["signed_at"],
                    "sha256": signed["digest"],
                    "pricing": pricing,      # price list used and the prices printed (None = no prices)
                }, f, indent=2, ensure_ascii=False)
        except OSError as e:
            self.write_system(f"Could not write the sign-off record: {e}")

        self.status.config(text="Ready")
        if kind == "pdf":
            self.write_system(f"PDF saved: {out}")
            try:
                if sys.platform == "win32":
                    os.startfile(str(out))
                else:
                    import webbrowser
                    webbrowser.open(Path(out).resolve().as_uri())
            except Exception:
                pass
        else:
            self.write_system(
                "Edge or Chrome could not be used automatically. A printable "
                f"page was saved ({out}) and opened in your browser; choose "
                "'Save as PDF' in the print window."
            )

    def ask_gemini(self, question, structured=False):
        """Runs in a worker thread. Talks to the UI only through self.events."""
        try:
            if structured:
                cfg = genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_Analysis,
                )
                resp = self.chat.send_message(question, config=cfg)
                raw = (resp.text or "").strip()
                if not raw:
                    raise ValueError("Gemini returned no text (the reply may have been blocked). "
                                     "Please try again.")
                data = ai_schema.parse_ai_json(raw)      # ValueError if not valid / complete
                self.events.put(("analysis", (data, raw)))
                return

            got_text = False
            for chunk in self.chat.send_message_stream(question):
                if chunk.text:
                    if not got_text:
                        self.events.put(("first", None))
                        got_text = True
                    self.events.put(("chunk", chunk.text))
            if not got_text:
                raise ValueError("Gemini returned no text (the reply may have been blocked). "
                                 "Please try again.")
            self.events.put(("done", None))

        except Exception as e:
            self.events.put(("error", str(e)))

    def poll_events(self):
        """UI thread: apply what the worker thread queued."""
        try:
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "first":
                        self.clear_generating_status()
                    elif kind == "chunk":
                        self.append_ai_response(payload)
                    elif kind == "analysis":
                        self.show_analysis(*payload)
                    elif kind == "done":
                        self.finish_response()
                    elif kind == "error":
                        self.show_error(payload)
                except Exception as e:
                    print("UI update error:", e)
        finally:
            self.root.after(60, self.poll_events)

    def show_analysis(self, data, raw):
        """Show a validated structured answer and remember it for sign-off/export."""
        self.clear_generating_status()
        text = ai_schema.to_text(data)
        self.analysis = data
        self.analysis_raw = raw
        self.response_parts = [text]
        self.output.config(state=tk.NORMAL)
        first = True

        def put(chunk, tag):
            nonlocal first
            self.output.insert(tk.END, chunk, tag)
            first = False

        def gap():
            if not first:
                self.output.insert(tk.END, "\n\n", "message")

        summary = (data.get("narrative") or "").strip()
        if summary:
            put(summary, "message")
        for sec in data.get("sections") or []:
            gap()
            if sec.get("heading"):
                put(sec["heading"] + "\n", "heading")
            put("\n".join("- " + p for p in sec.get("points", [])), "message")
        for prio in ai_schema.PRIORITIES:
            items = ai_schema.tests_of(data, prio)
            if items:
                gap()
                put(ai_schema.TITLES[prio] + "\n", "heading")
                put("\n".join("- " + t["name"] + (ai_schema.SEP + t["reason"] if t.get("reason") else "")
                              for t in items), "message")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)
        self.note_unpriced(data)
        self.finish_response()

    def note_unpriced(self, analysis):
        """Tell the doctor which tests will print without a price, before sign-off."""
        cat = get_catalogue()
        if not cat:
            return
        try:
            pr = catalogue.price_analysis(cat, analysis)
        except Exception as e:
            print("Price check error:", e)
            return
        if pr["unpriced"]:
            self.write_system(
                "Not found in the price list, so the PDF will show \"Price on request\": "
                + "; ".join(pr["unpriced"])
                + ". In the review window, rename a test to its price-list name to get a price."
            )
        if pr["duplicates"]:
            self.write_system("Listed more than once (counted once in the totals): "
                              + "; ".join(pr["duplicates"]) + ".")

    def clear_generating_status(self):
        self.output.config(state=tk.NORMAL)

        # Find and remove the exact text "Generating response..."
        generating_text = "Generating response..."
        start = self.output.search(generating_text, "1.0", tk.END)

        if start:
            end = f"{start}+{len(generating_text)}c"
            self.output.delete(start, end)

        self.current_ai_start = None

        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

    def finish_response(self):
        self.insert_text("\n")
        self.busy = False
        self.send_button.config(state=tk.NORMAL)
        self.status.config(text="Ready")
        self.entry.focus_set()

        if self.pending_path:
            path, self.pending_path = self.pending_path, None
            self.root.after(300, lambda: self.send_client_file(path))

    def show_error(self, error):
        self.response_ok = False
        self.clear_generating_status()
        self.insert_text(
            "\nError: " + error + "\n",
            "system"
        )
        self.finish_response()

    # ---------------- CLEAR CHAT ----------------

    def clear_chat(self):

        if self.busy:
            messagebox.showinfo(
                "Please wait",
                "Wait until the current response finishes."
            )
            return

        if not messagebox.askyesno(
            "Clear Chat",
            "Do you want to clear the conversation?"
        ):
            return

        try:
            if self.client:
                self.chat = self.client.chats.create(
                    model=self.model
                )

            self.output.config(state=tk.NORMAL)
            self.output.delete("1.0", tk.END)
            self.output.config(state=tk.DISABLED)

            self.response_parts = []
            self.response_client_id = None
            self.analysis = None
            self.analysis_raw = None
            self.response_ok = True

            self.write_system("New conversation started.")

        except Exception as e:
            messagebox.showerror("Error", str(e))


# ---------------- START APPLICATION ----------------

if __name__ == "__main__":

    # If a window is already open, hand it the record and exit
    if (len(sys.argv) > 1 and is_allowed_client_file(sys.argv[1])
            and forward_to_running_instance(sys.argv[1])):
        sys.exit(0)

    root = tk.Tk()
    app = GeminiApp(root)

    root.mainloop()
