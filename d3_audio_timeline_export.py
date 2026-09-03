#!/usr/bin/env python3
"""
d3_audio_timeline_export.py

Extract WAV media used by Audio layers from a Disguise / d3 .d3export file.

Usage:
    python3 d3_audio_timeline_export.py "/path/to/track.d3export"

Output:
    <trackname>_audio_list.txt

Format:
    Layer Name: objects/audiofile/.../filename.wav

Entries are sorted by timeline start position.
"""

from pathlib import Path
import re
import struct
import sys


def to_apxr(path: str) -> str:
    return path[:-4] + ".apxr" if path.endswith(".apx") else path


def build_audio_maps(data: bytes):
    clip_to_file = {}

    # AudioClip -> AudioFile (direct)
    for m in re.finditer(rb'objects/audioclip/[\x20-\x7e]+?\.apxr', data):
        clip_resource = m.group().decode("utf-8", "replace")
        section = data[m.end():m.end() + 3000]

        af = re.search(
            rb'objects/audiofile/[\x20-\x7e]+?(?=[\x00-\x1f])',
            section
        )
        if af:
            clip_to_file[clip_resource] = af.group().decode("utf-8", "replace")

    # Optional AudioClip -> AudioAsset -> AudioFile chain
    clip_to_asset = {}
    for m in re.finditer(rb'objects/audioclip/[\x20-\x7e]+?\.apxr', data):
        clip_resource = m.group().decode("utf-8", "replace")
        section = data[m.end():m.end() + 3000]

        aa = re.search(rb'objects/audioasset/[\x20-\x7e]+?\.apx', section)
        if aa:
            clip_to_asset[clip_resource] = aa.group().decode("utf-8", "replace")

    asset_to_file = {}
    for m in re.finditer(rb'objects/audioasset/[\x20-\x7e]+?\.apxr', data):
        asset_resource = m.group().decode("utf-8", "replace")
        section = data[m.end():m.end() + 3000]

        af = re.search(
            rb'objects/audiofile/[\x20-\x7e]+?(?=[\x00-\x1f])',
            section
        )
        if af:
            asset_to_file[asset_resource] = af.group().decode("utf-8", "replace")

    for clip_resource, asset_ref in clip_to_asset.items():
        if clip_resource not in clip_to_file:
            media = asset_to_file.get(to_apxr(asset_ref))
            if media:
                clip_to_file[clip_resource] = media

    return clip_to_file


def parse_audio_layers(data: bytes, clip_to_file: dict):
    layer_matches = list(re.finditer(rb'Layer_UID_[0-9a-f]{16}\x00', data))
    if not layer_matches:
        return []

    track_end = data.find(b'****', layer_matches[-1].end())
    if track_end == -1:
        track_end = len(data)

    rows = []

    for i, lm in enumerate(layer_matches):
        block_end = (
            layer_matches[i + 1].start()
            if i + 1 < len(layer_matches)
            else track_end
        )
        block = data[lm.start():block_end]

        marker = b'SuperLayer\x00'
        p = block.find(marker)
        if p < 0:
            continue

        name_start = p + len(marker) + 4
        name_end = block.find(b'\x00', name_start)
        if name_end < 0:
            continue

        layer_name = block[name_start:name_end].decode("utf-8", "replace")

        value_pos = name_end + 1
        if value_pos + 16 > len(block):
            continue

        start_seconds, _duration_seconds = struct.unpack_from("<dd", block, value_pos)

        resolved = []

        # AudioClip references
        refs = []
        for m in re.finditer(rb'objects/audioclip/[\x20-\x7e]+?\.apx', block):
            ref = m.group().decode("utf-8", "replace")
            if ref not in refs:
                refs.append(ref)

        for ref in refs:
            media = clip_to_file.get(to_apxr(ref))
            if media and media.lower().endswith(".wav"):
                resolved.append(media)

        # Fallback direct AudioFile references
        for m in re.finditer(
            rb'objects/audiofile/[\x20-\x7e]+?(?=[\x00-\x1f])',
            block
        ):
            media = m.group().decode("utf-8", "replace")
            if media.lower().endswith(".wav") and media not in resolved:
                resolved.append(media)

        for media in resolved:
            rows.append((start_seconds, layer_name, media))

    rows.sort(key=lambda r: r[0])
    return rows


def main():
    if len(sys.argv) != 2:
        print('Usage: python3 d3_audio_timeline_export.py "/path/to/track.d3export"')
        sys.exit(1)

    src = Path(sys.argv[1])

    if not src.exists():
        print(f"File not found: {src}")
        sys.exit(1)

    data = src.read_bytes()
    clip_to_file = build_audio_maps(data)
    rows = parse_audio_layers(data, clip_to_file)

    out = src.with_name(src.stem + "_audio_list.txt")
    out.write_text(
        "".join(f"{layer}: {media}\n" for _, layer, media in rows),
        encoding="utf-8"
    )

    print(f"Found {len(rows)} WAV-bearing Audio layer entries.")
    print(f"Wrote: {out}")


if __name__ == "__main__":
    main()
