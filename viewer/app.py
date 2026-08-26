from __future__ import annotations

from html import escape
import os
from pathlib import Path
from urllib.parse import quote


APP_ROOT = Path(__file__).resolve().parent
NUMBERS_SOURCE_BASIC_COLUMN_WIDTH = 98.0


def column_name(index_zero_based):
    n = int(index_zero_based) + 1
    result = ""

    while n:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result

    return result


def css_text_style(style):
    parts = [
        "text-align:left",
        "vertical-align:top",
    ]

    if style.get("background"):
        parts.append(f"background:{style['background']}")

    if style.get("font_color"):
        parts.append(f"color:{style['font_color']}")

    if style.get("font_size") is not None:
        parts.append(f"font-size:{style['font_size']}pt")

    if style.get("font_name"):
        font_name = str(style["font_name"]).replace('"', '\\"')
        parts.append(f'font-family:"{font_name}", sans-serif')

    if style.get("bold"):
        parts.append("font-weight:700")

    if style.get("italic"):
        parts.append("font-style:italic")

    decorations = []

    if style.get("underline"):
        decorations.append("underline")

    if style.get("strike"):
        decorations.append("line-through")

    if decorations:
        parts.append("text-decoration:" + " ".join(decorations))

    return ";".join(parts)


def split_text_into_link_segments(text, links):
    if not text:
        return []

    if not links:
        return [{"text": text, "url": None}]

    matches = []

    for link in links:
        display_text = str(link.get("text") or "")
        url = str(link.get("url") or "")

        if not display_text or not url:
            continue

        start = 0

        while True:
            idx = text.find(display_text, start)

            if idx < 0:
                break

            matches.append(
                {
                    "start": idx,
                    "end": idx + len(display_text),
                    "text": display_text,
                    "url": url,
                }
            )
            start = idx + len(display_text)

    matches.sort(key=lambda item: (item["start"], item["end"]))

    accepted = []
    last_end = -1

    for match in matches:
        if match["start"] < last_end:
            continue

        accepted.append(match)
        last_end = match["end"]

    segments = []
    cursor = 0

    for match in accepted:
        if match["start"] > cursor:
            segments.append(
                {
                    "text": text[cursor:match["start"]],
                    "url": None,
                }
            )

        segments.append(
            {
                "text": match["text"],
                "url": match["url"],
            }
        )
        cursor = match["end"]

    if cursor < len(text):
        segments.append(
            {
                "text": text[cursor:],
                "url": None,
            }
        )

    return segments


def _image_src(html_path, output_folder, image_file):
    image_path = Path(output_folder) / "IMG" / str(image_file)
    relative = os.path.relpath(image_path, start=Path(html_path).parent)
    return quote(Path(relative).as_posix(), safe="/._-~")


def _text_html(text, links):
    pieces = []

    for segment in split_text_into_link_segments(str(text or ""), links or []):
        segment_text = escape(str(segment["text"]))
        url = segment.get("url")

        if url:
            pieces.append(
                '<a class="rich-link" href="{}" target="_blank" rel="noopener">{}</a>'.format(
                    escape(str(url), quote=True),
                    segment_text,
                )
            )
        else:
            pieces.append(segment_text)

    return "".join(pieces)


