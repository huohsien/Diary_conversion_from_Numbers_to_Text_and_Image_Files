from __future__ import annotations

import argparse
import atexit
import csv
import json
import os
from pathlib import Path

from flask import Flask, abort, render_template, send_from_directory


APP_ROOT = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(APP_ROOT / "templates"),
    static_folder=str(APP_ROOT / "static"),
)

CONFIG = None


def load_config(config_path):
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))

    for key in ("output_folder", "csv_path", "metadata_path"):
        payload[key] = Path(payload[key]).expanduser().resolve()

    return payload


def image_relative_path(cell_text):
    if not cell_text:
        return None

    img_prefix = str(CONFIG["img_folder_name"]) + "/"

    for line in str(cell_text).splitlines():
        stripped = line.strip()

        if stripped.startswith(img_prefix):
            candidate = (CONFIG["output_folder"] / stripped).resolve()
            output_root = CONFIG["output_folder"].resolve()

            if candidate != output_root and output_root not in candidate.parents:
                return None

            if candidate.is_file():
                return stripped

    return None


def text_without_image_path(cell_text):
    if not cell_text:
        return ""

    img_prefix = str(CONFIG["img_folder_name"]) + "/"

    kept = [
        line
        for line in str(cell_text).splitlines()
        if not line.strip().startswith(img_prefix)
    ]
    return "\n".join(kept).strip()


def css_style(style):
    if not style:
        return ""

    parts = []

    if style.get("background"):
        parts.append(f"background:{style['background']}")

    if style.get("font_color"):
        parts.append(f"color:{style['font_color']}")

    if style.get("font_size_pt"):
        parts.append(f"font-size:{style['font_size_pt']}pt")

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
    if style.get("strikethrough"):
        decorations.append("line-through")
    if decorations:
        parts.append("text-decoration:" + " ".join(decorations))

    horizontal = style.get("horizontal_alignment")
    if horizontal in ("left", "center", "right", "justify"):
        parts.append(f"text-align:{horizontal}")

    vertical = style.get("vertical_alignment")
    if vertical == "top":
        parts.append("vertical-align:top")
    elif vertical in ("middle", "center"):
        parts.append("vertical-align:middle")
    elif vertical == "bottom":
        parts.append("vertical-align:bottom")

    inset = style.get("text_inset_pt")
    if inset is not None:
        try:
            parts.append(f"padding-left:{float(inset)}pt")
            parts.append(f"padding-right:{float(inset)}pt")
        except Exception:
            pass

    return ";".join(parts)


@app.route("/")
def index():
    with CONFIG["csv_path"].open(
        "r",
        encoding=CONFIG["csv_encoding"],
        newline="",
    ) as fp:
        csv_rows = list(csv.reader(fp))

    metadata = json.loads(
        CONFIG["metadata_path"].read_text(encoding="utf-8")
    )

    # Current diary files use one table. If there are multiple tables,
    # render them sequentially.
    csv_index = 0
    display_tables = []
    total_image_rows = []

    for table_meta in metadata.get("tables", []):
        display_rows = []

        for row_meta in table_meta.get("rows", []):
            if csv_index >= len(csv_rows):
                break

            csv_row = csv_rows[csv_index]
            csv_index += 1

            cells = []
            row_has_image = False

            for cell_index, cell_meta in enumerate(row_meta.get("cells", [])):
                cell_text = csv_row[cell_index] if cell_index < len(csv_row) else ""
                image_rel = image_relative_path(cell_text)

                if image_rel:
                    row_has_image = True

                cells.append(
                    {
                        "text": text_without_image_path(cell_text),
                        "image_rel": image_rel,
                        "rowspan": int(cell_meta.get("rowspan", 1)),
                        "colspan": int(cell_meta.get("colspan", 1)),
                        "source_col": int(cell_meta.get("source_col", cell_index)),
                        "style": css_style(cell_meta.get("style", {})),
                    }
                )

            if row_has_image:
                total_image_rows.append(len(total_image_rows) + 1)

            source_row = int(row_meta.get("source_row", 0))
            row_height = None
            row_heights = table_meta.get("row_heights", [])

            if 0 <= source_row < len(row_heights):
                row_height = row_heights[source_row]

            display_rows.append(
                {
                    "source_row": source_row,
                    "height": row_height,
                    "cells": cells,
                }
            )

        display_tables.append(
            {
                "sheet_name": table_meta.get("sheet_name"),
                "table_name": table_meta.get("table_name"),
                "col_widths": table_meta.get("col_widths", []),
                "rows": display_rows,
            }
        )

    return render_template(
        "viewer.html",
        csv_name=CONFIG["csv_path"].name,
        tables=display_tables,
        row_count=len(csv_rows),
    )


@app.route("/file/<path:relative_path>")
def exported_file(relative_path):
    output_root = CONFIG["output_folder"].resolve()
    candidate = (output_root / relative_path).resolve()

    if candidate != output_root and output_root not in candidate.parents:
        abort(404)

    return send_from_directory(str(output_root), relative_path)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--pid-file")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    CONFIG = load_config(args.config)

    pid_file = Path(args.pid_file).resolve() if args.pid_file else None

    if pid_file is not None:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        def remove_pid_file():
            try:
                if pid_file.is_file():
                    recorded = pid_file.read_text(encoding="utf-8").strip()
                    if recorded == str(os.getpid()):
                        pid_file.unlink()
            except Exception:
                pass

        atexit.register(remove_pid_file)

    app.run(
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False,
    )
