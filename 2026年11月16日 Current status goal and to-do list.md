# 2026-11-16 Current status, goal, and to-do list

## 1. Why this document exists

This document is a checkpoint for the project **Diary conversion from Numbers to Text and Image Files** as of 2026-11-16.

The purpose is to make the project understandable months later without having to reconstruct the design from chat history or Git commit messages. It records:

- the final goal of the project;
- what the current parser actually does today;
- which parts of the current exported data are permanent diary data and which parts exist only to inspect the migration from Numbers;
- the newly verified facts about embedded image metadata;
- a newly discovered inspection-pipeline bug/architectural gap;
- the decisions made on 2026-11-16;
- the remaining implementation and validation work.

This is a **status/design document**, not a claim that every item below has already been implemented. Sections explicitly distinguish **implemented**, **verified by research**, **decided but not yet implemented**, and **future work**.

---

## 2. Final goal

The long-term goal is **not** to build a permanent HTML clone of Apple Numbers.

The goal is to convert the Numbers diary into a **complete, durable, portable diary archive that is independent of Numbers and independent of the temporary inspection viewer**.

Eventually this archive should be usable by a future iOS diary application or any other application without requiring:

- Apple Numbers;
- `numbers-parser`;
- the original `.numbers` files;
- the temporary HTML inspection viewer;
- the original Numbers table geometry.

The archive must preserve everything that is needed to understand the diary semantically, including both explicit information and implicit information.

### 2.1 Explicit diary information

Examples include:

- diary date;
- record time;
- text;
- images;
- hyperlinks and their destinations;
- intentional empty logical fields when they carry record structure;
- the association/order between time, text, images, and other logical items.

### 2.2 Implicit diary information

The Numbers formatting is not merely cosmetic. Some formatting was intentionally used as part of the diary's language and can therefore carry semantic meaning.

Examples include:

- background color;
- font color;
- font family;
- font size;
- bold;
- italic;
- underline;
- strikethrough.

Examples of user-defined meaning already known include:

- light/green color families can indicate health-related records;
- gray can indicate recordings;
- dark colors, including black background with white text, can indicate bad/negative events;
- yellow can indicate delivery-related records;
- bold can indicate emphasis and may later become a higher-level semantic concept such as `important`;
- strikethrough should remain visible if a future diary application chooses to reproduce it directly.

There are additional color meanings that have not yet been formally cataloged. The raw formatting must therefore be preserved first; higher-level semantic interpretation can be added later without losing the original evidence.

### 2.3 Raw properties first; interpreted semantics later

The archive should preserve raw source properties even before all of their meanings have been formally defined.

For example:

```text
background=#d9ead3
bold=1
font_size=12
strike=0
```

A future semantic-analysis/import step may additionally interpret these as something such as:

```text
category=health
importance=important
```

The interpreted semantic labels should not replace the raw properties. The raw properties remain the durable evidence of what was actually present in the Numbers diary.

---

## 3. Repository state at this checkpoint

The uploaded repository snapshot is on:

```text
branch: main
HEAD:   10aa90b Fix Numbers header semantics in inspection viewer
origin/main: same commit
```

Tracked production files are clean in the uploaded snapshot.

Untracked files in that snapshot are:

```text
.DS_Store
Numbers_Image_Metadata_Research.ipynb
viewer/.DS_Store
```

The image-metadata research notebook is intentionally worth keeping, but as of this snapshot it has **not yet been committed**.

The `.DS_Store` files are unrelated Finder artifacts and should not become part of the project history.

Important architectural commits leading to the current design include:

```text
8e4065a  Refactor Numbers export to dual CSV with property-driven inspection viewer
42f927a  Use canonical properties for row-height reconstruction
93e6f17  Batch parse diary years with static inspection HTML
1f03c53  Make single-day diary export transactional
10aa90b  Fix Numbers header semantics in inspection viewer
```

---

## 4. Current intended output hierarchy

The current project design is:

```text
Diary Export/
├── data/
│   └── <year>/
│       └── <Month>/
│           └── <Month day>/
│               ├── <Month day>.csv
│               ├── <Month day>.properties.csv
│               └── IMG/
│
└── _inspection/
    └── <year>/
        └── <Month>/
            └── <Month day>/
                └── <Month day>.html
```

