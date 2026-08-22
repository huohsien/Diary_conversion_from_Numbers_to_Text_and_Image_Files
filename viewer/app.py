from __future__ import annotations

import argparse
import csv
import json
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

    output_folder = Path(payload["output_folder"]).expanduser().resolve()
    csv_path = Path(payload["csv_path"]).expanduser().resolve()

    if not output_folder.is_dir():
        raise FileNotFoundError(f"Output folder is missing: {output_folder}")

    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file is missing: {csv_path}")

    payload["output_folder"] = output_folder
    payload["csv_path"] = csv_path
    return payload


def column_name(n):
    name = ""
    while n:
        n, remainder = divmod(n - 1, 26)
        name = chr(65 + remainder) + name
    return name


app.jinja_env.globals["column_name"] = column_name


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


@app.route("/")
def index():
    with CONFIG["csv_path"].open(
        "r",
        encoding=CONFIG["csv_encoding"],
        newline="",
    ) as fp:
        rows = list(csv.reader(fp))

    max_cols = max((len(row) for row in rows), default=0)

    display_rows = []
    rows_with_images = []

    for row_number, row in enumerate(rows, start=1):
        padded = row + [""] * (max_cols - len(row))
        display_cells = []
        image_count = 0

        for cell_text in padded:
            image_rel = image_relative_path(cell_text)

            if image_rel:
                image_count += 1

            display_cells.append(
                {
                    "text": text_without_image_path(cell_text),
                    "image_rel": image_rel,
                }
            )

        if image_count:
            rows_with_images.append(
                {
                    "row_number": row_number,
                    "image_count": image_count,
                }
            )

        display_rows.append(
            {
                "row_number": row_number,
                "cells": display_cells,
            }
        )

    return render_template(
        "viewer.html",
        csv_name=CONFIG["csv_path"].name,
        row_count=len(rows),
        col_count=max_cols,
        rows=display_rows,
        rows_with_images=rows_with_images,
    )


@app.route("/file/<path:relative_path>")
def exported_file(relative_path):
    output_root = CONFIG["output_folder"].resolve()
    candidate = (output_root / relative_path).resolve()

    if candidate != output_root and output_root not in candidate.parents:
        abort(404)

    return send_from_directory(
        str(output_root),
        relative_path,
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    CONFIG = load_config(args.config)

    app.run(
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False,
    )
