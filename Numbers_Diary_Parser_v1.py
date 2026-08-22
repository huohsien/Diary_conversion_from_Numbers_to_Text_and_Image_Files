from __future__ import annotations

from pathlib import Path
from datetime import date, datetime
import ast
import csv
import importlib.util
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
import webbrowser
from urllib.error import URLError
from urllib.parse import quote, unquote
from urllib.request import urlopen

import numbers_parser
from numbers_parser import Document


IMG_FOLDER_NAME = "IMG"
CSV_ENCODING = "utf-8-sig"

viewer_process = None
viewer_log_handle = None


# ----------------------------------------------------------------------
# Property-cell mini format
# ----------------------------------------------------------------------

def _property_escape(value):
    if value is None:
        return ""

    return quote(
        str(value),
        safe=" /:#?&@,+-._~[]()!",
        encoding="utf-8",
        errors="strict",
    )


def _property_unescape(value):
    return unquote(str(value), encoding="utf-8", errors="strict")


def _serialize_properties(properties):
    parts = []

    for key, value in properties.items():
        if value is None:
            continue

        if isinstance(value, bool):
            value = 1 if value else 0

        parts.append(f"{key}={_property_escape(value)}")

    return ";".join(parts)


def _parse_properties(text):
    if not text:
        return {}

    result = {}

    for token in str(text).split(";"):
        if not token or "=" not in token:
            continue

        key, value = token.split("=", 1)
        result[key] = _property_unescape(value)

    return result


# ----------------------------------------------------------------------
# Numbers parsing helpers
# ----------------------------------------------------------------------

def _safe_filename(filename):
    filename = Path(str(filename)).name
    filename = re.sub(r"[\x00-\x1f]", "_", filename)
    return filename or "image"


def _unique_destination_path(folder, filename):
    filename = _safe_filename(filename)
    candidate = folder / filename

    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    i = 2

    while True:
        candidate = folder / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _rgb_to_hex(rgb):
    if rgb is None:
        return None

    try:
        r = float(rgb.r)
        g = float(rgb.g)
        b = float(rgb.b)
    except Exception:
        try:
            r, g, b = [float(x) for x in rgb[:3]]
        except Exception:
            return None

    if max(r, g, b) <= 1.0:
        r, g, b = r * 255, g * 255, b * 255

    r = max(0, min(255, round(r)))
    g = max(0, min(255, round(g)))
    b = max(0, min(255, round(b)))

    return f"#{r:02x}{g:02x}{b:02x}"


def _background_to_css(bg_color):
    if bg_color is None:
        return None

    if isinstance(bg_color, (list, tuple)) and not hasattr(bg_color, "r"):
        colors = [_rgb_to_hex(c) for c in bg_color]
        colors = [c for c in colors if c]

        if len(colors) == 1:
            return colors[0]

        if len(colors) > 1:
            return "linear-gradient(180deg, " + ", ".join(colors) + ")"

    return _rgb_to_hex(bg_color)


def _extract_cell_style(cell):
    style = getattr(cell, "style", None)

    if style is None:
        return {}

    return {
        "background": _background_to_css(getattr(style, "bg_color", None)),
        "font_color": _rgb_to_hex(getattr(style, "font_color", None)),
        "font_size": getattr(style, "font_size", None),
        "font_name": getattr(style, "font_name", None),
        "bold": bool(getattr(style, "bold", False)),
        "italic": bool(getattr(style, "italic", False)),
        "underline": bool(getattr(style, "underline", False)),
        "strike": bool(getattr(style, "strikethrough", False)),
    }


def _rich_text_dict(cell):
    value = getattr(cell, "value", None)

    if isinstance(value, dict):
        return value

    formatted = getattr(cell, "formatted_value", None)

    if isinstance(formatted, dict):
        return formatted

    return None


