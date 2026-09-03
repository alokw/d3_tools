# d3_tools

Simple tools, scripts, and utilities for use with d3/Disguise media servers.

Currently these are CLI utilities that parse the binary `.d3export` track export
format written by Disguise Designer (tested against r32.4.15) and pull out
human-readable cue and media listings — the kind of thing you'd otherwise
transcribe by hand off the timeline.

## Requirements

Python 3.8+. No external dependencies; parsing is raw `bytes` / `struct` / `re`.

## Tools

### `d3_timeline_report.py` — combined cue + media report

The main tool. Cues and timeline layers share the same beat timeline, so both are
emitted as rows in one beat-ordered table. Cue rows carry the note and section
break; layer rows carry the layer name and its resolved media file. Where a layer
starts on a cue, the cue is listed first.

```
python d3_timeline_report.py input/yourshow.d3export
```

```
  0:03:00  beat 180    *  30s countdown into opening
  0:03:00  beat 180       - smpte: 9961_smpte_30/ltc-30_00595500-01150000_hap_48k.mov
  0:03:00  beat 180       - 3013_countdown_1min: 3000/3013_countdown_1min_v003.mov
  0:03:02  beat 182       currently mos
  0:03:30  beat 210       opening film (1105)
  0:03:30  beat 210       - 1105_a1_openingfilm_hapaudio: 1100/1105_a1_openingfilm_v002_hapaudio.mov
```

Covers both video and audio media. Asset paths have their `objects/videofile/` /
`objects/audiofile/` prefix stripped, since it's identical on every row.

### `d3_cue_export.py` — cue list only

Cue beat position, note text, and section break flag in timeline order. Columns
mirror d3's own cue table so output pastes straight into an existing sheet.

```
python d3_cue_export.py input/yourshow.d3export
```

```
Beat    Tag     Note                            Track_Time      TC_Time     Section_Break
0                                               0:00:00         0:00:00     1
152             /                               0:02:32         0:02:32     0
180             30s countdown into opening      0:03:00         0:03:00     1
182             currently mos                   0:03:02         0:03:02     0
```

### `d3export.py` — shared library

Binary parsing primitives: record walking, cue table, layers, media reference
chains, beat/timecode conversion. Both scripts import it. New format knowledge
belongs here rather than in the individual scripts.

## Usage

Both scripts take the source file and an optional output path:

```
python d3_timeline_report.py <track.d3export> [output]
```

With no output path, results go to `output/<stem>_timeline_report.<ext>` or
`output/<stem>_cues.<ext>` beside the scripts. The folder is created
automatically and is git-ignored.

| Option | Applies to | Description |
|---|---|---|
| `--format {tsv,csv,txt}` | both | Output format; defaults to `tsv` |
| `--bpm N` | both | Beats per minute for time conversion (default 60) |
| `--tc-offset X` | both | Offset added to `TC_Time`, as seconds or `H:MM:SS` |
| `--notes-only` | cue export | Skip cues with no note text |
| `--all-layers` | report | Include layers that carry no media |

### Columns

`Beat`, `Tag`, `Note`, `Track_Time`, `TC_Time`, `Section_Break`, and — in the
report — `Layer` and `Asset`.

`Tag` is always empty. d3's cue table shows a Tag column, but no tag string is
stored anywhere in the export format; the column is kept so the layout still
lines up with an existing sheet.

## Binary format

Byte-level documentation is in [`notes/d3export_format.md`](notes/d3export_format.md).
The essentials:

- **File structure** — 12-byte header (`72 19 04 07` + `blip` + version u32), then
  a flat sequence of `****`-framed records with 4-byte-aligned sizes.
- **Track blob** — the `objects/track/<name>.apx` record is ~92% of the file. It
  holds every layer (embedded, separated by `FF FF FF FF`) and the cue table at
  its tail.
- **Layers** — each has a SuperLayer section with name (`string\0`), start
  position (`f64` LE, beats), and duration (`f64` LE, beats).
- **Media chain** — VideoClip → VideoAsset → VideoFragment → VideoFile, mixing
  external records and embedded objects. Same pattern for Audio\*.
- **Cue table** — `[u32 count] [f64 beat, string\0 cue_path] * count`. Cue beat
  positions are stored **only** here, never in the cue records themselves.
- **Cue records** — at `internal/cue/uid_*.apx`: note text, section break flag,
  transition type, transition time. A note of `"/"` is a divider by convention,
  which is independent of the section break flag.
- **`.apx` vs `.apxr`** — record *header* paths end in `.apxr`; *references* to
  them end in `.apx`. Append the `r` before looking a reference up.
- **Timing** — beats convert at 60 BPM (1 beat = 1 second) in tested exports.
- **Byte order** — little-endian throughout; strings are null-terminated ASCII.

### Verification

Cue extraction was checked against a cue table exported from d3's own UI. Beat
positions, note text, and section break flags all matched exactly.

## Known limitations

- **Tempo is assumed, not read.** Beats map 1:1 to seconds at 60 BPM in the
  exports tested. The Track fields hold an `f64` of `1.0` that may be tempo in
  beats/second, but that's unconfirmed — hence the `--bpm` override.
- **The audio chain is only exercised by a synthetic fixture.** The sample export
  on hand has no audio layers, so AudioClip → AudioAsset → AudioFile hasn't been
  confirmed against a real file with audio.
- **Media reference lookahead can cross record boundaries.** `_lookahead_map` in
  `d3export.py` scans a fixed byte window after each record header. It resolves
  correctly on tested exports, but bounding the search to the record's own
  content payload would be more robust.
- **Only the first fragment of a multi-fragment VideoAsset is resolved.** Some
  assets carry 3+ fragments pointing at different VideoFile versions.
- **The `f64` beside a VideoClip reference is not a usable beat position.** It
  looks like one and often matches, but 47 of 125 fell outside their own layer's
  span in testing. Layer start position is used for placement instead.

## Possible future work

- Read the real tempo if the Track `f64` turns out to be it — test against a show
  running at something other than 60 BPM.
- Pull more metadata out of VideoAsset records (resolution, codec, frame count,
  sample rate) for a media spec report.
- Explore other layer module types; TextModule layers may carry on-screen text
  worth listing.
- Identify the unknown `u8` flag in cue records — set on 43 of 124 cues in the
  sample, with no matching column in d3's cue table.
- Group report output by section rather than emitting a flat list.

## Repo layout

```
d3_timeline_report.py     combined cue + media report
d3_cue_export.py          cue list
d3export.py               shared binary parsing library
notes/                    format research and documentation
input/                    local test files (git-ignored)
output/                   generated output (git-ignored)
```