The conceptual boundary is now explicit:

### `Diary Export/data/`

This is the **permanent diary archive**.

Everything required to reconstruct the diary **semantically** must ultimately exist here.

### `Diary Export/_inspection/`

This is **temporary migration-validation output**.

Its purpose is to reconstruct enough of the original Numbers appearance/structure to let a human visually compare:

```text
original Numbers diary
vs.
parsed data reconstructed into HTML
```

Anything required only for this comparison may live under `_inspection/` and may be discarded after validation.

A future iOS diary app must not depend on `_inspection/`.

---

## 5. Current canonical dual-CSV design

The existing two-CSV design should be retained. A third canonical `layout.csv` is **not currently justified** merely to separate two layout-oriented properties.

### 5.1 `<day>.csv`

Contains logical diary values such as:

- time;
- text;
- image filename;
- intentional empty logical fields.

Example:

```csv
15:49,pasted-image-112.jpeg,今天去吃飯
```

### 5.2 `<day>.properties.csv`

Has the **same logical row/column shape** as `<day>.csv`.

Each CSV field contains the properties of the corresponding logical item.

The outer format is CSV. Inside each individual CSV field, the project uses a small property mini-format:

```text
key=value;key=value;key=value
```

Therefore semicolons are **not** the CSV delimiter. They separate multiple properties inside one CSV cell.

Example corresponding to the data row above:

```csv
type=datetime;span=1,type=image;span=2,type=text;span=3;font_size=12;background=#d9ead3
```

Conceptually:

```text
Day.csv cell 1:
    15:49

Day.properties.csv cell 1:
    type=datetime
    span=1

Day.csv cell 2:
    pasted-image-112.jpeg

Day.properties.csv cell 2:
    type=image
    span=2

Day.csv cell 3:
    今天去吃飯

Day.properties.csv cell 3:
    type=text
    span=3
    font_size=12
    background=#d9ead3
```

The production code already has paired `_serialize_properties()` and `_parse_properties()` functions for this format.

---

## 6. Which existing properties belong to permanent diary data?

The current `.properties.csv` intentionally contains a mixture of properties. That is acceptable as long as the distinction is documented.

### 6.1 Semantic/intrinsic properties that must be preserved

These can matter to a future diary application even if that application does not reproduce the Numbers layout exactly:

```text
type
image_file
background
font_color
font_size
font_name
bold
italic
underline
strike
linkN_text
linkN_url
```

Reasons:

- `type` distinguishes logical item kinds such as text, datetime, image, or text+image.
- `image_file` associates a logical item with its media asset when needed.
- formatting properties can encode user-defined diary meaning and emphasis.
- hyperlinks are explicit content and must remain usable outside Numbers.

### 6.2 Source-layout properties that are mainly for migration inspection

Currently:

```text
span
row_height
```

These preserve aspects of the original Numbers table geometry.

A future iOS diary application can normally ignore them and choose its own layout.

They are only two fields and they already fit the existing per-cell properties format. There is no strong reason at this stage to create a third permanent CSV only to move them elsewhere.

The important action is to **document their role clearly**, so future code does not mistake them for required diary-domain semantics.

---

## 7. Image extraction: newly verified facts

A dedicated research notebook now exists:

```text
Numbers_Image_Metadata_Research.ipynb
```

Its purpose is to establish what Numbers and `numbers-parser` actually preserve in pasted images before changing the production parser.

### 7.1 Byte preservation is verified

For the controlled test snapshot, SHA-256 comparison showed an exact match between:

```text
numbers-parser BackgroundImage.data
```

and the corresponding raw image assets stored directly inside:

```text
<file>.numbers / Data/*
```

The current experiment produced:

```text
numbers-parser images: 121
raw Data/ image SHA values: 121
EXACT SHA-256 matches: 121 / 121
```

These counts belong to that particular test-file snapshot and should not be treated as a permanent project invariant. The general conclusion is the important part:

> `BackgroundImage.data` can be written directly and preserves the raw embedded image bytes without re-encoding.

This validates the existing production approach of writing `bg_image.data` directly to `IMG/`.