def _extract_hyperlinks(cell):
    """
    Prefer the public numbers-parser Cell.hyperlinks property.
    Fall back to older rich-text dictionary representations.
    """
    raw_links = getattr(cell, "hyperlinks", None)

    if not raw_links:
        rich = _rich_text_dict(cell)
        raw_links = rich.get("hyperlinks") if rich else []

    links = []

    for item in raw_links or []:
        display_text = None
        target_url = None

        if isinstance(item, dict):
            display_text = (
                item.get("text")
                or item.get("display")
                or item.get("label")
                or item.get("title")
            )
            target_url = (
                item.get("url")
                or item.get("href")
                or item.get("target")
            )

        elif isinstance(item, (tuple, list)):
            if len(item) >= 2:
                display_text = item[0]
                target_url = item[1]
            elif len(item) == 1:
                display_text = item[0]
                target_url = item[0]

        elif isinstance(item, str):
            display_text = item
            target_url = item

        if display_text is None and target_url is not None:
            display_text = target_url

        if target_url is None and display_text is not None:
            target_url = display_text

        if display_text is None or target_url is None:
            continue

        links.append(
            {
                "text": str(display_text),
                "url": str(target_url),
            }
        )

    return links
def _extract_visible_text_from_rich_value(value):
    """
    Normalize rich-text representations from different numbers-parser builds.
    """
    if isinstance(value, dict):
        text_value = value.get("text")
        if text_value is not None:
            return str(text_value)

    if isinstance(value, str):
        stripped = value.strip()

        # Defensive support for a stringified rich-text metadata dictionary,
        # which is exactly what appeared in the 11:19 exported cell.
        if stripped.startswith("{") and "'text'" in stripped:
            try:
                parsed = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                parsed = None

            if isinstance(parsed, dict) and parsed.get("text") is not None:
                return str(parsed["text"])

        return value

    return None


def _cell_value_as_text(cell):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return ""

    value = getattr(cell, "value", None)

    if value is None:
        return ""

    if isinstance(cell, numbers_parser.cell.DateCell):
        try:
            return value.strftime("%H:%M")
        except Exception:
            pass

    # For text/rich-text, trust value first. In the user's parser build,
    # formatted_value can expose the whole rich-text metadata structure.
    visible = _extract_visible_text_from_rich_value(value)
    if visible is not None:
        return visible

    formatted = getattr(cell, "formatted_value", None)
    visible = _extract_visible_text_from_rich_value(formatted)

    if visible not in (None, ""):
        return visible

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return str(value)
def _extract_background_image(cell, img_dir):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return None

    style = getattr(cell, "style", None)
    if style is None:
        return None

    bg_image = getattr(style, "bg_image", None)

    if not isinstance(bg_image, numbers_parser.cell.BackgroundImage):
        return None

    image_data = getattr(bg_image, "data", None)
    if image_data is None:
        return None

    original_filename = getattr(bg_image, "filename", None) or "image"
    destination = _unique_destination_path(img_dir, original_filename)
    destination.write_bytes(image_data)

    return destination.name


def _horizontal_span(cell):
    """
    Only horizontal merge/span is part of this diary schema.
    Vertical rowspan is intentionally not exported.
    """
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return 0

    if not bool(getattr(cell, "is_merged", False)):
        return 1

    size = getattr(cell, "size", None)

    if not size:
        return 1

    try:
        # numbers-parser merge size is (rows, columns).
        return max(1, int(size[1]))
    except Exception:
        return 1


def _empty_cell_properties(span=1):
    return {
        "type": "empty",
        "span": span,
    }


