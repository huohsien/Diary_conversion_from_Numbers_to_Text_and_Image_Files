from __future__ import annotations

from pathlib import Path
from datetime import date, datetime
import csv
import importlib.util
import json
import re
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
viewer_started_output_folder = None


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


def _cell_value_as_text(cell):
    if isinstance(cell, numbers_parser.cell.MergedCell):
        return ""

    value = getattr(cell, "value", None)

    if value is None:
        return ""

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


def _cell_to_csv_text(cell, img_dir, output_day_folder):
    value_text = _cell_value_as_text(cell)
    image_rel_path = _extract_background_image(
        cell,
        img_dir,
        output_day_folder,
    )

    if value_text and image_rel_path:
        return value_text + IMAGE_AND_TEXT_SEPARATOR + image_rel_path

    if image_rel_path:
        return image_rel_path

    return value_text


def parse_numbers_file(numbers_file, output_root):
    numbers_file = Path(numbers_file).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()

    if not numbers_file.exists():
        raise FileNotFoundError(f"Numbers file not found: {numbers_file}")

    if numbers_file.suffix.lower() != ".numbers":
        raise ValueError(f"Expected a .numbers file: {numbers_file}")

    output_day_folder = output_root / numbers_file.stem
    img_dir = output_day_folder / IMG_FOLDER_NAME
    csv_path = output_day_folder / f"{numbers_file.stem}.csv"

    output_day_folder.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    doc = Document(str(numbers_file))

    total_sheets = len(doc.sheets)
    total_tables = sum(len(sheet.tables) for sheet in doc.sheets)
    multiple_tables = total_tables > 1

    with csv_path.open("w", newline="", encoding=CSV_ENCODING) as fp:
        writer = csv.writer(fp)

        for sheet_index, sheet in enumerate(doc.sheets):
            sheet_name = getattr(sheet, "name", None) or f"Sheet {sheet_index + 1}"

            for table_index, table in enumerate(sheet.tables):
                table_name = getattr(table, "name", None) or f"Table {table_index + 1}"

                if multiple_tables:
                    writer.writerow(["# SHEET", sheet_name])
                    writer.writerow(["# TABLE", table_name])

                rows = table.rows()

                for idx_r, row in enumerate(rows):
                    csv_row = []

                    for idx_c in range(len(row)):
                        cell = table.cell(idx_r, idx_c)
                        csv_row.append(
                            _cell_to_csv_text(
                                cell,
                                img_dir,
                                output_day_folder,
                            )
                        )

                    writer.writerow(csv_row)

                if multiple_tables:
                    writer.writerow([])

    return {
        "source": str(numbers_file),
        "output_folder": str(output_day_folder),
        "csv": str(csv_path),
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


def _stop_viewer_process():
    global viewer_process, viewer_log_handle, viewer_started_output_folder

    if viewer_process is not None and viewer_process.poll() is None:
        viewer_process.terminate()

        try:
            viewer_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            viewer_process.kill()
            viewer_process.wait(timeout=5)

    if viewer_log_handle is not None:
        try:
            viewer_log_handle.close()
        except Exception:
            pass

    viewer_process = None
    viewer_log_handle = None
    viewer_started_output_folder = None


def stop_numbers_viewer():
    running = viewer_process is not None and viewer_process.poll() is None
    _stop_viewer_process()

    if running:
        print("Numbers Diary Viewer stopped.")
    else:
        print("No Numbers Diary Viewer process is running.")


def display_numbers_export(result, viewer_port=8766):
    """
    Start the standalone Flask Viewer as a subprocess, wait for it to become
    reachable, then open it in the default browser.

    The intended Notebook call stays:

        display_numbers_export(result)
    """
    global viewer_process, viewer_log_handle, viewer_started_output_folder

    output_folder = Path(result["output_folder"]).expanduser().resolve()
    csv_path = Path(result["csv"]).expanduser().resolve()

    module_root = Path(__file__).resolve().parent
    viewer_app = module_root / "viewer" / "app.py"
    viewer_log = output_folder / "viewer-server.log"
    viewer_config = output_folder / "_viewer_config.json"

    if not viewer_app.is_file():
        raise FileNotFoundError(
            "Viewer app was not found:\n"
            f"{viewer_app}"
        )

    if not csv_path.is_file():
        raise FileNotFoundError(
            "Parsed CSV was not found:\n"
            f"{csv_path}"
        )

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
                "img_folder_name": IMG_FOLDER_NAME,
                "csv_encoding": CSV_ENCODING,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _stop_viewer_process()

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
        ],
        cwd=str(viewer_app.parent),
        stdout=viewer_log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    viewer_started_output_folder = output_folder
    local_url = f"http://127.0.0.1:{viewer_port}/"

    deadline = time.monotonic() + 12

    while time.monotonic() < deadline:
        if _viewer_ready(local_url):
            break

        if viewer_process.poll() is not None:
            break

        time.sleep(0.25)

    if not _viewer_ready(local_url):
        return_code = viewer_process.poll()
        raise RuntimeError(
            "Numbers Diary Viewer did not start.\n"
            f"Process return code: {return_code}\n"
            f"Read the server log:\n{viewer_log}"
        )

    print(f"Numbers Diary Viewer: {local_url}")
    print(f"Viewer server log: {viewer_log}")

    webbrowser.open_new_tab(local_url)

    return local_url
