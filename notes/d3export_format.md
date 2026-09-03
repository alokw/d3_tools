# Disguise d3export Binary Format

Reference documentation for the `.d3export` track export format used by Disguise/d3.

**Analyzed with:** d3 r32.4.15, build 258231 (exported Jul-30-2026)

---

## File Header (12 bytes)

| Offset | Size | Type   | Value          | Description               |
|--------|------|--------|----------------|---------------------------|
| 0      | 4    | bytes  | `72 19 04 07`  | Type tag (reused for all serialized objects) |
| 4      | 4    | ASCII  | `blip`         | Magic identifier          |
| 8      | 4    | u32 LE | 3              | Format version            |

---

## Record Framing (`****` records)

Everything after the 12-byte header is a sequence of `****` records. Records chain contiguously — each record's offset + total_size equals the next record's offset.

**Record header (32 + path_len bytes):**

| Offset | Size     | Type   | Description                         |
|--------|----------|--------|-------------------------------------|
| +0     | 4        | ASCII  | `****` marker                       |
| +4     | 4        | u32 LE | Total record size (marker to next marker) |
| +8     | 4        | u32 LE | Content payload size                |
| +12    | 8        | bytes  | Reserved (always zero)              |
| +20    | 4        | u32 LE | Counter / sequence number           |
| +24    | 4        | u32 LE | Version / build identifier (constant across all records) |
| +28    | 4        | u32 LE | Path string length                  |
| +32    | path_len | ASCII  | Resource path (no null terminator)  |

After the header comes the content payload, then 0–3 bytes of `#` (0x23) padding for 4-byte alignment.

---

## Object Serialization

All binary payloads (except `conf/depends.txt`) use a common recursive serialization format with type inheritance.

### Top-level objects (stored as record content)

```
[4 bytes] type tag: 72 19 04 07
[4 bytes] u32 LE: serialization version (always 1)
[string\0] UID (e.g. "Track_UID_d41bd2fd9b6390e2")
```

### Embedded objects (e.g. layers within track, fragments within assets)

Same as above but **without** the type tag and version prefix — starts directly at the UID.

### Resource base (common to all objects)

Every object inherits from Resource:

```
[string\0] "Resource"
[u32 LE]   field count = 9
[25 bytes]  Resource fields:
    [u32 LE] data size (file size for VideoFile/Mesh/DxTexture; 0 for others)
    [u32 LE] reserved (0)
    [u32 LE] reserved (0)
    [u8]     flag
    [u32 LE] version / build number
    [u32 LE] content hash
    [u32 LE] flag (0 or 1)
```

After the Resource base, type-specific sections follow in an inheritance chain. Each declares:

```
[string\0] type name (e.g. "SuperLayer", "Layer", "Cue")
[u32 LE]   field count
[...]      type-specific field data
```

### Null references

Null resource references are encoded as `null\0` (5 bytes ASCII).

### Resource path suffixes: `.apx` vs `.apxr`

This distinction matters for every lookup and is easy to miss:

- **`.apxr`** — the path in a record's **header**. This is the definition site of the object.
- **`.apx`** — how other objects **reference** that record (e.g. a layer pointing at a VideoClip).

So a reference read out of a layer must have `r` appended before it will match a record header path. In `d3export.py` this is `to_apxr()`.

In the analyzed file: 88 `objects/videoclip/….apxr` record headers, and 125 `objects/videoclip/….apx` references inside the track (several layers reuse the same clip).

---

## Type Hierarchies

| UID prefix          | Path prefix                      | Hierarchy                                    |
|---------------------|----------------------------------|----------------------------------------------|
| Track_UID_          | objects/track                    | Resource → SuperTrack(3) → Track(64)         |
| Layer_UID_          | *(embedded in track)*            | Resource → SuperLayer(7) → Layer(15)         |
| VideoClip_UID_      | objects/videoclip                | Resource → VideoClip(50)                     |
| VideoAsset_UID_     | objects/videoasset               | Resource → VideoAsset(3)                     |
| VideoFile_UID_      | internal/videofile               | Resource → VideoFile(6)                      |
| VideoFragment_UID_  | *(embedded in VideoAsset)*       | Resource → VideoFragment(4)                  |
| Cue_UID_            | internal/cue                     | Resource → Cue(3)                            |
| DxTexture_UID_      | internal/dxtexture               | Resource → DxTexture(5)                      |
| MeshFromObj_UID_    | internal/mesh                    | Resource → Node(1)                           |
| Mesh_UID_           | objects/mesh                     | Resource → Node(1)                           |
| FeedProjection_UID_ | objects/feedprojection           | Resource → Projection(16)                    |
| DirectProjection_UID_ | objects/directprojection       | Resource → Projection(16)                    |
| LedScreen_UID_      | objects/ledscreen                | Resource → Object(10)                        |
| ScreenConfiguration_UID_ | objects/screenconfiguration | Resource → ScreenConfiguration(11)           |
| LogicalAudioOutDevice_UID_ | objects/logicalaudiooutdevice | Resource → LogicalDevice(1) → LogicalAudioOutDevice(5) |
| TextFont_UID_       | objects/text                     | Resource → TextFont(1)                       |
| ColourProfile_UID_  | objects/colourprofile            | Resource → ColourProfile                     |

