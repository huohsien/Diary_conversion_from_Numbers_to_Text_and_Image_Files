This iteration implements the final layout logic discussed on 2026-08-22.

Canonical data:
- <day>.csv
- <day>.properties.csv
- IMG/

Important canonical rules:
1. A merged anchor is ONE logical item; its property has span=N.
2. Merged continuation cells are skipped.
3. An ordinary empty physical cell BETWEEN meaningful items is preserved:
       data CSV        -> empty field
       properties CSV  -> type=empty;span=1
   Example:
       A=time, B:C=text, D=empty, E=image
   becomes:
       data:        06:10, text, , image.jpg
       properties:  datetime(span1), text(span2), empty(span1), image(span1)
4. Trailing unused cells are omitted, then records are padded only at the end
   to make the CSV rectangular.
5. Only horizontal span exists in the canonical schema. No rowspan.

Viewer layout:
- Header A/B/C/... and row-number strip mimic Numbers.
- A/time column is narrow and centered.
- Basic B/C/D/... width is taken once from the Numbers template and stored ONLY
  in disposable inspection JSON; it does not enter canonical data/properties.
- Text is always top-left, padded, and wraps naturally.
- Image uses aspect-preserving contain/fit behavior and is centered.
- Browser determines row height from the tallest rendered item.
- Original Numbers blank physical rows/row numbering are inspection-only.

Canonical properties do NOT contain source row/column, row height, or per-cell
column widths. The only transition-layout property is span.


Image-layout patch:
- Jinja whitespace stripping remains in place, so template indentation does not
  become visible pre-wrap whitespace.
- The Numbers-like A/B/C... top ruler and 1/2/3... left ruler remain sticky.
- Images no longer use width:100% as the sizing rule.
- A square one-basic-column cell is the baseline image row height.
- Text intrinsic wrapped height can make a row taller.
- Portrait/landscape images are aspect-fit and centered inside that final row,
  so tall portraits do not stretch the row merely because of their aspect ratio.


Hyperlink / rich-text fix:
- Cell.hyperlinks is read directly when available.
- Cell.value is preferred over formatted_value for text/rich-text.
- dict rich-text values export only the "text" field.
- stringified rich-text dictionaries are safely parsed with ast.literal_eval
  and export only the "text" field.
- linkN_text and linkN_url remain in the companion properties CSV.