def _logical_item_from_cell(cell, img_dir):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return None

    text_value = _cell_value_as_text(cell)
    image_filename = _extract_background_image(cell, img_dir)
    span = _horizontal_span(cell)
    style = _extract_cell_style(cell)
    hyperlinks = _extract_hyperlinks(cell)

    if text_value and image_filename:
        item_type = "text_image"
        data_value = text_value

    elif image_filename:
        item_type = "image"
        data_value = image_filename

    elif isinstance(cell, numbers_parser.cell.DateCell) and text_value:
        item_type = "datetime"
        data_value = text_value

    elif text_value:
        item_type = "text"
        data_value = text_value

    else:
        item_type = "empty"
        data_value = ""

    props = {
        "type": item_type,
        "span": span,
    }

    if item_type == "text_image":
        props["image_file"] = image_filename

    if item_type in ("text", "text_image"):
        for key in (
            "background",
            "font_color",
            "font_size",
            "font_name",
            "bold",
            "italic",
            "underline",
            "strike",
        ):
            value = style.get(key)
            if value is not None:
                props[key] = value

        for i, link in enumerate(hyperlinks, start=1):
            props[f"link{i}_text"] = link["text"]
            props[f"link{i}_url"] = link["url"]

    elif item_type == "image":
        # Background color is still useful for visual inspection if present.
        if style.get("background") is not None:
            props["background"] = style["background"]

    return data_value, props


def _find_last_meaningful_source_col(table, row, img_dir):
    """
    Find the last physical source column containing actual diary content.

    Ordinary empty cells BEFORE that point are intentional gaps and MUST be
    preserved in the canonical CSV. Ordinary empty cells AFTER that point are
    merely trailing table capacity and are omitted.
    """
    last_col = None

    # This function MUST NOT extract images again, so inspect only presence.
    for source_col in range(len(row)):
        cell = table.cell(row.index if hasattr(row, "index") else 0, source_col)

    return last_col


def _has_background_image(cell):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return False

    style = getattr(cell, "style", None)
    if style is None:
        return False

    return isinstance(
        getattr(style, "bg_image", None),
        numbers_parser.cell.BackgroundImage,
    )


def _cell_has_meaningful_content(cell):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return False

    if _has_background_image(cell):
        return True

    return bool(_cell_value_as_text(cell))


def _table_layout_constants(table):
    """
    Inspection-only geometry.

    The canonical CSV/property CSV does not store UI geometry. For the viewer,
    we only retain the two Numbers template widths:
      - time column width (A)
      - basic content-column width (B, C, D, ...)
    """
    try:
        time_width = float(table.col_width(0))
    except Exception:
        time_width = 64.0

    widths = []

    # Sample actual physical content columns. The user's diary template normally
    # uses equal B/C/D/... widths and creates wider areas by merging cells.
    try:
        source_rows = table.rows()
        max_cols = max((len(row) for row in source_rows), default=0)
    except Exception:
        max_cols = 0

    for c in range(1, max_cols):
        try:
            width = float(table.col_width(c))
            if width > 0:
                widths.append(width)
        except Exception:
            pass

    basic_width = statistics.median(widths) if widths else 120.0

    return {
        "time_column_width": time_width,
        "basic_column_width": basic_width,
    }


# ----------------------------------------------------------------------
# Canonical export
# ----------------------------------------------------------------------