### 7.2 Embedded image datetime often survives Numbers

Direct in-memory inspection of raw image bytes established that many pasted images retain embedded EXIF capture metadata, including `DateTimeOriginal` and, in many camera images, camera/lens information and timezone offsets.

Some image assets do not contain a trustworthy embedded capture datetime. In particular, some HEIC assets in the controlled test contained only minimal EXIF and no `DateTimeOriginal`.

Therefore:

- embedded capture time should be used when actually present;
- no timestamp should be invented when reliable capture metadata is absent;
- ZIP member timestamps must **not** automatically be interpreted as image capture timestamps;
- filesystem creation/modification timestamps created during export must **not** be mistaken for original capture metadata.

---

## 8. Proposed permanent image datetime representation

This is a design decision made on 2026-11-16 but is **not yet implemented in the production parser**.

For an image logical item, add the following properties to the corresponding field in `<day>.properties.csv` when a trustworthy embedded capture datetime exists:

```text
image_datetime=2026-11-15T15:49:23+08:00
image_datetime_source=DateTimeOriginal
```

A complete property field could therefore look like:

```text
type=image;span=2;image_datetime=2026-11-15T15:49:23+08:00;image_datetime_source=DateTimeOriginal
```

The matching `<day>.csv` cell remains simply:

```text
pasted-image-112.jpeg
```

### 8.1 Meaning of the two proposed keys

`image_datetime` is the normalized capture datetime exposed to future consumers of the diary archive.

`image_datetime_source` records provenance: which embedded field supplied that value.

Expected source values may include:

```text
DateTimeOriginal
DateTimeDigitized
DateTime
```

`DateTimeOriginal` should normally have highest priority.

If there is no trustworthy embedded capture datetime, both keys should be omitted rather than filled from an unproven timestamp source.

### 8.2 Do not encode datetime into filenames

The permanent filename should not be changed merely to carry the datetime.

Reasons include:

- preserving the original image/resource naming as closely as practical;
- multiple images can share the same second;
- timezone/provenance do not fit cleanly in filenames;
- filenames should not become a second metadata schema;
- the original image bytes are already preserved exactly.

### 8.3 Filesystem modification time may be a convenience mirror

It is reasonable to optionally set the exported image file's filesystem modification time (`mtime`) to the embedded capture datetime for Finder/file-manager convenience.

If implemented, this must be treated only as a **convenience mirror**.

The authoritative extracted value remains `image_datetime` in `.properties.csv`, while the original embedded EXIF remains untouched inside the image bytes.

---

## 9. Inspection viewer: current purpose

The HTML viewer exists to answer one question:

> Did the parser preserve the Numbers diary correctly enough that a human can visually detect missing or mis-associated content?

It is not the final diary application.

It is allowed to use Numbers-specific geometry that a future diary app will ignore.

The current viewer uses, among other things:

- `span`;
- explicit `row_height`;
- font/background properties;
- source header-row/header-column information;
- mapping from original Numbers row numbers to canonical record rows.

The current HTML is a static, directly openable file. The older Flask server and permanent viewer JSON sidecar were deliberately removed from the current architecture.

---

## 10. Newly discovered inspection-pipeline architectural gap

A real gap was discovered on 2026-11-16.

The project documentation and function naming suggest that inspection HTML is reconstructed from the persisted dual CSV. In practice, the current production parser still passes additional state directly through memory during the same parse operation.

### 10.1 `inspection_source_rows` exists only in RAM

During parsing, the code builds a list that maps original Numbers rows to canonical record rows.

Example:

```text
Numbers row 1 -> canonical record 0
Numbers row 2 -> canonical record 1
Numbers row 3 -> no canonical record (completely blank source row)
Numbers row 4 -> canonical record 2
```

A blank source row does **not** stop parsing. The parser continues to later rows.

This mapping exists so the inspection HTML can reproduce blank Numbers rows for visual comparison.

It is not diary-domain information and is not required by a future iOS diary app.

However, it is currently passed directly from the Numbers parse to HTML generation without first being persisted.

### 10.2 Header-row/header-column metadata is also passed in memory

The current viewer receives:

