from __future__ import annotations

import argparse
import atexit
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

NUMBERS_SOURCE_BASIC_COLUMN_WIDTH = 98.0


def load_config(config_path):
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    payload["output_folder"] = Path(payload["output_folder"]).expanduser().resolve()
    payload["inspection_json"] = Path(payload["inspection_json"]).expanduser().resolve()
    return payload


def column_name(index_zero_based):
    n = int(index_zero_based) + 1
    result = ""

    while n:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result

    return result


app.jinja_env.globals["column_name"] = column_name


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


@app.route("/")
def index():
    payload = json.loads(
        CONFIG["inspection_json"].read_text(encoding="utf-8")
    )

    records = {
        int(record["record_index"]): record
        for record in payload.get("records", [])
    }

    rows = []

    for source_row in payload.get("source_rows", []):
        record_index = source_row.get("record_index")

        if record_index is None:
            rows.append(
                {
                    "numbers_row": int(source_row["numbers_row"]),
                    "cells": [],
                }
            )
            continue

        record = records[int(record_index)]
        cells = []

        explicit_row_height = record.get("row_height")
        display_row_height = None
        if explicit_row_height is not None:
            try:
                basic_width = float(payload.get("basic_column_width", 120.0))
                source_basic_width = float(
                    payload.get(
                        "source_basic_column_width",
                        NUMBERS_SOURCE_BASIC_COLUMN_WIDTH,
                    )
                )
                display_row_height = (
                    float(explicit_row_height) * basic_width / source_basic_width
                )
            except Exception:
                display_row_height = None

        for cell in record.get("cells", []):
            item_type = cell.get("type", "text")
            value = cell.get("value", "")
            links = cell.get("links") or []

            cells.append(
                {
                    "type": item_type,
                    "value": value,
                    "span": int(cell.get("span", 1)),
                    "style": css_text_style(cell.get("style") or {}),
                    "segments": (
                        split_text_into_link_segments(value, links)
                        if item_type in ("text", "text_image")
                        else []
                    ),
                    "image_file": cell.get("image_file"),
                }
            )

        rows.append(
            {
                "numbers_row": int(source_row["numbers_row"]),
                "cells": cells,
                "display_row_height": display_row_height,
            }
        )

    return render_template(
        "viewer.html",
        title=payload.get("data_csv", "Numbers Diary Inspection"),
        time_width=float(payload.get("time_column_width", 64.0)),
        basic_width=float(payload.get("basic_column_width", 120.0)),
        max_physical_columns=int(payload.get("max_physical_columns", 1)),
        rows=rows,
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