def parse_numbers_file(numbers_file, output_root):
    """
    Canonical output:

        <day>.csv
        <day>.properties.csv
        IMG/

    One Numbers diary row -> one CSV record.

    Rules:
    - merged continuation cells are skipped
    - a merged anchor becomes ONE logical CSV item with property span=N
    - an ordinary empty physical cell between two content items is preserved:
          data CSV        -> empty field
          properties CSV  -> type=empty;span=1
    - trailing unused physical cells are omitted
    - shorter records are padded only at the END to make rectangular CSV
    """
    numbers_file = Path(numbers_file).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()

    if not numbers_file.exists():
        raise FileNotFoundError(f"Numbers file not found: {numbers_file}")

    if numbers_file.suffix.lower() != ".numbers":
        raise ValueError(f"Expected a .numbers file: {numbers_file}")

    output_day_folder = output_root / numbers_file.stem
    img_dir = output_day_folder / IMG_FOLDER_NAME
    data_csv_path = output_day_folder / f"{numbers_file.stem}.csv"
    properties_csv_path = output_day_folder / f"{numbers_file.stem}.properties.csv"
    inspection_json_path = output_day_folder / f"{numbers_file.stem}.inspection.json"

    output_day_folder.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    doc = Document(str(numbers_file))

    data_rows = []
    property_rows = []

    # Inspection-only: preserve original row numbering / blank source rows,
    # plus only the two template width constants. This does NOT enter canonical
    # data or properties.
    inspection_source_rows = []
    layout_constants = None

    for sheet in doc.sheets:
        for table in sheet.tables:
            if layout_constants is None:
                layout_constants = _table_layout_constants(table)

            source_rows = table.rows()

            for source_row_index, row in enumerate(source_rows):
                meaningful_cols = []

                for source_col_index in range(len(row)):
                    cell = table.cell(source_row_index, source_col_index)

                    if _cell_has_meaningful_content(cell):
                        meaningful_cols.append(source_col_index)

                if not meaningful_cols:
                    inspection_source_rows.append(
                        {
                            "numbers_row": source_row_index + 1,
                            "record_index": None,
                        }
                    )
                    continue

                last_meaningful_col = max(meaningful_cols)

                data_row = []
                property_row = []

                for source_col_index in range(last_meaningful_col + 1):
                    cell = table.cell(source_row_index, source_col_index)

                    # A continuation part of B:C:D merge is NOT a logical item.
                    if isinstance(cell, numbers_parser.cell.MergedCell):
                        continue

                    data_value, props = _logical_item_from_cell(
                        cell,
                        img_dir=img_dir,
                    )

                    data_row.append(data_value)
                    property_row.append(_serialize_properties(props))

                record_index = len(data_rows)
                data_rows.append(data_row)
                property_rows.append(property_row)

                inspection_source_rows.append(
                    {
                        "numbers_row": source_row_index + 1,
                        "record_index": record_index,
                    }
                )

    max_items = max((len(row) for row in data_rows), default=0)

    data_rows = [
        row + [""] * (max_items - len(row))
        for row in data_rows
    ]
    property_rows = [
        row + [""] * (max_items - len(row))
        for row in property_rows
    ]

    with data_csv_path.open("w", newline="", encoding=CSV_ENCODING) as fp:
        csv.writer(fp).writerows(data_rows)

    with properties_csv_path.open("w", newline="", encoding=CSV_ENCODING) as fp:
        csv.writer(fp).writerows(property_rows)

    build_inspection_json(
        data_csv_path=data_csv_path,
        properties_csv_path=properties_csv_path,
        inspection_json_path=inspection_json_path,
        inspection_source_rows=inspection_source_rows,
        layout_constants=layout_constants or {
            "time_column_width": 64.0,
            "basic_column_width": 120.0,
        },
    )

    return {
        "source": str(numbers_file),
        "output_folder": str(output_day_folder),
        "csv": str(data_csv_path),
        "properties_csv": str(properties_csv_path),
        "img_folder": str(img_dir),
        "inspection_json": str(inspection_json_path),
    }


# ----------------------------------------------------------------------
# Derived inspection JSON
# ----------------------------------------------------------------------

def _property_number(props, key, default=None, cast=float):
    value = props.get(key)

    if value in (None, ""):
        return default

    try:
        return cast(value)
    except Exception:
        return default


