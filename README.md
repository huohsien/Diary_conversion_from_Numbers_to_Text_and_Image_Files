# Diary conversion from Numbers to Text and Image Files

This project converts Apple Numbers diary files into portable canonical CSV +
image data and generates one static HTML page per day for human visual
inspection.

## Design rule: module = details, notebook = visible workflow

`Numbers_Diary_Parser_v1.py` contains the detailed reusable operations, most
importantly `parse_numbers_file()`, which still parses exactly one Numbers diary
file.  The notebook deliberately owns the high-level orchestration: the visible
loops over hand-picked year folders, calendar-month folders, and individual
`.numbers` files.

That separation keeps the already-tested single-file parser reusable while
making batch processing readable and easy to stop or inspect at checkpoints.

## Source hierarchy

The current hand-picked 2026 source folder is:

```text
/Users/huohsien/Library/Mobile Documents/com~apple~Numbers/Documents/2026 Diary/
├── January/
├── February/
├── March/
├── April/
├── May/
├── June/
├── July/
└── August/
```

The notebook does not invent this path.  `SOURCE_YEAR_FOLDERS` contains the
explicit folders chosen by the user.  More years can later be added as more
`Path(...)` entries.

`list_month_folders(year_folder)` only handles the filesystem detail of finding
existing month folders and returning them in January-to-December order.
`list_numbers_files(month_folder)` handles file discovery inside one month.
The actual loops remain visible in the notebook.

There is no need to calculate February 28/29 or month lengths: only files that
actually exist are visited.  The diary DateCell inside each Numbers file still
determines the canonical output date.

## Runtime used for the current parser

The current working environment uses Python 3.12 and `numbers-parser` 4.19.0.
The older Python 3.8 / `numbers-parser` 4.4.2 environment failed while opening
`July 27.numbers` and `July 29.numbers` inside `numbers_parser.Document(...)`;
4.19.0 successfully opens and parses July 27.


## Run modes and dry run

The Notebook has one visible `RUN_MODE` switch:

- `RUN_MODE = "dry_run"` opens every discovered `.numbers` file, resolves its
  diary date, walks every sheet/table/row/cell, and exercises text, style,
  hyperlink, merge/span, background-image, and row-height reads. It writes no
  CSV, images, inspection HTML, staging directory, or `Diary Export` data.
- `RUN_MODE = "parse"` performs the real transactional export.

For a small real-output test, set `ONLY_FILE_NAMES` in the Notebook, for example
`{"July 27.numbers", "July 29.numbers"}`. The visible year/month loops remain
the same, but only those filenames are processed.

This keeps the high-level decision visible in the Notebook while the detailed
read/parse operations remain in `Numbers_Diary_Parser_v1.py`.

## Verbose run logging

Every Notebook run creates a timestamped verbose log under the Git project:

```text
<project>/logs/
└── diary-parser-YYYYMMDD-HHMMSS-<mode>.log
```

Logs live with the project rather than under `Diary Export`, because
`Diary Export` is derived output that may be deleted and regenerated. The log
therefore survives a clean re-export. The file log records DEBUG and above,
including runtime versions, source discovery, document opening, date
resolution/fallbacks, sheet/table/row progress, images, special styled cells,
row-height handling, CSV/HTML generation, staging, transactional commit/rollback,
full exception tracebacks, per-file elapsed time, and whole-run elapsed time.
The Notebook console remains much quieter; WARNING and ERROR messages are also
shown there.

`logs/*.log`, `__pycache__/`, and `*.pyc` are ignored by Git. `logs/.gitkeep`
only keeps the intentional logs directory in the repository.

## Output hierarchy

```text
~/Downloads/Diary Export/
├── data/
│   └── 2026/
│       └── May/
│           └── May 25/
│               ├── May 25.csv
│               ├── May 25.properties.csv
│               └── IMG/
│
└── _inspection/
    └── 2026/
        └── May/
            └── May 25/
                └── May 25.html
```

`data/` is canonical persistent export data. `_inspection/` is derived human
inspection output. Every successfully parsed day gets one directly-openable
HTML page. There is no inspection JSON, Flask server, viewer config, viewer log,
port, or `SHOW_INSPECTION` flag.

The HTML embeds viewer CSS, JavaScript, and reconstructed cell data. Images stay
in the canonical `data/.../IMG/` folder and are referenced through relative
paths.

## Canonical dual CSV

`<day>.csv` contains logical diary values: time, text, intentional empty fields,
and image filenames. `<day>.properties.csv` has the same logical shape and
stores matching properties.

Canonical rules include:

- merged continuation cells are skipped;
- horizontal merge is stored as `span=N`;
- intentional empty cells between meaningful items are preserved;
- visually styled empty cells (for example an empty gray cell) preserve their background instead of collapsing into ordinary white blanks;
- trailing unused physical cells are omitted;
- text properties preserve background, font color/name/size, bold, italic,
  underline, strike, and hyperlink metadata;
- rich-text dictionaries are normalized to visible text;
- images are stored in `IMG/` and referenced by bare filename;
- `row_height=20.0` is treated as the normal/default auto-fit state and is not
  stored;
- a Numbers row height different from `20.0` is preserved once as an explicit
  `row_height=<value>` property.

## Inspection layout

The static HTML is reconstructed from the canonical dual CSV, not from a Direct
representation.

1. Normal rows are text-driven and browser wrapping determines their height.
2. Explicit/manual rows use the preserved Numbers row height as a minimum after
   the same layout scaling used for content-column width.
3. Images preserve aspect ratio and contain inside the final row box.
4. Image-only rows fall back to one basic-column square unless an explicit row
   height is present.
5. Time cells remain horizontally centered but are vertically top-aligned, matching Numbers.
6. Background-only empty cells retain their exported background in the inspection HTML.

## Notebook checkpoints

The notebook is intentionally readable as workflow rather than as a function
library:

1. import/reload the parser module;
2. explicitly choose `SOURCE_YEAR_FOLDERS` and `OUTPUT_ROOT`;
3. preview each year/month and the number of `.numbers` files before writing;
4. visibly loop year -> month -> file and call `dry_run_numbers_file()` or
   `parse_numbers_file()` according to `RUN_MODE`;
5. print successes, errors, total elapsed time, and the run-log path at the end.

One unusual file can be recorded as an error while the remaining files continue
when `CONTINUE_ON_ERROR = True`.

## Transactional single-day replacement

`parse_numbers_file()` builds one complete day under `Diary Export/.staging/` first. Existing canonical data and inspection HTML are left untouched until CSV, properties CSV, images, and HTML have all been generated successfully. The staged data and inspection directories are then renamed into place with rollback backups, so an ordinary parse/build error does not corrupt a previous successful export.
