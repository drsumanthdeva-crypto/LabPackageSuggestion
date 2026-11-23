"""Structured (JSON) form of the AI recommendation.

The model is asked for:
    {"narrative": "short summary",
     "sections": [{"heading": "...", "points": ["...", "..."]}],
     "tests": [{"name": "...", "priority": "top|moderate|optional", "reason": "..."}]}

This module has no GUI or network code, so it can be tested on its own.
  parse_ai_json()        - raw model text -> validated dict (raises ValueError)
  to_text()              - dict -> readable text (chat window, digest, audit file)
  narrative_to_text()    - summary + sections as ONE editable text box:
                               summary paragraph(s)
                               ## Heading
                               - point
  text_to_narrative()    - the reverse (used by the doctor's review window)
  tests_to_lines() / analysis_from_boxes() - used by the doctor's review window

"sections" is optional: an answer with only "narrative" (older records, or a
model that ignored the field) still works.
"""
import json
import re

PRIORITIES = ("top", "moderate", "optional")
TITLES = {"top": "Top priority", "moderate": "Moderate priority", "optional": "Optional"}

_SYNONYMS = {
    "top": "top", "high": "top", "top priority": "top", "high priority": "top",
    "moderate": "moderate", "medium": "moderate",
    "moderate priority": "moderate", "medium priority": "moderate",
    "optional": "optional", "low": "optional", "low priority": "optional",
}
_SPLIT = re.compile(r"\s+[—–]\s+")      # " — " or " – " between name and reason
_LEAD = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")   # "## Heading" in the editable text
SEP = " — "


def _clean(s):
    """One line, single spaces; keeps the name/reason separator unambiguous."""
    s = " ".join(str(s).split())
    return _SPLIT.sub(" - ", s)


# ---------------------------------------------------------------- narrative / sections
def narrative_to_text(analysis):
    """Summary and sections as one editable text (see module doc)."""
    parts = [(analysis.get("narrative") or "").strip()]
    for sec in analysis.get("sections") or []:
        lines = ["## " + sec.get("heading", "")] + ["- " + p for p in sec.get("points", [])]
        parts.append("\n".join(lines))
    return "\n\n".join(x for x in parts if x)


def text_to_narrative(text):
    """Editable text -> (summary, [{'heading', 'points'}]).

    Everything before the first '## ' line is the summary. After a heading,
    every non-empty line is a point (a leading dash, bullet or number is
    removed). Text with no '## ' line at all is just a summary.
    """
    summary, sections, cur = [], [], None
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        m = _HEADING.match(line)
        if m:
            cur = {"heading": _clean(m.group(1)), "points": []}
            sections.append(cur)
        elif cur is None:
            summary.append(line)
        else:
            p = _clean(_LEAD.sub("", line))
            if p:
                cur["points"].append(p)
    sections = [s for s in sections if s["heading"] or s["points"]]
    return "\n".join(summary).strip(), sections


def _clean_sections(raw):
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('The AI reply has an invalid "sections" value (expected a list).')
    out = []
    for i, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            raise ValueError("Section %d is not an object." % i)
        heading = _clean(s.get("heading") or "").lstrip("# ").strip()
        pts = s.get("points")
        if pts is None:
            pts = []
        elif isinstance(pts, str):
            pts = [pts]
        elif not isinstance(pts, list):
            raise ValueError("Section %d has an invalid \"points\" value." % i)
        points = []
        for p in pts:
            if isinstance(p, str):
                p = _clean(_LEAD.sub("", p))
                if p:
                    points.append(p)
        if heading or points:
            out.append({"heading": heading or "Notes", "points": points})
    return out


def normalise(data):
    """Validate and tidy a decoded JSON value. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("The AI reply is not a JSON object.")
    narrative = data.get("narrative")
    if not isinstance(narrative, str):
        raise ValueError('The AI reply has no "narrative" text.')
    tests = data.get("tests")
    if not isinstance(tests, list):
        raise ValueError('The AI reply has no "tests" list.')
    # A narrative that carries its own "## Heading" lines is split into
    # sections too, so the structure survives whichever way the model sent it.
    summary, from_text = text_to_narrative(narrative)
    sections = from_text + _clean_sections(data.get("sections"))
    out = []
    for i, t in enumerate(tests, 1):
        if not isinstance(t, dict):
            raise ValueError("Test %d is not an object." % i)
        name = _clean(t.get("name") or "")
        if not name:
            continue
        prio = _SYNONYMS.get(str(t.get("priority", "")).strip().lower())
        if prio is None:
            raise ValueError("Test '%s' has an unknown priority: %r" % (name, t.get("priority")))
        reason = t.get("reason")
        out.append({"name": name, "priority": prio,
                    "reason": _clean(reason) if isinstance(reason, str) else ""})
    return {"narrative": summary, "sections": sections, "tests": out}


def parse_ai_json(raw):
    """Raw model text -> validated dict. Raises ValueError with a readable message."""
    s = (raw or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S | re.I)
    if m:
        s = m.group(1)
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError("The AI reply was not complete, valid JSON (it may have been cut "
                         "off): %s" % e)
    return normalise(data)


# ---------------------------------------------------------------- tests
def tests_of(analysis, priority):
    return [t for t in analysis.get("tests", []) if t.get("priority") == priority]


def tests_to_lines(tests, priority):
    """Tests of one priority as editable text, one per line."""
    lines = []
    for t in tests:
        if t.get("priority") != priority:
            continue
        lines.append(t["name"] + (SEP + t["reason"] if t.get("reason") else ""))
    return "\n".join(lines)


def lines_to_tests(text, priority):
    out = []
    for line in (text or "").splitlines():
        line = _LEAD.sub("", line).strip()
        if not line:
            continue
        parts = _SPLIT.split(line, maxsplit=1)
        name = _clean(parts[0])
        if not name:
            continue
        reason = _clean(parts[1]) if len(parts) > 1 else ""
        out.append({"name": name, "priority": priority, "reason": reason})
    return out


def analysis_from_boxes(narrative_text, boxes):
    """narrative_text: the summary/sections box; boxes: {'top','moderate','optional'} texts."""
    summary, sections = text_to_narrative(narrative_text)
    tests = []
    for p in PRIORITIES:
        tests += lines_to_tests(boxes.get(p, ""), p)
    return {"narrative": summary, "sections": sections, "tests": tests}


def to_text(analysis):
    """Readable text, also used for the digest and the audit record."""
    parts = [(analysis.get("narrative") or "").strip()]
    for sec in analysis.get("sections") or []:
        head = [sec["heading"]] if sec.get("heading") else []
        parts.append("\n".join(head + ["- " + p for p in sec.get("points", [])]))
    for p in PRIORITIES:
        items = tests_of(analysis, p)
        if items:
            parts.append(TITLES[p] + "\n" + "\n".join(
                "- " + t["name"] + (SEP + t["reason"] if t.get("reason") else "") for t in items))
    return "\n\n".join(x for x in parts if x)