def build_inspection_json(
    data_csv_path,
    properties_csv_path,
    inspection_json_path,
    inspection_source_rows,
    layout_constants,
):
    """
    Viewer data is reconstructed from canonical data+properties.

    The ONLY extra inspection-only source information is:
      - original Numbers row numbering / blank rows
      - time-column width
      - basic content-column width

    No row heights or per-cell widths are copied from Numbers.
    """
    data_csv_path = Path(data_csv_path)
    properties_csv_path = Path(properties_csv_path)
    inspection_json_path = Path(inspection_json_path)

    with data_csv_path.open("r", encoding=CSV_ENCODING, newline="") as fp:
        data_rows = list(csv.reader(fp))

    with properties_csv_path.open("r", encoding=CSV_ENCODING, newline="") as fp:
        property_rows = list(csv.reader(fp))

    if len(data_rows) != len(property_rows):
        raise ValueError(
            "Data CSV and properties CSV do not have the same row count."
        )

    records = []
    max_physical_columns = 1  # A = time column.

    for record_index, (data_row, prop_row) in enumerate(
        zip(data_rows, property_rows)
    ):
        if len(data_row) != len(prop_row):
            raise ValueError(
                f"CSV shape mismatch at record row {record_index + 1}."
            )

        cells = []
        physical_col = 0

        for logical_index, (value, prop_text) in enumerate(
            zip(data_row, prop_row)
        ):
            # Trailing rectangular padding.
            if value == "" and prop_text == "":
                continue

            props = _parse_properties(prop_text)
            item_type = props.get("type", "text")
            span = max(1, int(_property_number(props, "span", 1, int)))

            style = {
                "background": props.get("background"),
                "font_color": props.get("font_color"),
                "font_size": _property_number(
                    props,
                    "font_size",
                    None,
                    float,
                ),
                "font_name": props.get("font_name"),
                "bold": props.get("bold") == "1",
                "italic": props.get("italic") == "1",
                "underline": props.get("underline") == "1",
                "strike": props.get("strike") == "1",
            }

            links = []
            i = 1

            while True:
                text_key = f"link{i}_text"
                url_key = f"link{i}_url"

                if text_key not in props and url_key not in props:
                    break

                links.append(
                    {
                        "text": props.get(text_key, ""),
                        "url": props.get(url_key, ""),
                    }
                )
                i += 1

            cell = {
                "logical_index": logical_index,
                "type": item_type,
                "value": value,
                "span": span,
                "physical_col": physical_col,
                "style": style,
                "links": links,
                "image_file": (
                    value
                    if item_type == "image"
                    else props.get("image_file")
                ),
            }

            cells.append(cell)
            physical_col += span

        max_physical_columns = max(
            max_physical_columns,
            physical_col,
        )

        records.append(
            {
                "record_index": record_index,
                "cells": cells,
            }
        )

    payload = {
        "data_csv": data_csv_path.name,
        "properties_csv": properties_csv_path.name,
        "time_column_width": float(
            layout_constants["time_column_width"]
        ),
        "basic_column_width": float(
            layout_constants["basic_column_width"]
        ),
        "max_physical_columns": max_physical_columns,
        "source_rows": inspection_source_rows,
        "records": records,
    }

    inspection_json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return inspection_json_path


# ----------------------------------------------------------------------
# Viewer lifecycle
# ----------------------------------------------------------------------

def _viewer_ready(url):
    try:
        with urlopen(url, timeout=0.5) as response:
            return response.status < 500
    except (URLError, TimeoutError, OSError):
        return False


def _pid_is_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True


def _terminate_pid(pid, timeout=5):
    pid = int(pid)

    if not _pid_is_alive(pid):
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    return True


def _listener_pids_on_port(port):
    completed = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    return sorted(
        {
            int(line.strip())
            for line in completed.stdout.splitlines()
            if line.strip().isdigit()
        }
    )


