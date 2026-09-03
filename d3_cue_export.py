#!/usr/bin/env python3
"""
Extract the cue list from a Disguise / d3 .d3export track export.

Cue beat positions come from the cue table at the tail of the track blob; the
note text and section break flag come from the individual cue records. Output
columns mirror d3's own cue table so the result can be pasted into an existing
sheet.

Usage:
    python3 d3_cue_export.py /path/to/track.d3export

Optional output path and flags:
    python3 d3_cue_export.py track.d3export cues.tsv --format tsv
    python3 d3_cue_export.py track.d3export --notes-only --tc-offset 1:00:00
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

import d3export as d3

COLUMNS = ["Beat", "Tag", "Note", "Track_Time", "TC_Time", "Section_Break"]

# d3 exposes a Tag column in its cue table, but no tag string is stored in the
# export format. The column is emitted empty so the layout still lines up.
TAG_PLACEHOLDER = ""


def parse_offset(value: str) -> float:
    """Accept either plain seconds or H:MM:SS / MM:SS."""
    if ":" not in value:
        return float(value)

    parts = [float(part) for part in value.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + part
    return seconds


def build_rows(cues, bpm: float, tc_offset: float):
    rows = []
    for cue in cues:
        seconds = d3.beats_to_seconds(cue.beat, bpm)
        rows.append(
            [
                d3.format_beat(cue.beat),
                TAG_PLACEHOLDER,
                cue.note,
                d3.format_timecode(seconds),
                d3.format_timecode(seconds + tc_offset),
                "1" if cue.section_break else "0",
            ]
        )
    return rows


def render(rows, fmt: str) -> str:
    if fmt == "txt":
        lines = []
        for beat, _tag, note, track_time, _tc, section_break in rows:
            marker = " *" if section_break == "1" else ""
            lines.append(f"{track_time} (beat {beat}){marker}: {note}")
        return "".join(line + "\n" for line in lines)

    buffer = io.StringIO()
    delimiter = "\t" if fmt == "tsv" else ","
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract the cue list from a d3 .d3export file."
    )
    parser.add_argument("source", type=Path, help="path to the .d3export file")
    parser.add_argument(
        "output", type=Path, nargs="?", help="output path (default: output/<stem>_cues.<ext>)"
    )
    parser.add_argument(
        "--format",
        choices=("tsv", "csv", "txt"),
        default="tsv",
        help="output format (default: tsv)",
    )
    parser.add_argument(
        "--bpm",
        type=float,
        default=d3.DEFAULT_BPM,
        help=f"beats per minute for time conversion (default: {d3.DEFAULT_BPM:g})",
    )
    parser.add_argument(
        "--tc-offset",
        default="0",
        help="offset added to TC_Time, as seconds or H:MM:SS (default: 0)",
    )
    parser.add_argument(
        "--notes-only",
        action="store_true",
        help="skip cues with no note text",
    )
    args = parser.parse_args()

    src = args.source.expanduser().resolve()
    if not src.exists():
        print(f"Error: file not found: {src}", file=sys.stderr)
        return 2

    if src.suffix.lower() != ".d3export":
        print("Warning: input file does not end in .d3export", file=sys.stderr)

    try:
        tc_offset = parse_offset(args.tc_offset)
    except ValueError:
        print(f"Error: could not parse --tc-offset {args.tc_offset!r}", file=sys.stderr)
        return 2

    data = d3.read_file(src)
    cues = d3.extract_cues(data)
    if not cues:
        print("Error: no cue table found in this .d3export file.", file=sys.stderr)
        return 1

    total = len(cues)
    if args.notes_only:
        cues = [cue for cue in cues if cue.note]

    rows = build_rows(cues, args.bpm, tc_offset)

    out = (
        args.output.expanduser().resolve()
        if args.output
        else d3.default_output_path(__file__, src, f"_cues.{args.format}")
    )
    out.write_text(render(rows, args.format), encoding="utf-8")

    sections = sum(1 for cue in cues if cue.section_break)
    dividers = sum(1 for cue in cues if cue.note == "/")

    print(f"Wrote {len(rows)} cue(s) to: {out}")
    print(f"Scanned {total} total cue(s); {sections} section break(s), {dividers} divider(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