---

## Track Object

The track record (`objects/track/<name>.apx`) is the main blob, typically ~92% of the file.

### Track header

```
[type tag + version]
[string\0] Track_UID_...
[Resource base]
[u32 LE]   unknown flag/version
[string\0] show/track name (e.g. "Set List")
[string\0] "SuperTrack"
[u32 LE]   field count = 3
[u32 LE]   layer count
```

### Embedded layers

Layer objects follow immediately, separated by `FF FF FF FF` markers between each pair.

### Track fields (after all layers)

```
[string\0] "Track"
[u32 LE]   field count = 64
[56 bytes]  Track-specific fields:
    [16 bytes] zeros
    [string\0] empty string
    [string\0] "null" (null reference)
    [u32 LE]   hash/counter
    [u32 LE]   (2)
    [u32 LE]   (1)
    [f64 LE]   1.0 (possibly tempo/playback speed)
    [string\0] "null" (null reference)
    [9 bytes]  zeros
```

### Cue table (at end of track content)

```
[u32 LE]   cue count
repeated for each cue:
    [f64 LE]   timeline position (beats)
    [string\0] path to cue record (e.g. "internal/cue/uid_XXXX.apx")
```

The cue table stores timeline positions alongside cue paths — this is the only place cue timing appears (not in the individual cue records themselves).

---

## Layer Records

Embedded within the Track blob. Each layer uses the embedded object format (no type tag prefix).

### SuperLayer fields (field count = 7)

```
[string\0] layer name (human-readable)
[f64 LE]   start position (beats)
[f64 LE]   duration (beats)
[f64 LE]   third double (observed as 0.0)
[u8]       layer enable flag
```

### Layer fields (field count = 15)

```
[u32 LE]   module-related
[u32 LE]   module version/ID
[u32 LE]   module-related
[string\0] module name
[...]      module-specific nested objects (FieldSequence, FloatSequence, etc.)
```

### Layer module types observed

| Module                  | Count |
|-------------------------|-------|
| ColourShift             | 125   |
| TextModule              | 13    |
| FadeModule              | 4     |
| ScreenPositionModule    | 1     |
| ProjectionAwareModule   | 1     |

Layers with media content use ColourShift modules that contain ResourceSequence objects referencing VideoClip paths.

### Media reference sites inside a layer

Clip references sit inside keyframe sequence structures, laid out as:

```
"Sequence\0" [u32 field count] [u32]
"Key\0"      [u32] [u32]
[f64 LE]     keyframe value
[00 00 01]
[string\0]   objects/videoclip/….apx
```

**Caution:** the f64 immediately preceding the clip path looks like a beat position and matches the layer start in many cases, but it is *not* reliably the clip's absolute timeline position — in the analyzed file 47 of 125 references fall outside their own layer's start/duration span, with inconsistent offsets. Use the **layer's** SuperLayer start position for placement instead; that value is verified and lines up exactly with cue beats.

---

## VideoClip → VideoAsset → VideoFile Chain

### VideoClip (external records)

- Path: `objects/videoclip/<name>.apx`
- Hierarchy: Resource → VideoClip(fc=50)
- Contains ~50 fields including flags, f32 parameters, and path references to:
  - A VideoAsset (`objects/videoasset/<name>.apx`)
  - A ColourProfile (`objects/colourprofile/srgb.apx`)

### VideoAsset (external records)

- Path: `objects/videoasset/<name>.apx`
- Hierarchy: Resource → VideoAsset(fc=3)

```
[u32 LE]   total frame count
[f32 LE]   framerate (e.g. 30.0)
[f32 LE]   width
[f32 LE]   height
[u8]       flag
[u8]       flag
[string\0] codec name ("hap", "NotchLC", "still_image:PNG")
[u32 LE]   audio channel count/config
[f32 LE]   audio sample rate (e.g. 48000.0)
[u32 LE]   fragment count
[...]      embedded VideoFragment objects
[string\0] "null" (terminator)
```

### VideoFragment (embedded in VideoAsset)

- Hierarchy: Resource → VideoFragment(fc=4)

```
[u32 LE]   start frame
[u32 LE]   end frame
[string\0] version/variant suffix (e.g. "0", "001n", "006")
[6 bytes]  flags
[string\0] VideoFile path (e.g. "objects/videofile/<name>.mov")
```

Multiple fragments can chain within a single VideoAsset (e.g. multi-part media).

### VideoFile (external records)

- Path: `internal/videofile/<name>.apx`
- Hierarchy: Resource → VideoFile(fc=6)

