from __future__ import annotations

from pathlib import Path
from datetime import date, datetime
import csv
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

import numbers_parser
from numbers_parser import Document


IMG_FOLDER_NAME = "IMG"
CSV_ENCODING = "utf-8-sig"
IMAGE_AND_TEXT_SEPARATOR = "\n"

viewer_process = None
viewer_log_handle = None


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

    # Support either 0..1 floats or 0..255 values.
    if max(r, g, b) <= 1.0:
        r, g, b = r * 255, g * 255, b * 255

    r = max(0, min(255, round(r)))
    g = max(0, min(255, round(g)))
    b = max(0, min(255, round(b)))

    return f"#{r:02x}{g:02x}{b:02x}"


def _background_to_css(bg_color):
    if bg_color is None:
        return None

    if isinstance(bg_color, (list, tuple)) and bg_color:
        # RGB itself is tuple-like, so first detect RGB by attributes.
        if hasattr(bg_color, "r"):
            return _rgb_to_hex(bg_color)

        colors = [_rgb_to_hex(c) for c in bg_color]
        colors = [c for c in colors if c]

        if len(colors) == 1:
            return colors[0]

        if len(colors) > 1:
            return "linear-gradient(180deg, " + ", ".join(colors) + ")"

    return _rgb_to_hex(bg_color)


def _alignment_value(alignment, name, index):
    if alignment is None:
        return None

    value = getattr(alignment, name, None)

    if value is None:
        try:
            value = alignment[index]
        except Exception:
            return None

    if value is None:
        return None

    return str(value).lower()


def _extract_cell_style(cell):
    """
    Serialize the cell-wide style properties that numbers-parser exposes.

    Note: numbers-parser can read cell/paragraph style, but it does not expose
    mixed character formatting inside one cell. The exported metadata therefore
    represents the cell-wide style.
    """
    style = getattr(cell, "style", None)

    if style is None:
        return {}

    alignment = getattr(style, "alignment", None)

    return {
        "background": _background_to_css(getattr(style, "bg_color", None)),
        "font_color": _rgb_to_hex(getattr(style, "font_color", None)),
        "font_size_pt": getattr(style, "font_size", None),
        "font_name": getattr(style, "font_name", None),
        "bold": bool(getattr(style, "bold", False)),
        "italic": bool(getattr(style, "italic", False)),
        "underline": bool(getattr(style, "underline", False)),
        "strikethrough": bool(getattr(style, "strikethrough", False)),
        "horizontal_alignment": _alignment_value(alignment, "horizontal", 0),
        "vertical_alignment": _alignment_value(alignment, "vertical", 1),
        "text_inset_pt": getattr(style, "text_inset", None),
    }


def _cell_value_as_text(cell):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return ""

    value = getattr(cell, "value", None)

    if value is None:
        return ""

    # Diary time cells should be compact 24-hour HH:MM, not ISO timestamps.
    if isinstance(cell, numbers_parser.cell.DateCell):
        try:
            return value.strftime("%H:%M")
        except Exception:
            pass

    formatted = getattr(cell, "formatted_value", None)
    if formatted not in (None, ""):
        return str(formatted)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return str(value)


def _extract_background_image(cell, img_dir, output_day_folder):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return None

    style = getattr(cell, "style", None)
    if style is None:
        return None

    bg_image = getattr(style, "bg_image", None)

    if not isinstance(bg_image, numbers_parser.cell.BackgroundImage):
        return None

    original_filename = getattr(bg_image, "filename", None) or "image"
    image_data = getattr(bg_image, "data", None)

    if image_data is None:
        return None

    destination = _unique_destination_path(img_dir, original_filename)
    destination.write_bytes(image_data)

    return destination.relative_to(output_day_folder).as_posix()


def _merge_span(cell):
    """
    Return (rowspan, colspan) for a merge anchor.
    numbers-parser exposes merge-anchor size as (rows, columns).
    """
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return None

    if not bool(getattr(cell, "is_merged", False)):
        return (1, 1)

    size = getattr(cell, "size", None)

    if not size:
        return (1, 1)

    try:
        rowspan = int(size[0])
        colspan = int(size[1])
    except Exception:
        return (1, 1)

    return (max(1, rowspan), max(1, colspan))


