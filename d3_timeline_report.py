#!/usr/bin/env python3
"""
Build a combined cue + media report from a Disguise / d3 .d3export track export.

Cues and timeline layers share the same beat timeline, so both are emitted as
rows in one beat-ordered table. Cue rows carry the note and section break;
layer rows carry the layer name and the resolved media file. Where a layer
starts on a cue, the cue row is listed first.

Column layout mirrors d3's cue table with Layer and Asset appended.

Usage:
    python3 d3_timeline_report.py /path/to/track.d3export

Optional output path and flags:
    python3 d3_timeline_report.py track.d3export report.tsv --format tsv
    python3 d3_timeline_report.py track.d3export --all-layers --format txt
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

import d3export as d3

COLUMNS = [
    "Beat",
    "Tag",
    "Note",
    "Track_Time",
    "TC_Time",
    "Section_Break",
    "Layer",
    "Asset",
]

# d3 exposes a Tag column in its cue table, but no tag string is stored in the
# export format. The column is emitted empty so the layout still lines up.
TAG_PLACEHOLDER = ""

CUE_ROW = 0
LAYER_ROW = 1

# Every asset path carries the same resource-type prefix, so it adds nothing to
# the Asset column. Stripped here only; the media/audio exports keep full paths.
ASSET_PREFIXES = ("objects/videofile/", "objects/audiofile/")


def short_asset(asset: str) -> str:
    for prefix in ASSET_PREFIXES:
        if asset.startswith(prefix):
            return asset[len(prefix) :]
    return asset


def parse_offset(value: str) -> float:
    """Accept either plain seconds or H:MM:SS / MM:SS."""
    if ":" not in value:
        return float(value)

    parts = [float(part) for part in value.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + part
    return seconds


def layer_media(layer, video_map, audio_map):
    """Resolved media paths referenced by a layer, in reference order."""
    media = []

    for ref in d3.refs_in_block(layer.block, d3.VIDEOCLIP_REF):
        resolved = video_map.get(d3.to_apxr(ref))
        if resolved:
            media.append(resolved)

    for ref in d3.refs_in_block(layer.block, d3.AUDIOCLIP_REF):
        resolved = audio_map.get(d3.to_apxr(ref))
        if resolved and resolved not in media:
            media.append(resolved)

    for ref in d3.refs_in_block(layer.block, d3.AUDIOFILE_REF):
        if ref not in media:
            media.append(ref)

    return media


def collect_events(data: bytes, include_empty_layers: bool):
    """Cue and layer events sharing one beat timeline, ready to sort."""
    records = d3.read_records(data)
    track = d3.find_track(records)

    video_map = d3.build_video_maps(data)
    audio_map = d3.build_audio_maps(data)

    events = []

    for cue in d3.extract_cues(data):
        events.append((cue.beat, CUE_ROW, 0, 0, cue, None, None))

    layers = d3.extract_layers(data, track)
    for layer in layers:
        media = layer_media(layer, video_map, audio_map)
        if media:
            for ref_index, asset in enumerate(media):
                events.append(
                    (layer.start, LAYER_ROW, layer.index, ref_index, None, layer, asset)
                )
        elif include_empty_layers:
            events.append((layer.start, LAYER_ROW, layer.index, 0, None, layer, ""))

    events.sort(key=lambda event: event[:4])
    return events, len(layers)


def build_rows(events, bpm: float, tc_offset: float):
    rows = []
    for beat, _kind, _layer_index, _ref_index, cue, layer, asset in events:
        seconds = d3.beats_to_seconds(beat, bpm)
        rows.append(
            [
                d3.format_beat(beat),
                TAG_PLACEHOLDER,
                cue.note if cue else "",
                d3.format_timecode(seconds),
                d3.format_timecode(seconds + tc_offset),
                ("1" if cue.section_break else "0") if cue else "",
                layer.name if layer else "",
                short_asset(asset) if asset else "",
            ]
        )
    return rows


def render(rows, fmt: str) -> str:
    if fmt == "txt":
        lines = []
        for beat, _tag, note, track_time, _tc, section_break, layer, asset in rows:
            # Every row carries its own time so a layer is never read as
            # belonging to the cue printed above it.
            stamp = f"{track_time:>9}  beat {beat:<6}"
            if layer:
                body = f"{layer}: {asset}" if asset else layer
                lines.append(f"{stamp}    - {body}")
            else:
                marker = "*" if section_break == "1" else " "
                lines.append(f"{stamp} {marker}  {note}".rstrip())
        return "".join(line + "\n" for line in lines)

    buffer = io.StringIO()
    delimiter = "\t" if fmt == "tsv" else ","
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a combined cue + media report from a d3 .d3export file."
    )
    parser.add_argument("source", type=Path, help="path to the .d3export file")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="output path (default: output/<stem>_timeline_report.<ext>)",
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
        "--all-layers",
        action="store_true",
        help="include layers that carry no media",
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
    events, total_layers = collect_events(data, args.all_layers)
    if not events:
        print("Error: no cues or layers found in this .d3export file.", file=sys.stderr)
        return 1

    rows = build_rows(events, args.bpm, tc_offset)

    out = (
        args.output.expanduser().resolve()
        if args.output
        else d3.default_output_path(__file__, src, f"_timeline_report.{args.format}")
    )
    out.write_text(render(rows, args.format), encoding="utf-8")

    cue_rows = sum(1 for event in events if event[1] == CUE_ROW)
    media_rows = len(events) - cue_rows

    print(f"Wrote {len(rows)} row(s) to: {out}")
    print(f"  {cue_rows} cue(s), {media_rows} media row(s) across {total_layers} layer(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