```
[5x u32 LE] boolean flags
[u32 LE]    width
[u32 LE]    height
[u32 LE]    bits per pixel
[string\0]  codec name
[u8]        flag
[u32 LE]    width (duplicate)
[u32 LE]    height (duplicate)
[i32 LE]    secondary BPP (-1 for hap)
[f32 LE]    framerate
[u32 LE]    total frame count
[u8]        flag
[u8]        flag
[f32 LE]    audio sample rate
[u32 LE]    build version
[u32 LE]    content hash
[u32 LE]    audio config
```

---

## AudioClip → AudioAsset → AudioFile Chain

Same structural pattern as video, but with `audioclip`, `audioasset`, `audiofile` path prefixes. Uses `.wav` file extensions. Not all exports contain audio objects — depends on whether the track has audio layers.

---

## Cue Records

Stored as individual `****` records at `internal/cue/uid_XXXXXXXXXXXXXXXX.apx`.

### Cue fields (field count = 3)

```
[string\0] note text (empty string for no note)
[u32 LE]   zero padding
[u8]       section break flag (0 or 1)
[u8]       transition type (observed: always 2)
[3 bytes]  zeros
[u8]       flag (0 or 1)
[3 bytes]  zeros
[f64 LE]   transition time (0.0, 1.5, or 2.0 seconds)
[string\0] "null" (null reference)
[9 bytes]  zeros
```

**Important:** Cue timeline positions are NOT stored in cue records. They are stored in the Track object's cue table (see above), where each entry pairs an f64 beat position with the cue's resource path.

### Section breaks and dividers

These are two independent things:

- **Section break flag** — the `u8` after the zero padding. This is the `Section_Break` column in d3's own cue table. 84 of 124 cues have it set in the analyzed file.
- **`"/"` dividers** — a *convention* of writing `/` as the note text to mark a visual divider. Only 3 cues in the analyzed file. A divider cue does **not** necessarily have the section break flag set (the ones here have it clear).

### Verification

The cue extraction was checked against a cue table exported from d3's own UI. All fields matched exactly across the sample rows — beat positions, note text, and the section break flag:

| Beat | Note                        | Section_Break |
|------|-----------------------------|---------------|
| 0    | *(empty)*                   | 1             |
| 30   | *(empty)*                   | 1             |
| 60   | *(empty)*                   | 1             |
| 152  | `/`                         | 0             |
| 180  | 30s countdown into opening  | 1             |
| 182  | currently mos               | 0             |
| 210  | opening film (1105)         | 0             |
| 288  | `/`                         | 0             |
| 330  | evan title (1205)           | 1             |

The `u8` at offset 9 of the cue tail is set on 43 of 124 cues; its meaning is unknown and it does not correspond to any column in the d3 cue table.

---

## Overall File Layout

```
Byte 0:           File header (12 bytes)
Byte 12:          **** conf/depends.txt
                      Plain ASCII: "d3 r<version> <build> <hash> <date>"
Byte ~144:        **** objects/track/<name>.apx (the main blob, ~92% of file)
                      Layers, animation data, cue table
Byte ~2.1M:       **** internal/dxtexture/...
                  **** objects/feedprojection/...
                  **** objects/screen2/...
                  **** internal/mesh/...
                  **** objects/colourprofile/...
                  **** objects/directprojection/...
                  **** objects/ledscreen/...
                  **** objects/screenconfiguration/...
                  **** objects/logicalaudiooutdevice/...
                  **** objects/text/...
                  **** objects/mesh/...
                  **** objects/videoclip/...
                  **** objects/videoasset/...
                  **** internal/videofile/...
                  **** internal/cue/...
EOF
```

---

## Key Observations

- **Byte order:** Little-endian throughout
- **Strings:** Null-terminated ASCII
- **Alignment:** Record sizes are 4-byte aligned (padded with `#` = 0x23)
- **Object identity:** Every object has a typed UID (e.g. `Track_UID_`, `Layer_UID_`, `Cue_UID_`)
- **Cross-references:** Objects reference each other via resource paths (e.g. `objects/videoasset/name.apx`)
- **Timing units:** Timeline positions and durations are in **beats** (f64), not seconds or timecode
- **Embedded vs. external:** Layers and fragments are embedded within their parent objects; clips, assets, files, and cues are separate `****` records

## Tempo and timecode

Beat positions convert to wall-clock time at **60 BPM (1 beat = 1 second)** in the analyzed file — beat 152 shows as `0:02:32` in d3's cue table, beat 180 as `0:03:00`.

No tempo/BPM field has been located in the format. The Track fields contain an `f64` of `1.0` that may be a tempo or playback-rate value (1.0 beats/second would be consistent with 60 BPM), but this is unconfirmed against a second export. The scripts therefore default to 60 BPM and expose a `--bpm` override.

The `TC_Time` column in d3's cue table is identical to `Track_Time` in this export; no separate timecode offset is stored, so the tools expose a `--tc-offset` flag instead.

## Not stored in the export

- **Cue `Tag` column** — d3's cue table has a Tag column, but no tag string appears anywhere in the format. The tools emit the column empty so the layout still lines up with an existing sheet.
- **Absolute timecode / show start time** — see above.