def _table_geometry(table, row_count, col_count):
    row_heights = []
    col_widths = []

    for r in range(row_count):
        try:
            row_heights.append(float(table.row_height(r)))
        except Exception:
            row_heights.append(None)

    for c in range(col_count):
        try:
            col_widths.append(float(table.col_width(c)))
        except Exception:
            col_widths.append(None)

    return {
        "row_heights": row_heights,
        "col_widths": col_widths,
    }


def parse_numbers_file(numbers_file, output_root):
    """
    Parse one .numbers diary file into CSV + IMG + viewer metadata.

    Merged continuation cells are skipped. Merge anchors carry rowspan/colspan
    metadata so the browser viewer can reproduce the original merged layout.
    Leading completely-empty table rows are omitted.
    """
    numbers_file = Path(numbers_file).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()

    if not numbers_file.exists():
        raise FileNotFoundError(f"Numbers file not found: {numbers_file}")

    if numbers_file.suffix.lower() != ".numbers":
        raise ValueError(f"Expected a .numbers file: {numbers_file}")

    output_day_folder = output_root / numbers_file.stem
    img_dir = output_day_folder / IMG_FOLDER_NAME
    csv_path = output_day_folder / f"{numbers_file.stem}.csv"
    metadata_path = output_day_folder / f"{numbers_file.stem}.meta.json"

    output_day_folder.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    doc = Document(str(numbers_file))

    total_sheets = len(doc.sheets)
    total_tables = sum(len(sheet.tables) for sheet in doc.sheets)

    all_csv_rows = []
    tables_meta = []

    for sheet_index, sheet in enumerate(doc.sheets):
        sheet_name = getattr(sheet, "name", None) or f"Sheet {sheet_index + 1}"

        for table_index, table in enumerate(sheet.tables):
            table_name = getattr(table, "name", None) or f"Table {table_index + 1}"
            source_rows = table.rows()
            source_row_count = len(source_rows)
            source_col_count = max((len(row) for row in source_rows), default=0)

            geometry = _table_geometry(
                table,
                source_row_count,
                source_col_count,
            )

            table_rows_meta = []
            seen_nonempty_row = False

            for source_row_index, row in enumerate(source_rows):
                csv_row = []
                row_cells_meta = []
                row_has_content = False

                for source_col_index in range(len(row)):
                    cell = table.cell(source_row_index, source_col_index)

                    # Continuation pieces of merged regions must not appear as
                    # extra empty cells in CSV or Viewer.
                    if isinstance(cell, numbers_parser.cell.MergedCell):
                        continue

                    value_text = _cell_value_as_text(cell)
                    image_rel_path = _extract_background_image(
                        cell,
                        img_dir,
                        output_day_folder,
                    )

                    if value_text and image_rel_path:
                        csv_text = (
                            value_text
                            + IMAGE_AND_TEXT_SEPARATOR
                            + image_rel_path
                        )
                    elif image_rel_path:
                        csv_text = image_rel_path
                    else:
                        csv_text = value_text

                    rowspan, colspan = _merge_span(cell)

                    csv_row.append(csv_text)
                    row_cells_meta.append(
                        {
                            "source_col": source_col_index,
                            "rowspan": rowspan,
                            "colspan": colspan,
                            "style": _extract_cell_style(cell),
                        }
                    )

                    if value_text or image_rel_path:
                        row_has_content = True

                # Drop only blank rows at the beginning of a table.
                if not seen_nonempty_row and not row_has_content:
                    continue

                seen_nonempty_row = True

                all_csv_rows.append(csv_row)
                table_rows_meta.append(
                    {
                        "source_row": source_row_index,
                        "cells": row_cells_meta,
                    }
                )

            tables_meta.append(
                {
                    "sheet_name": sheet_name,
                    "table_name": table_name,
                    "source_row_count": source_row_count,
                    "source_col_count": source_col_count,
                    "row_heights": geometry["row_heights"],
                    "col_widths": geometry["col_widths"],
                    "rows": table_rows_meta,
                }
            )

    with csv_path.open("w", newline="", encoding=CSV_ENCODING) as fp:
        writer = csv.writer(fp)
        writer.writerows(all_csv_rows)

    metadata = {
        "source": str(numbers_file),
        "csv": str(csv_path),
        "img_folder": str(img_dir),
        "tables": tables_meta,
    }

    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "source": str(numbers_file),
        "output_folder": str(output_day_folder),
        "csv": str(csv_path),
        "metadata": str(metadata_path),
        "img_folder": str(img_dir),
        "sheet_count": total_sheets,
        "table_count": total_tables,
    }



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
        return True

    return True