def _render_table(payload, output_folder, html_path):
    time_width = float(payload.get("time_column_width", 64.0))
    basic_width = float(payload.get("basic_column_width", 120.0))
    source_basic_width = float(
        payload.get("source_basic_column_width", NUMBERS_SOURCE_BASIC_COLUMN_WIDTH)
    )
    max_physical_columns = int(payload.get("max_physical_columns", 1))

    records = {
        int(record["record_index"]): record
        for record in payload.get("records", [])
    }

    out = []
    out.append(
        '<table style="--time-width: {}px; --basic-width: {}px;">'.format(
            time_width,
            basic_width,
        )
    )
    out.append("<colgroup>")
    out.append('<col class="row-number-column">')
    out.append('<col class="time-column">')
    for _ in range(1, max_physical_columns):
        out.append('<col class="basic-column">')
    out.append("</colgroup>")

    out.append("<thead><tr>")
    out.append('<th class="corner"></th>')
    for col_index in range(max_physical_columns):
        out.append(
            '<th class="column-header">{}</th>'.format(
                escape(column_name(col_index))
            )
        )
    out.append("</tr></thead>")
    out.append("<tbody>")

    for source_row in payload.get("source_rows", []):
        numbers_row = int(source_row["numbers_row"])
        record_index = source_row.get("record_index")

        if record_index is None:
            out.append('<tr class="diary-row">')
            out.append(f'<th class="row-header">{numbers_row}</th>')
            for _ in range(max_physical_columns):
                out.append('<td class="blank-cell"></td>')
            out.append("</tr>")
            continue

        record = records[int(record_index)]
        explicit_row_height = record.get("row_height")
        display_row_height = None

        if explicit_row_height is not None:
            try:
                display_row_height = (
                    float(explicit_row_height) * basic_width / source_basic_width
                )
            except Exception:
                display_row_height = None

        if display_row_height is None:
            out.append('<tr class="diary-row">')
        else:
            out.append(
                '<tr class="diary-row" data-explicit-row-height="{}">'.format(
                    display_row_height
                )
            )

        out.append(f'<th class="row-header">{numbers_row}</th>')

        cells = record.get("cells", [])
        used_columns = 0

        for cell in cells:
            item_type = cell.get("type", "text")
            value = cell.get("value", "")
            span = max(1, int(cell.get("span", 1)))
            used_columns += span

            classes = ["diary-cell"]
            if item_type == "datetime":
                classes.append("time-cell")
            if item_type == "empty":
                classes.append("empty-cell")
            if item_type == "image":
                classes.append("image-cell")

            attrs = [
                f'colspan="{span}"',
                'class="{}"'.format(" ".join(classes)),
            ]

            if item_type in ("text", "text_image"):
                attrs.append(
                    'style="{}"'.format(
                        escape(css_text_style(cell.get("style") or {}), quote=True)
                    )
                )

            out.append("<td {}>".format(" ".join(attrs)))

            if item_type == "datetime":
                out.append(
                    '<div class="time-value">{}</div>'.format(
                        escape(str(value or ""))
                    )
                )

            elif item_type in ("text", "text_image"):
                out.append(
                    '<div class="text-value">{}</div>'.format(
                        _text_html(value, cell.get("links") or [])
                    )
                )

                image_file = cell.get("image_file")
                if item_type == "text_image" and image_file:
                    out.append(
                        '<div class="image-fit"><img src="{}" loading="lazy"></div>'.format(
                            escape(
                                _image_src(html_path, output_folder, image_file),
                                quote=True,
                            )
                        )
                    )

            elif item_type == "image" and cell.get("image_file"):
                out.append(
                    '<div class="image-fit"><img src="{}" loading="lazy"></div>'.format(
                        escape(
                            _image_src(
                                html_path,
                                output_folder,
                                cell["image_file"],
                            ),
                            quote=True,
                        )
                    )
                )

            out.append("</td>")

        for _ in range(used_columns, max_physical_columns):
            out.append('<td class="blank-cell"></td>')

        out.append("</tr>")

    out.append("</tbody></table>")
    return "\n".join(out)


def render_standalone_html(payload, output_folder, html_path):
    """
    Render one inspection page that can be opened directly from Finder.

    CSS, JavaScript and table data are embedded into the HTML. Images stay in
    the canonical data/IMG folder and are referenced with relative file paths.
    No Flask server, JSON sidecar, config file or log file is required.
    """
    html_path = Path(html_path).expanduser().resolve()
    output_folder = Path(output_folder).expanduser().resolve()

    template = (APP_ROOT / "templates" / "viewer.html").read_text(encoding="utf-8")
    css = (APP_ROOT / "static" / "style.css").read_text(encoding="utf-8")
    javascript = (APP_ROOT / "static" / "viewer.js").read_text(encoding="utf-8")

    title = str(payload.get("data_csv") or "Numbers Diary Inspection")
    table_html = _render_table(payload, output_folder, html_path)

    rendered = (
        template
        .replace("__TITLE__", escape(title))
        .replace("__INLINE_CSS__", css)
        .replace("__TABLE_HTML__", table_html)
        .replace("__INLINE_JS__", javascript)
    )

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(rendered, encoding="utf-8")
    return html_path
