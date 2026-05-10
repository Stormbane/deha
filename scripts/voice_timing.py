"""voice_timing.py -- extract a per-turn latency trace from HA + supervisor logs.

Step 0 of docs/plans/voice-performance-2026-05-09.md. The plan calls for
a real timing trace before optimizing anything; this is the tool that
produces it.

Two log sources, each with its own clock:

  Home Assistant   docker exec homeassistant cat /config/home-assistant.log
                   timestamps are UTC, naive ("2026-05-09 07:29:00.796")
  Supervisor       %LOCALAPPDATA%/narada/logs/supervisor.log (Windows)
                   timestamps are local wall clock, naive
                   wraps brain_server stdout under "[brain]" prefix and
                   contains both supervisor lines and inner narada.tts.wyoming
                   logger lines

We normalize both to UTC by reading the system tz offset, then align by
absolute UTC time. A "turn" is the span from "Initialized microVAD" to
the matching "synth_done" (or "conversation result" if TTS markers are
missing). The most recent turn is picked by default; --user-text picks
the most recent turn whose STT result starts with that text.

Usage:
    python scripts/voice_timing.py
    python scripts/voice_timing.py --user-text "good morning"
    python scripts/voice_timing.py --append-to docs/plans/voice-performance-2026-05-09.md
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ---------- log sourcing ----------


def fetch_ha_log() -> list[str]:
    """Fetch the live HA log via `docker exec`."""
    result = subprocess.run(
        ["docker", "exec", "homeassistant", "cat", "/config/home-assistant.log"],
        capture_output=True, text=True, errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(
            f"docker exec failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.splitlines()


def supervisor_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "narada" / "logs" / "supervisor.log"
    return Path.home() / ".narada" / "logs" / "supervisor.log"


def fetch_supervisor_log() -> list[str]:
    p = supervisor_log_path()
    if not p.exists():
        raise SystemExit(f"supervisor.log not found at {p}")
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


# ---------- timestamp parsing ----------

# HA: "2026-05-09 07:29:00.796 DEBUG (MainThread) [...] message"
_HA_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\w+\s+\(")
# Supervisor outer: "2026-05-09 17:29:00 narada.supervisor INFO ..."
_SUP_OUTER_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\S+\s+\w+")
# Supervisor INNER (brain_server logger line wrapped under [brain]):
#   "[brain] 2026-05-10 04:03:50,505 narada.tts.wyoming INFO synth ..."
# Python logging default uses ',mmm' for milliseconds.
_SUP_INNER_TS_RE = re.compile(
    r"\[brain\]\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})\s+\S+\s+\w+"
)


def _local_offset() -> timedelta:
    """Current system UTC offset. Used to interpret naive supervisor-log
    timestamps as local wall clock."""
    return datetime.now().astimezone().utcoffset() or timedelta(0)


def parse_ha_ts(line: str) -> datetime | None:
    m = _HA_TS_RE.match(line)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(
        tzinfo=timezone.utc
    )


def parse_supervisor_ts(line: str, local_offset: timedelta) -> datetime | None:
    """Prefer the inner brain_server logger timestamp (millisecond precision)
    when present, fall back to the supervisor's outer prefix (second
    precision)."""
    inner = _SUP_INNER_TS_RE.search(line)
    if inner:
        naive = datetime.strptime(inner.group(1), "%Y-%m-%d %H:%M:%S")
        naive = naive.replace(microsecond=int(inner.group(2)) * 1000)
        return naive.replace(tzinfo=timezone(local_offset)).astimezone(timezone.utc)
    m = _SUP_OUTER_TS_RE.match(line)
    if not m:
        return None
    naive = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=timezone(local_offset)).astimezone(timezone.utc)


# ---------- event extraction ----------


@dataclass
class Event:
    t: datetime          # UTC
    source: str          # "ha" or "sup"
    kind: str            # canonical name, e.g. "vad_start"
    payload: str = ""    # extra content (text, ms, ...)

    def __str__(self) -> str:
        suffix = f"  ({self.payload})" if self.payload else ""
        return f"[{self.source}] {self.kind}{suffix}"


# HA markers we care about. Order matters: first match wins per line.
_HA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("microvad_init",      re.compile(r"assist_pipeline\.audio_enhancer\] Initialized microVAD")),
    ("vad_start",          re.compile(r"assist_pipeline\.vad\] Voice command started")),
    ("vad_end",            re.compile(r"assist_pipeline\.vad\] Voice command finished")),
    ("stt_result",         re.compile(r"speech-to-text result SpeechResult\(text='([^']*)'")),
    ("conversation_post",  re.compile(r"conversation\.agent_manager\] Processing in en:\s*(.+)$")),
    ("conversation_done",  re.compile(r"assist_pipeline\.pipeline\] conversation result ConversationResult")),
]

# Supervisor markers (matched on the trailing portion after the prefix).
_SUP_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("converse_in",        re.compile(r"\[converse\] cid=\S+ user=(.+)$")),
    # Optional ttft=Xms field added between continue and the parens.
    ("converse_out",       re.compile(r"\[converse\] cid=\S+ reply=.+continue=\S+\s+(?:ttft=\d+ms\s+)?\((\d+) ms\)\s*$")),
    ("synth_start",        re.compile(r"narada\.tts\.wyoming INFO synth voice=(\S+) text=")),
    ("synth_first_chunk",  re.compile(r"narada\.tts\.wyoming INFO first_chunk voice=(\S+)")),
    ("synth_done",         re.compile(r"narada\.tts\.wyoming INFO synth_done voice=(\S+) chunks=(\d+)")),
]


def parse_ha_events(lines: list[str]) -> list[Event]:
    out: list[Event] = []
    for line in lines:
        ts = parse_ha_ts(line)
        if ts is None:
            continue
        for kind, pat in _HA_PATTERNS:
            m = pat.search(line)
            if m:
                payload = m.group(1).strip() if m.groups() else ""
                out.append(Event(t=ts, source="ha", kind=kind, payload=payload))
                break
    return out


def parse_supervisor_events(lines: list[str]) -> list[Event]:
    offset = _local_offset()
    out: list[Event] = []
    for line in lines:
        ts = parse_supervisor_ts(line, offset)
        if ts is None:
            continue
        for kind, pat in _SUP_PATTERNS:
            m = pat.search(line)
            if m:
                payload = m.group(1).strip() if m.groups() else ""
                # converse_out has two groups; payload should be the ms count
                if kind == "converse_out":
                    payload = m.group(1)  # ms
                out.append(Event(t=ts, source="sup", kind=kind, payload=payload))
                break
    return out


# ---------- turn segmentation ----------


def _normalize_text(s: str) -> str:
    """Strip surrounding quotes and whitespace from an STT text fragment."""
    s = s.strip().strip("'\"").strip()
    return s


def find_turn(
    events: list[Event],
    user_text: str | None,
) -> list[Event]:
    """Pick one turn out of the merged event stream.

    Strategy:
      1. Walk events forward, grouping into candidate turns. A new turn
         starts at each microvad_init (or, if missing, at each vad_start
         that's >2s after the previous turn's last event).
      2. Each candidate is associated with the stt_result text, if any.
      3. If --user-text is given, pick the most recent turn whose stt
         text matches (case-insensitive, prefix or substring). Otherwise
         pick the most recent turn that has at least a converse_out.
    """
    events = sorted(events, key=lambda e: e.t)

    # Group into turns
    turns: list[list[Event]] = []
    current: list[Event] = []
    last_t: datetime | None = None
    GAP = timedelta(seconds=15)

    for ev in events:
        is_new = False
        if ev.kind == "microvad_init":
            is_new = True
        elif current and last_t and (ev.t - last_t) > GAP and ev.kind in {
            "vad_start", "converse_in",
        }:
            is_new = True
        if is_new and current:
            turns.append(current)
            current = []
        current.append(ev)
        last_t = ev.t
    if current:
        turns.append(current)

    if not turns:
        return []

    def turn_text(t: list[Event]) -> str:
        for ev in t:
            if ev.kind == "stt_result":
                return _normalize_text(ev.payload)
            if ev.kind == "converse_in":
                return _normalize_text(ev.payload)
        return ""

    if user_text:
        target = user_text.strip().lower()
        for t in reversed(turns):
            if target in turn_text(t).lower():
                return t
        return []

    # Default: most recent turn that actually has a converse_out (real
    # completed turn rather than a partial wake)
    for t in reversed(turns):
        if any(e.kind == "converse_out" for e in t):
            return t
    return turns[-1]


# ---------- formatting ----------


_KIND_LABELS = {
    "microvad_init":     "microVAD initialized",
    "vad_start":         "voice command started",
    "vad_end":           "voice command finished",
    "stt_result":        "STT result",
    "conversation_post": "HA -> /converse",
    "converse_in":       "/converse received",
    "converse_out":      "/converse returned",
    "conversation_done": "HA conversation step done",
    "synth_start":       "TTS Synthesize received",
    "synth_first_chunk": "first audio chunk",
    "synth_done":        "TTS done (AudioStop)",
}


def format_trace(turn: list[Event]) -> str:
    if not turn:
        return "(no turn matched)"
    t0 = turn[0].t
    lines: list[str] = []
    lines.append(f"# T0 = {t0.isoformat()}  ({turn[0].kind})")
    lines.append("")
    for ev in turn:
        dt = (ev.t - t0).total_seconds()
        label = _KIND_LABELS.get(ev.kind, ev.kind)
        extra = ""
        if ev.kind in {"stt_result", "converse_in"} and ev.payload:
            text = _normalize_text(ev.payload)
            if len(text) > 60:
                text = text[:57] + "..."
            extra = f"  '{text}'"
        elif ev.kind == "converse_out" and ev.payload:
            extra = f"  ({ev.payload} ms server-side)"
        elif ev.kind == "synth_done" and ev.payload:
            extra = f"  ({ev.payload} chunks)"
        lines.append(f"T={dt:6.3f}  [{ev.source}] {label}{extra}")

    # Summary derived metrics
    lines.append("")
    lines.append("## derived")
    metrics = compute_metrics(turn)
    for name, val in metrics.items():
        lines.append(f"  {name:<30} {val}")
    return "\n".join(lines)


def compute_metrics(turn: list[Event]) -> dict[str, str]:
    """Pull out the headline numbers.

    The HEADLINE is user-perceived first-audio latency: from when the
    user stopped speaking (vad_end) to when the first audio chunk goes
    on the wire (synth_first_chunk). Everything else is sub-stage
    breakdown to localize the cost.

    Common confusion to avoid: 'TTS first-chunk after synth' looks
    fast (~500ms on CPU Kokoro), but if HA is BATCHING the conversation
    deltas before invoking TTS, that 500ms only starts AFTER the full
    claude reply is generated. End-to-end is the only honest metric
    for what the user actually hears.
    """
    by_kind: dict[str, datetime] = {}
    for ev in turn:
        by_kind.setdefault(ev.kind, ev.t)  # first occurrence

    def diff(a: str, b: str) -> str:
        if a in by_kind and b in by_kind:
            return f"{(by_kind[b] - by_kind[a]).total_seconds():6.3f} s"
        return "(missing)"

    return {
        # ---- HEADLINE ----
        "*** USER-PERCEIVED first audio (vad_end -> first_chunk)":
            diff("vad_end", "synth_first_chunk"),
        # ---- sub-stage breakdown ----
        "  STT (vad_end -> stt_result)":      diff("vad_end", "stt_result"),
        "  Conversation (claude)":            diff("converse_in", "converse_out"),
        "  HA buffer-then-TTS (converse_out -> synth_start)":
            diff("converse_out", "synth_start"),
        "  TTS engine first-chunk (synth_start -> synth_first_chunk)":
            diff("synth_start", "synth_first_chunk"),
        "  TTS total (synth_start -> synth_done)":
            diff("synth_start", "synth_done"),
        # If HA is dispatching TTS per-sentence (true streaming), the
        # 'HA buffer-then-TTS' row above is small (tens of ms); if HA is
        # waiting for the full conversation reply before invoking TTS
        # (no streaming TTS), that row equals the conversation latency
        # and the headline reflects it. That row is the diagnostic for
        # whether streaming TTS is actually live.
    }


# ---------- cli ----------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--user-text", default=None,
        help="Substring to match against the STT result; picks the most "
             "recent turn whose recognized text contains this.",
    )
    ap.add_argument(
        "--append-to", type=Path, default=None,
        help="If given, append a markdown-formatted trace block to this "
             "file (under a '## Captured timing traces' section).",
    )
    ap.add_argument(
        "--label", default=None,
        help="Optional short label for the trace (used in the appended "
             "block heading). Defaults to the matched user text.",
    )
    args = ap.parse_args()

    ha_lines = fetch_ha_log()
    sup_lines = fetch_supervisor_log()

    events = parse_ha_events(ha_lines) + parse_supervisor_events(sup_lines)
    if not events:
        print("No events parsed from either log.", file=sys.stderr)
        return 1

    turn = find_turn(events, args.user_text)
    if not turn:
        print(
            "No matching turn found. "
            f"({len(events)} events parsed; try without --user-text)",
            file=sys.stderr,
        )
        return 2

    rendered = format_trace(turn)
    print(rendered)

    if args.append_to:
        label = args.label
        if not label:
            for ev in turn:
                if ev.kind in {"stt_result", "converse_in"}:
                    label = _normalize_text(ev.payload)[:60]
                    break
        label = label or "trace"
        captured_at = datetime.now().astimezone().isoformat(timespec="seconds")

        block = (
            f"\n### {captured_at} -- {label}\n\n"
            f"```\n{rendered}\n```\n"
        )
        path = args.append_to
        if not path.exists():
            print(f"--append-to: {path} does not exist", file=sys.stderr)
            return 3
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
        print(f"\nappended to {path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