```text
num_header_rows
num_header_cols
```

from the Numbers parsing stage.

These are required to reproduce the Numbers table header semantics in the inspection HTML but are not currently part of the permanent diary data.

### 10.3 Viewer display constants are not data

The following values are currently hard-coded rendering policy:

```text
PROPERTY_TIME_COLUMN_WIDTH = 64.0
PROPERTY_BASIC_COLUMN_WIDTH = 120.0
NUMBERS_SOURCE_BASIC_COLUMN_WIDTH = 98.0
```

These should remain code/viewer constants rather than becoming diary data.

Changing the appearance of the inspection viewer should not require rewriting the diary archive.

---

## 11. Decision: strict two-stage inspection validation

The inspection pipeline should be refactored so that it cannot receive hidden in-memory information from the parse stage.

### Stage A — parse Numbers and write artifacts to disk

The parser reads the `.numbers` file and writes:

```text
Diary Export/data/...
Diary Export/_inspection/... temporary inspection metadata
```

Then Stage A ends.

### Stage B — independently reconstruct inspection HTML from disk

A separate entry point, preferably runnable in a fresh Python process or separate notebook, reads only files that Stage A actually wrote to disk and generates the static HTML.

Stage B must not receive:

- a live `Document` object;
- Numbers cells;
- Python lists retained from Stage A;
- hidden function arguments containing data that was never persisted.

This provides a much stronger validation guarantee:

> If the reconstructed inspection HTML looks correct, the information needed to reconstruct it genuinely existed in persisted export artifacts rather than being silently carried through RAM.

---

## 12. Temporary inspection sidecar decision

Information required only for migration inspection should live under `_inspection/`, not under permanent `data/`.

A small JSON sidecar is acceptable for this purpose.

This is **not** a return to the old viewer-specific JSON architecture that duplicated the diary content. The new sidecar should contain only inspection/source-structure data that does not belong to the semantic diary archive.

A possible structure is:

```json
{
  "num_header_rows": 1,
  "num_header_cols": 1,
  "source_rows": [
    {"numbers_row": 1, "record_index": 0},
    {"numbers_row": 2, "record_index": 1},
    {"numbers_row": 3, "record_index": null},
    {"numbers_row": 4, "record_index": 2}
  ]
}
```

The exact filename can be decided during implementation, for example:

```text
<day>.inspection.json
```

This sidecar may be deleted together with the rest of `_inspection/` without losing the diary itself.

If exact Numbers-like migration inspection is needed again after deleting `_inspection/`, the source `.numbers` file can be reparsed to recreate the temporary inspection metadata.

---

## 13. Image datetime in the inspection viewer

After image datetime is persisted in `.properties.csv`, the inspection viewer should **not independently re-read EXIF** to discover the datetime again.

Instead:

```text
.properties.csv
    image_datetime
        ↓
inspection builder
        ↓
HTML overlay
```

The HTML can display a camera-style timestamp such as:

```text
2026/11/15 14:25:10
```

in the lower-right corner of the rendered image.

This is important because the viewer should validate the **persisted canonical export**, not bypass it by reading information directly from the image again.

The timestamp overlay must be HTML/CSS presentation only. It must not be burned into the image pixels.

---

## 14. Permanent data-format documentation requirement

The existing README is useful as a project overview, but it is not sufficient as the long-term archive specification.

A dedicated specification should be added to the repository, for example:

```text
DIARY_EXPORT_DATA_FORMAT.md
```

That document should explain, independently of the parser implementation:

- directory hierarchy under `Diary Export/data/`;
- relationship between `<day>.csv` and `<day>.properties.csv`;
- the property-cell mini-format (`key=value;key=value`);
- every currently supported property key;
- which properties are semantic/intrinsic and which are source-layout preservation;
- image filename semantics;
- image datetime semantics and provenance;
- hyperlink representation;
- intentional empty fields;
- how future consumers should ignore unknown properties rather than fail;
- which information belongs only to `_inspection/` and is disposable;
- how to reconstruct the semantic diary without the inspection viewer.

A core invariant should be written prominently:

> **Everything required to reconstruct the diary semantically must exist under `Diary Export/data/`. `_inspection/` is disposable migration-validation output and is not part of the diary archive.**