def _process_command(pid):
    completed = subprocess.run(
        ["ps", "-p", str(int(pid)), "-o", "command="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip()


def _is_our_viewer_process(pid, viewer_app):
    command = _process_command(pid)

    if not command:
        return False

    return str(Path(viewer_app).resolve()) in command


def _close_log_handle():
    global viewer_log_handle

    if viewer_log_handle is not None:
        try:
            viewer_log_handle.close()
        except Exception:
            pass

    viewer_log_handle = None


def _stop_in_memory_viewer():
    global viewer_process

    if viewer_process is not None and viewer_process.poll() is None:
        viewer_process.terminate()

        try:
            viewer_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            viewer_process.kill()
            viewer_process.wait(timeout=5)

    viewer_process = None
    _close_log_handle()


def _stop_stale_viewer(viewer_app, viewer_port, pid_file):
    _stop_in_memory_viewer()

    candidate_pids = []

    if pid_file.is_file():
        try:
            candidate_pids.append(
                int(pid_file.read_text(encoding="utf-8").strip())
            )
        except Exception:
            pass

    for pid in _listener_pids_on_port(viewer_port):
        if pid not in candidate_pids:
            candidate_pids.append(pid)

    for pid in candidate_pids:
        if _pid_is_alive(pid) and _is_our_viewer_process(pid, viewer_app):
            _terminate_pid(pid)

    deadline = time.monotonic() + 5

    while time.monotonic() < deadline:
        listeners = _listener_pids_on_port(viewer_port)

        if not listeners:
            break

        if all(
            not _is_our_viewer_process(pid, viewer_app)
            for pid in listeners
        ):
            raise RuntimeError(
                f"Port {viewer_port} is used by another application."
            )

        time.sleep(0.1)

    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def stop_numbers_viewer(viewer_port=8766):
    module_root = Path(__file__).resolve().parent
    viewer_app = module_root / "viewer" / "app.py"
    pid_file = module_root / "viewer" / ".numbers_diary_viewer.pid"

    _stop_stale_viewer(
        viewer_app=viewer_app,
        viewer_port=viewer_port,
        pid_file=pid_file,
    )

    print("Numbers Diary Viewer is stopped.")


def display_numbers_export(result, viewer_port=8766):
    global viewer_process, viewer_log_handle

    output_folder = Path(result["output_folder"]).expanduser().resolve()
    inspection_json_path = Path(result["inspection_json"]).expanduser().resolve()

    module_root = Path(__file__).resolve().parent
    viewer_app = module_root / "viewer" / "app.py"
    viewer_log = output_folder / "viewer-server.log"
    viewer_config = output_folder / "_viewer_config.json"
    pid_file = module_root / "viewer" / ".numbers_diary_viewer.pid"

    if not viewer_app.is_file():
        raise FileNotFoundError(f"Viewer app was not found:\n{viewer_app}")

    if importlib.util.find_spec("flask") is None:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "Flask"]
        )

    viewer_config.write_text(
        json.dumps(
            {
                "output_folder": str(output_folder),
                "inspection_json": str(inspection_json_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _stop_stale_viewer(
        viewer_app=viewer_app,
        viewer_port=viewer_port,
        pid_file=pid_file,
    )

    viewer_log_handle = open(viewer_log, "a", encoding="utf-8")

    viewer_process = subprocess.Popen(
        [
            sys.executable,
            str(viewer_app),
            "--config",
            str(viewer_config),
            "--host",
            "127.0.0.1",
            "--port",
            str(viewer_port),
            "--pid-file",
            str(pid_file),
        ],
        cwd=str(viewer_app.parent),
        stdout=viewer_log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    local_url = f"http://127.0.0.1:{int(viewer_port)}/"
    deadline = time.monotonic() + 12

    while time.monotonic() < deadline:
        if _viewer_ready(local_url):
            break

        if viewer_process.poll() is not None:
            break

        time.sleep(0.25)

    if not _viewer_ready(local_url):
        return_code = viewer_process.poll()
        _close_log_handle()

        raise RuntimeError(
            "Numbers Diary Viewer did not start.\n"
            f"Process return code: {return_code}\n"
            f"Read the server log:\n{viewer_log}"
        )

    print(f"Numbers Diary Viewer: {local_url}")
    print(f"Viewer server log: {viewer_log}")

    browser_url = f"{local_url}?opened={time.time_ns()}"
    opened = webbrowser.open_new_tab(browser_url)

    if not opened:
        print("Browser did not open automatically. Open this URL:")
        print(local_url)

    return local_url
