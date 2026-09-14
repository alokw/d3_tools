#!/usr/bin/env python3
"""
Shared parsing primitives for Disguise / d3 `.d3export` files.

Byte-level format documentation lives in `notes/d3export_format.md`.

The format is a 12-byte file header followed by a flat sequence of `****`
framed records. One record (`objects/track/<name>.apx`) holds the timeline:
all layers are embedded inside it, and a cue table sits at its tail.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import NamedTuple

FILE_HEADER_SIZE = 12
RECORD_MARKER = b"****"
RECORD_HEADER_SIZE = 32

# d3 stores timeline positions in beats. Every export inspected so far runs at
# 60 BPM (1 beat == 1 second); no tempo field has been located in the format,
# so callers may override it.
DEFAULT_BPM = 60.0


class Record(NamedTuple):
    """A `****` framed record. `start`/`end` bound the content payload."""

    path: str
    start: int
    end: int


class Cue(NamedTuple):
    beat: float
    note: str
    section_break: bool
    transition_type: int
    transition_time: float
    path: str


class Layer(NamedTuple):
    index: int
    name: str
    start: float
    duration: float
    block: bytes


def read_file(path) -> bytes:
    return Path(path).read_bytes()


def read_records(data: bytes):
    """Walk the `****` record chain. Records are contiguous and 4-byte aligned."""
    records = []
    offset = FILE_HEADER_SIZE

    while offset + RECORD_HEADER_SIZE <= len(data):
        if data[offset : offset + 4] != RECORD_MARKER:
            break

        total_size, content_size = struct.unpack_from("<II", data, offset + 4)
        (path_len,) = struct.unpack_from("<I", data, offset + 28)

        content_start = offset + RECORD_HEADER_SIZE + path_len
        content_end = content_start + content_size
        if total_size <= 0 or content_end > len(data):
            break

        path = data[offset + RECORD_HEADER_SIZE : content_start].decode(
            "ascii", "replace"
        )
        records.append(Record(path, content_start, content_end))
        offset += total_size

    return records


def find_track(records):
    for record in records:
        if record.path.startswith("objects/track/"):
            return record
    return None


def to_apxr(path: str) -> str:
    """References inside the track end in `.apx`; record header paths end in `.apxr`."""
    return path[:-4] + ".apxr" if path.endswith(".apx") else path


def read_cstring(data: bytes, pos: int):
    end = data.find(b"\x00", pos)
    if end < 0:
        end = len(data)
    return data[pos:end].decode("utf-8", "replace"), end + 1


# --------------------------------------------------------------------------
# Cues
# --------------------------------------------------------------------------


def parse_cue_table(data: bytes, track: Record):
    """
    Read the cue table at the tail of the track content:

        [u32 count] [f64 beat, string\\0 cue_path] * count

    This is the only place cue beat positions are stored; the individual cue
    records carry the note and flags but no timing.

    Layers can also reference cues (e.g. a TrackJumpModule's jump target), so
    the first `internal/cue` string in the track is not necessarily the table.
    Each candidate is parsed and accepted only if its entries run out exactly
    at the end of the track content, which is where the table always sits.
    """
    search_from = track.start
    while True:
        hit = data.find(b"internal/cue", search_from, track.end)
        if hit < 0:
            return []
        search_from = hit + 1

        entries = _parse_cue_table_at(data, hit, track)
        if entries is not None:
            return entries


def _parse_cue_table_at(data: bytes, first_path: int, track: Record):
    """Try to read a cue table whose first entry path starts at `first_path`."""
    # The count sits immediately before the first entry's f64 beat.
    pos = first_path - 8 - 4
    if pos < track.start:
        return None

    (count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    if count <= 0 or count > 100000:
        return None

    entries = []
    for _ in range(count):
        if pos + 8 > track.end:
            return None
        (beat,) = struct.unpack_from("<d", data, pos)
        pos += 8
        path, pos = read_cstring(data, pos)
        if not path.startswith("internal/cue/"):
            return None
        entries.append((beat, path))

    return entries if pos == track.end else None


def parse_cue_record(data: bytes, record: Record):
    """
    Parse a single `internal/cue/uid_*.apx` record.

    Layout after the `Cue\\0` type name and its u32 field count:
        string\\0 note
        u32      padding
        u8       section break flag
        u8       transition type
        3 bytes  zeros
        u8       flag (purpose unknown)
        3 bytes  zeros
        f64      transition time
    """
    blob = data[record.start : record.end]

    marker = blob.find(b"Cue\x00")
    if marker < 0:
        return None

    note, pos = read_cstring(blob, marker + 4 + 4)
    tail = blob[pos:]
    if len(tail) < 21:
        return None

    section_break = bool(tail[4])
    transition_type = tail[5]
    (transition_time,) = struct.unpack_from("<d", tail, 13)

    return note, section_break, transition_type, transition_time


def extract_cues(data: bytes):
    """Join the track's cue table (timing) with the cue records (note, flags)."""
    records = read_records(data)
    track = find_track(records)
    if track is None:
        return []

    by_path = {record.path: record for record in records}

    cues = []
    for beat, path in parse_cue_table(data, track):
        record = by_path.get(path) or by_path.get(to_apxr(path))
        if record is None:
            continue

        fields = parse_cue_record(data, record)
        if fields is None:
            continue

        note, section_break, transition_type, transition_time = fields
        cues.append(
            Cue(beat, note, section_break, transition_type, transition_time, path)
        )

    cues.sort(key=lambda cue: cue.beat)
    return cues


# --------------------------------------------------------------------------
# Layers
# --------------------------------------------------------------------------

LAYER_UID = re.compile(rb"Layer_UID_[0-9a-f]{16}\x00")
SUPERLAYER = b"SuperLayer\x00"


def extract_layers(data: bytes, track=None):
    """
    Layers are embedded in the track blob, separated by `FF FF FF FF`.

    Each carries a SuperLayer section holding the name and two f64 beat values
    (start position and duration).
    """
    matches = list(LAYER_UID.finditer(data))
    if not matches:
        return []

    if track is not None:
        end_of_layers = track.end
    else:
        end_of_layers = data.find(RECORD_MARKER, matches[-1].end())
        if end_of_layers == -1:
            end_of_layers = len(data)

    layers = []
    for index, match in enumerate(matches):
        block_end = (
            matches[index + 1].start() if index + 1 < len(matches) else end_of_layers
        )
        block = data[match.start() : block_end]

        marker = block.find(SUPERLAYER)
        if marker < 0:
            continue

        # A 4-byte field sits between the type name and the layer name.
        name, pos = read_cstring(block, marker + len(SUPERLAYER) + 4)
        if pos + 16 > len(block):
            continue

        start, duration = struct.unpack_from("<dd", block, pos)
        layers.append(Layer(index, name, start, duration, block))

    return layers


# --------------------------------------------------------------------------
# Media reference chains
# --------------------------------------------------------------------------


def _lookahead_map(data: bytes, definition: bytes, target: bytes, window: int):
    """Map each record-defining path to the first `target` path that follows it."""
    mapping = {}
    for match in re.finditer(definition, data):
        key = match.group().decode("utf-8", "replace")
        section = data[match.end() : match.end() + window]
        found = re.search(target, section)
        if found:
            mapping[key] = found.group().decode("utf-8", "replace")
    return mapping


def build_video_maps(data: bytes, window: int = 1500):
    """Resolve the VideoClip -> VideoAsset -> VideoFile chain to clip -> file."""
    clip_to_asset = _lookahead_map(
        data,
        rb"objects/videoclip/[\x20-\x7e]+?\.apxr",
        rb"objects/videoasset/[\x20-\x7e]+?\.apx",
        window,
    )
    asset_to_file = _lookahead_map(
        data,
        rb"objects/videoasset/[\x20-\x7e]+?\.apxr",
        rb"objects/videofile/[\x20-\x7e]+?(?=[\x00-\x1f])",
        window,
    )

    clip_to_file = {}
    for clip, asset in clip_to_asset.items():
        media = asset_to_file.get(to_apxr(asset))
        if media:
            clip_to_file[clip] = media
    return clip_to_file


def build_audio_maps(data: bytes, window: int = 3000):
    """Resolve AudioClip -> AudioFile, directly or via AudioAsset."""
    clip_to_file = _lookahead_map(
        data,
        rb"objects/audioclip/[\x20-\x7e]+?\.apxr",
        rb"objects/audiofile/[\x20-\x7e]+?(?=[\x00-\x1f])",
        window,
    )
    clip_to_asset = _lookahead_map(
        data,
        rb"objects/audioclip/[\x20-\x7e]+?\.apxr",
        rb"objects/audioasset/[\x20-\x7e]+?\.apx",
        window,
    )
    asset_to_file = _lookahead_map(
        data,
        rb"objects/audioasset/[\x20-\x7e]+?\.apxr",
        rb"objects/audiofile/[\x20-\x7e]+?(?=[\x00-\x1f])",
        window,
    )

    for clip, asset in clip_to_asset.items():
        if clip not in clip_to_file:
            media = asset_to_file.get(to_apxr(asset))
            if media:
                clip_to_file[clip] = media
    return clip_to_file


def refs_in_block(block: bytes, pattern: bytes):
    """Ordered, de-duplicated resource references inside a layer block."""
    refs = []
    for match in re.finditer(pattern, block):
        ref = match.group().decode("utf-8", "replace")
        if ref not in refs:
            refs.append(ref)
    return refs


VIDEOCLIP_REF = rb"objects/videoclip/[\x20-\x7e]+?\.apx"
AUDIOCLIP_REF = rb"objects/audioclip/[\x20-\x7e]+?\.apx"
AUDIOFILE_REF = rb"objects/audiofile/[\x20-\x7e]+?(?=[\x00-\x1f])"


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


def beats_to_seconds(beats: float, bpm: float = DEFAULT_BPM) -> float:
    return beats * 60.0 / bpm


def format_timecode(seconds: float) -> str:
    """Format as H:MM:SS, matching d3's cue table display."""
    total = int(round(seconds))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def format_beat(beats: float) -> str:
    """Whole beats print without a decimal point; fractional beats keep it."""
    return str(int(beats)) if beats == int(beats) else f"{beats:g}"


def default_output_path(script_file: str, source, suffix: str) -> Path:
    """Default outputs land in `output/` beside the scripts."""
    output_dir = Path(script_file).parent / "output"
    output_dir.mkdir(exist_ok=True)
    return output_dir / (Path(source).stem + suffix)


if __name__ == "__main__":
    import sys

    print(
        "d3export.py is a library, not a tool. Run one of these instead:\n"
        "    python d3_timeline_report.py <track.d3export>\n"
        "    python d3_cue_export.py <track.d3export>",
        file=sys.stderr,
    )
    raise SystemExit(2)