---

## 15. Current implementation status versus final goal

### Already implemented in production

- single-file parsing through `parse_numbers_file()`;
- visible year -> month -> file orchestration in the notebook;
- extraction of diary date from the Numbers diary;
- dual-CSV representation;
- image extraction to `IMG/`;
- direct writing of `BackgroundImage.data` without intentional re-encoding;
- text extraction, including defensive handling of rich-text representations;
- hyperlink extraction and preservation;
- background/font/font-size/font-style preservation;
- horizontal merge/span preservation;
- intentional empty logical fields before the last meaningful item;
- explicit non-default row-height preservation;
- source header-row/header-column reading for inspection;
- static HTML inspection output;
- transactional per-day replacement using staging and rollback;
- current production environment using Python 3.12 / `numbers-parser` 4.19.0 in the established parser environment.

### Verified by research but not yet integrated into production

- `numbers-parser BackgroundImage.data` is byte-identical to raw Numbers image assets in the controlled test;
- embedded image capture datetime often survives Numbers;
- reliable image datetime can be extracted directly from the image bytes with Pillow / `pillow-heif`;
- some image assets have no trustworthy embedded capture datetime and must remain without one.

### Decided on 2026-11-16 but not yet implemented

- keep the existing dual-CSV canonical design;
- do not create a third permanent `layout.csv` merely for `span` and `row_height`;
- document source-layout properties explicitly;
- add `image_datetime` and `image_datetime_source` to image property cells;
- do not encode datetime into filenames;
- optionally mirror capture datetime into image filesystem `mtime` for convenience only;
- treat `_inspection/` as fully disposable;
- persist temporary inspection-only source structure under `_inspection/`;
- split parsing and HTML reconstruction into genuinely independent disk-to-disk stages;
- build inspection HTML from the persisted export rather than hidden in-memory state;
- add a dedicated permanent data-format specification.

### Future semantic work, intentionally not implemented yet

- catalog the actual diary background/font color vocabulary;
- map raw colors to higher-level meanings where appropriate;
- decide which formatting cues become explicit semantic tags in the future diary application;
- decide how the future iOS app displays emphasis, importance, strikethrough, categories, and other interpreted semantics;
- preserve raw properties even after higher-level semantic tags are introduced.

---

## 16. To-do list in recommended order

### A. Preserve the research checkpoint first

Commit:

```text
Numbers_Image_Metadata_Research.ipynb
```

with a generalized commit message that describes the method and conclusion rather than hard-coding one test file's mutable image counts.

Suggested commit title:

```text
Add Numbers image metadata preservation research
```

The commit should explain that the notebook verifies byte preservation and establishes a reproducible method for reading embedded image capture metadata directly from `BackgroundImage.data`.

Do not include `.DS_Store` files in this commit.

### B. Add this status/design checkpoint document

Commit this file separately or together with the permanent data-format specification when that specification is created.

This document is a dated checkpoint; it should remain historical even after some to-do items are completed.

### C. Add permanent `DIARY_EXPORT_DATA_FORMAT.md`

Create the stable schema/reference guide described above.

This should become the authoritative answer to questions such as:

- What does `.properties.csv` mean?
- Why is `span` there?
- Which properties must a future app preserve?
- What can a consumer safely ignore?
- What is inspection-only?

### D. Integrate image metadata extraction into `Numbers_Diary_Parser_v1.py`

Add a small reusable helper that receives the exact image bytes and returns a normalized capture datetime plus provenance when available.

Suggested priority:

```text
DateTimeOriginal
DateTimeDigitized
DateTime
```

Preserve timezone offsets when available.

HEIC/HEIF support should use the already tested Pillow + `pillow-heif` approach.

### E. Persist image datetime in `.properties.csv`

For images with trustworthy embedded capture time, write for example:

```text
image_datetime=2026-11-15T15:49:23+08:00
image_datetime_source=DateTimeOriginal
```

For images without trustworthy embedded capture time, omit these properties.

### F. Decide and implement optional filesystem `mtime` mirroring

If desired, set exported image modification time to the same capture datetime after writing the original bytes.

