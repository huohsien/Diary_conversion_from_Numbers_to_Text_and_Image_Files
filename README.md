# Diary conversion from Numbers to Text and Image Files

V1 parses one Apple Numbers diary file at a time and exports portable canonical
CSV data plus images. V2 will later traverse an entire year folder.

## Canonical output

```text
~/Downloads/Diary Export/
└── 2026/
    └── May/
        └── May 25/
            ├── May 25.csv
            ├── May 25.properties.csv
            └── IMG/
```

`<day>.csv` contains logical diary values: time, text, intentional empty fields,
and image filenames. `<day>.properties.csv` has the same row/column shape and
stores the matching properties.

Canonical rules:

- merged continuation cells are skipped;
- horizontal merge is stored as `span=N`;
- intentional empty cells between meaningful items are preserved;
- trailing unused physical cells are omitted;
- text properties preserve background, font color/name/size, bold, italic,
  underline, strike, and hyperlink metadata;
- rich-text dictionaries are normalized to visible text;
- images are stored in `IMG/` and referenced by bare filename;
- the diary template uses a fixed time column plus equal-width content columns;
- `numbers-parser` reports `row_height=20.0` for normal/default rows. That value
  is not stored because Numbers auto-fits those rows to wrapped text;
- a row height different from 20.0 is treated as an explicit/manual row height
  and stored once as `row_height=<value>` in that row's first properties field.

## Inspection

When `SHOW_INSPECTION = True`:

```text
~/Downloads/Diary Export/
└── _inspection/
    └── 2026/
        └── May/
            └── May 25/
                ├── properties.inspection.json
                ├── viewer-server.log
                └── viewer-config.json
```

There is one inspection viewer only:

```text
<day>.csv + <day>.properties.csv
        ↓
properties.inspection.json
        ↓
viewer.html
```

Row layout rule:

1. Normal rows (`row_height` absent) are text-driven. The browser wraps text at
   the width implied by `span` and lets that text determine the row height.
2. Explicit/manual rows use the stored Numbers height as a minimum height,
   scaled by the same ratio used for the fixed content-column width.
3. Images never determine the height of a row that contains text. They keep
   their aspect ratio and `contain` inside the final row box. This preserves the
   diary behavior where a tall screenshot may appear narrow in an ordinary row,
   while a manually enlarged row gives a tall screenshot more display area.
4. Image-only rows fall back to one basic-column square height unless an
   explicit/manual row height is present.

## V1 notebook

```python
TEST_NUMBERS_FILE = Path("/Users/huohsien/Desktop/May 25 copy.numbers")
OUTPUT_ROOT = Path("/Users/huohsien/Downloads/Diary Export")
SHOW_INSPECTION = True
```

Set `SHOW_INSPECTION = False` for canonical export only.
