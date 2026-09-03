#!/usr/bin/env python3
"""
Extract timeline media from a Disguise / d3 .d3export track export.

Output format:
    Layer Name: objects/videofile/full/dependency/path.mov

Rows are sorted by the layer's stored timeline start position.
Repeated layers/media are preserved.

Usage:
    python3 d3_timeline_media_export.py /path/to/track.d3export

Optional output path:
    python3 d3_timeline_media_export.py /path/to/track.d3export /path/to/output.txt
"""

from pathlib import Path
import re
import struct
import sys


def to_apxr(path: str) -> str:
    return path[:-4] + ".apxr" if path.endswith(".apx") else path


def extract_media(export_path: Path):
    data = export_path.read_bytes()

    # Map embedded VideoClip resources to VideoAsset resources.
    clip_to_asset = {}
    for m in re.finditer(rb"objects/videoclip/[\x20-\x7e]+?\.apxr", data):
        clip_resource = m.group().decode("utf-8", "replace")
        section = data[m.end() : m.end() + 1500]
        a = re.search(rb"objects/videoasset/[\x20-\x7e]+?\.apx", section)
        if a:
            clip_to_asset[clip_resource] = a.group().decode("utf-8", "replace")

    # Map embedded VideoAsset resources to the actual VideoFile dependency.
    asset_to_file = {}
    for m in re.finditer(rb"objects/videoasset/[\x20-\x7e]+?\.apxr", data):
        asset_resource = m.group().decode("utf-8", "replace")
        section = data[m.end() : m.end() + 1500]
        vf = re.search(rb"objects/videofile/[\x20-\x7e]+?(?=[\x00-\x1f])", section)
        if vf:
            asset_to_file[asset_resource] = vf.group().decode("utf-8", "replace")

    def resolve_clip(track_ref: str):
        asset_ref = clip_to_asset.get(to_apxr(track_ref))
        if not asset_ref:
            return None
        return asset_to_file.get(to_apxr(asset_ref))

    # Locate timeline Layer resources.
    layer_matches = list(re.finditer(rb"Layer_UID_[0-9a-f]{16}\x00", data))
    if not layer_matches:
        raise ValueError("No timeline layers were found in this .d3export file.")

    # Dependency objects follow the track payload; first **** after final layer is a safe cap.
    track_end = data.find(b"****", layer_matches[-1].end())
    if track_end == -1:
        track_end = len(data)

    rows = []
    unresolved = []

    for layer_index, lm in enumerate(layer_matches):
        end = (
            layer_matches[layer_index + 1].start()
            if layer_index + 1 < len(layer_matches)
            else track_end
        )
        block = data[lm.start() : end]

        # Layer name follows SuperLayer\0 + a 4-byte field.
        marker = b"SuperLayer\x00"
        p = block.find(marker)
        if p < 0:
            continue

        name_start = p + len(marker) + 4
        name_end = block.find(b"\x00", name_start)
        if name_end < 0:
            continue

        layer_name = block[name_start:name_end].decode("utf-8", "replace")

        # Two little-endian doubles immediately follow the name.
        # The first is the layer's timeline start position; the second is duration.
        value_pos = name_end + 1
        if value_pos + 16 > len(block):
            continue
        start_seconds, _duration_seconds = struct.unpack_from("<dd", block, value_pos)

        # In tested Designer 32.4.7 exports, each media-bearing layer contains
        # one unique VideoClip ref. Keep this generic in case a future export has more.
        refs = []
        for m in re.finditer(rb"objects/videoclip/[\x20-\x7e]+?\.apx", block):
            ref = m.group().decode("utf-8", "replace")
            if ref not in refs:
                refs.append(ref)

        for ref_index, ref in enumerate(refs):
            media_path = resolve_clip(ref)
            if media_path:
                rows.append(
                    (start_seconds, layer_index, ref_index, layer_name, media_path)
                )
            else:
                unresolved.append((layer_name, ref))

    # Timeline order first; original serialized layer order breaks exact-time ties.
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return rows, unresolved, len(layer_matches)


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(
            "Usage: python3 d3_timeline_media_export.py <track.d3export> [output.txt]",
            file=sys.stderr,
        )
        return 2

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists():
        print(f"Error: file not found: {src}", file=sys.stderr)
        return 2

    if src.suffix.lower() != ".d3export":
        print("Warning: input file does not end in .d3export", file=sys.stderr)

    if len(sys.argv) == 3:
        out = Path(sys.argv[2]).expanduser().resolve()
    else:
        out = src.with_name(src.stem + "_media_list.txt")

    rows, unresolved, total_layers = extract_media(src)

    out.write_text(
        "".join(f"{layer}: {media}\n" for _, _, _, layer, media in rows),
        encoding="utf-8",
    )

    print(f"Wrote {len(rows)} media layer(s) to: {out}")
    print(f"Scanned {total_layers} total timeline layer(s).")
    if unresolved:
        print(f"Warning: {len(unresolved)} media reference(s) could not be resolved.")
        for layer, ref in unresolved[:10]:
            print(f"  {layer}: {ref}")
        if len(unresolved) > 10:
            print(f"  ...and {len(unresolved) - 10} more")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