Document clearly that filesystem `mtime` is a convenience copy, not canonical metadata.

### G. Persist inspection-only source structure under `_inspection/`

Write at least:

```text
num_header_rows
num_header_cols
source_rows mapping
```

to a small inspection sidecar.

Do not persist viewer display-width constants as diary data.

### H. Split inspection generation into an independent second pass

Create an independently runnable function/script/notebook that accepts only persisted paths/files.

A strong test is to run Stage A, terminate/restart the Python process, and then run Stage B.

The second pass should consume:

```text
Day.csv
Day.properties.csv
IMG/
Day.inspection.json   # temporary inspection-only metadata
```

and generate:

```text
Day.html
```

### I. Update viewer to show image datetime from `.properties.csv`

Do not rediscover EXIF in the viewer.

Render a lower-right overlay such as:

```text
2026/11/15 14:25:10
```

only when `image_datetime` is present.

### J. Perform a controlled one-day end-to-end validation

Before a full-year run:

1. parse one known diary day;
2. inspect the two canonical CSV files and `IMG/`;
3. verify image bytes remain unchanged;
4. verify image datetime properties are correct where expected;
5. restart the inspection process from disk only;
6. generate HTML;
7. visually compare the HTML with the source Numbers file;
8. verify blank source rows and table headers still reconstruct correctly;
9. verify images without capture datetime display no invented timestamp.

### K. Run broader batch validation only after the one-day test passes

Then test across multiple days containing different combinations of:

- text-only records;
- hyperlinks;
- colors/fonts/emphasis;
- text+image cells;
- camera photos;
- screenshots;
- HEIC/HEIF;
- PNG/TIFF/JPEG/MPO;
- intentional blank cells;
- completely blank source rows;
- explicit row heights;
- merged cells.

### L. Later: define higher-level semantic mappings

Only after the raw archive format is stable, catalog the user-defined semantic vocabulary encoded by formatting.

Examples to investigate and formalize:

```text
background color -> category/event meaning
bold -> importance/emphasis
font size -> possible emphasis level
strikethrough -> retained visual state and/or semantic state
```

The mapping should be additive. Never discard the raw formatting property after deriving a higher-level semantic label.

---

## 17. Acceptance criteria for the project goal

The migration can be considered semantically successful only when all of the following are true:

1. `Diary Export/data/` alone contains everything needed by a future diary application to reproduce the diary's meaningful content and semantic cues.
2. Deleting `_inspection/` does not destroy any diary-domain information.
3. A future consumer can ignore Numbers-specific layout properties such as `span` and `row_height` without losing diary meaning.
4. Raw formatting properties that may encode meaning are preserved even before their meanings have been fully interpreted.
5. Hyperlinks remain associated with the correct text.
6. Images remain associated with the correct records and their original bytes are preserved.
7. Trustworthy embedded image capture datetime is preserved as explicit archive metadata when available.
8. Missing image capture datetime is represented as missing, not guessed.
9. Inspection HTML can be generated in a separate process from persisted artifacts, with no hidden RAM dependency on the source Numbers parse.
10. The data format is documented well enough that the diary can be reconstructed without reading the original parser source code or old chat history.

---

## 18. Short architectural summary

The target architecture after the next implementation steps is:

```text
Apple Numbers diary
        │
        │ parse once
        ▼
Diary Export/
├── data/                         PERMANENT
│   └── YYYY/Month/Day/
│       ├── Day.csv               logical values
│       ├── Day.properties.csv    semantic/intrinsic properties + small amount
│       │                         of preserved source-layout metadata
│       └── IMG/                  original image bytes
│
└── _inspection/                  DISPOSABLE
    └── YYYY/Month/Day/
        ├── Day.inspection.json   temporary Numbers-source structure needed
        │                         only for migration comparison
        └── Day.html              derived visual checker
```

Then, separately and later:

```text
Diary Export/data/
        │
        ├── future semantic interpretation
        │       raw colors/styles -> categories/tags when rules are known
        │
        └── future iOS diary application
                chooses its own layout and presentation
```

The central rule is:

> **Preserve the raw diary information now; interpret it later; never let the temporary inspection viewer become the permanent data model.**