def _listener_pids_on_port(port):
    """
    macOS fallback used after a kernel restart, when the old subprocess object
    is gone but the old Flask process is still listening on the fixed port.
    """
    try:
        completed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return []

    pids = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))

    return sorted(set(pids))


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

    viewer_app = str(Path(viewer_app).resolve())

    return (
        viewer_app in command
        or (
            "viewer/app.py" in command
            and "Numbers_Diary" in command
        )
    )


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
        pid = viewer_process.pid
        viewer_process.terminate()

        try:
            viewer_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            viewer_process.kill()
            viewer_process.wait(timeout=5)

        print(f"Stopped Notebook Viewer process: PID {pid}")

    viewer_process = None
    _close_log_handle()


def _stop_stale_viewer(viewer_app, viewer_port, pid_file):
    """
    Stop the previous Numbers Diary Viewer even after a Jupyter kernel restart.

    Order:
    1. Stop the subprocess object still known by this kernel.
    2. Stop the PID written by the previous Viewer.
    3. macOS fallback: inspect the process listening on the fixed port and
       terminate it only if its command line identifies this Viewer app.

    An unrelated service using the same port is never killed automatically.
    """
    _stop_in_memory_viewer()

    stale_pids = []

    if pid_file.is_file():
        try:
            stale_pids.append(int(pid_file.read_text(encoding="utf-8").strip()))
        except Exception:
            pass

    for pid in _listener_pids_on_port(viewer_port):
        if pid not in stale_pids:
            stale_pids.append(pid)

    for pid in stale_pids:
        if not _pid_is_alive(pid):
            continue

        if _is_our_viewer_process(pid, viewer_app):
            _terminate_pid(pid)
            print(f"Stopped stale Numbers Diary Viewer: PID {pid}")

    # Wait briefly for macOS to release the fixed port.
    local_url = f"http://127.0.0.1:{int(viewer_port)}/"
    deadline = time.monotonic() + 5

    while time.monotonic() < deadline:
        listeners = _listener_pids_on_port(viewer_port)

        if not listeners:
            break

        # If the remaining listener is not ours, do not kill it.
        if all(not _is_our_viewer_process(pid, viewer_app) for pid in listeners):
            raise RuntimeError(
                f"Port {viewer_port} is already used by another application.\n"
                "The Numbers Diary Parser will not kill an unrelated process."
            )

        time.sleep(0.1)

    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def stop_numbers_viewer(viewer_port=8766):
    """
    Stop the Viewer started by this Notebook or a previous kernel session.
    """
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
    """
    Restart the fixed-port standalone Viewer and open it in the default browser.

    The port stays fixed at 8766. A previous Viewer is explicitly terminated
    first, including one left behind by a Jupyter kernel restart.
    """
    global viewer_process, viewer_log_handle

    output_folder = Path(result["output_folder"]).expanduser().resolve()
    csv_path = Path(result["csv"]).expanduser().resolve()
    metadata_path = Path(result["metadata"]).expanduser().resolve()

    module_root = Path(__file__).resolve().parent
    viewer_app = module_root / "viewer" / "app.py"
    viewer_log = output_folder / "viewer-server.log"
    viewer_config = output_folder / "_viewer_config.json"
    pid_file = module_root / "viewer" / ".numbers_diary_viewer.pid"

    if not viewer_app.is_file():
        raise FileNotFoundError(f"Viewer app was not found:\n{viewer_app}")

    if not csv_path.is_file():
        raise FileNotFoundError(f"Parsed CSV was not found:\n{csv_path}")

    if not metadata_path.is_file():
        raise FileNotFoundError(f"Viewer metadata was not found:\n{metadata_path}")

    if importlib.util.find_spec("flask") is None:
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "Flask",
        ])

    viewer_config.write_text(
        json.dumps(
            {
                "output_folder": str(output_folder),
                "csv_path": str(csv_path),
                "metadata_path": str(metadata_path),
                "img_folder_name": IMG_FOLDER_NAME,
                "csv_encoding": CSV_ENCODING,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Important: always stop the previous fixed-port Viewer first.
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

    # Cache-busting query gives the browser a genuinely new navigation target.
    browser_url = f"{local_url}?opened={time.time_ns()}"
    opened = webbrowser.open_new_tab(browser_url)

    if not opened:
        print("Browser did not open automatically. Open this URL:")
        print(local_url)

    return local_url
