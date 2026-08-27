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

## Notebook checkpoints

The notebook is intentionally readable as workflow rather than as a function
library:

1. import/reload the parser module;
2. explicitly choose `SOURCE_YEAR_FOLDERS` and `OUTPUT_ROOT`;
3. preview each year/month and the number of `.numbers` files before writing;
4. visibly loop year -> month -> file and call `parse_numbers_file()` once per
   file;
5. print successes and errors at the end.

One unusual file can be recorded as an error while the remaining files continue
when `CONTINUE_ON_ERROR = True`.

## Transactional single-day replacement

`parse_numbers_file()` builds one complete day under `Diary Export/.staging/` first. Existing canonical data and inspection HTML are left untouched until CSV, properties CSV, images, and HTML have all been generated successfully. The staged data and inspection directories are then renamed into place with rollback backups, so an ordinary parse/build error does not corrupt a previous successful export.
