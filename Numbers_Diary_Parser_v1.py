from __future__ import annotations

from pathlib import Path
from datetime import date, datetime
import ast
import csv
import logging
import re
import shutil
import sys
import tempfile
import uuid
from importlib.metadata import PackageNotFoundError, version as package_version
from time import perf_counter
from urllib.parse import quote, unquote

import numbers_parser
from numbers_parser import Document

from viewer.app import render_standalone_html


IMG_FOLDER_NAME = "IMG"
CSV_ENCODING = "utf-8-sig"
PROJECT_ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("numbers_diary_parser")
LOGGER.addHandler(logging.NullHandler())


def _numbers_parser_version():
    try:
        return package_version("numbers-parser")
    except PackageNotFoundError:
        return "unknown"


def _format_elapsed(seconds):
    hours, remainder = divmod(float(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:05.2f}"


def configure_run_logging(run_mode, log_dir=None):
    """
    Configure one verbose log file for a Notebook run.

    Logs belong to the project, not to Diary Export. Diary Export is derived
    output and may be deleted/rebuilt; run logs should survive that operation.
    The file handler records DEBUG and above. The console handler only emits
    WARNING and ERROR so the Notebook's own progress output stays readable.
    """
    log_dir = Path(log_dir or (PROJECT_ROOT / "logs")).expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_mode or "run"))
    log_path = log_dir / f"diary-parser-{timestamp}-{safe_mode}.log"

    # Re-running the Notebook setup cell must not multiply handlers.
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    LOGGER.addHandler(console_handler)

    LOGGER.info("RUN START mode=%s", run_mode)
    LOGGER.info("project_root=%s", PROJECT_ROOT)
    LOGGER.info("log_path=%s", log_path)
    LOGGER.info("python=%s", sys.version.replace("\n", " "))
    LOGGER.info("python_executable=%s", sys.executable)
    LOGGER.info("numbers-parser=%s", _numbers_parser_version())
    return LOGGER, log_path


def finish_run_logging(run_mode, started_at, results, errors):
    elapsed = perf_counter() - started_at
    LOGGER.info(
        "RUN END mode=%s parsed_ok=%d failed=%d elapsed=%s",
        run_mode,
        len(results),
        len(errors),
        _format_elapsed(elapsed),
    )
    for handler in LOGGER.handlers:
        try:
            handler.flush()
        except Exception:
            pass
    return elapsed


# Property-driven viewer uses the fixed diary-template display widths from the
# verified dual-CSV viewer. Canonical row heights are stored only when the
# Numbers file contains an explicit/manual height.
PROPERTY_TIME_COLUMN_WIDTH = 64.0
PROPERTY_BASIC_COLUMN_WIDTH = 120.0
NUMBERS_SOURCE_BASIC_COLUMN_WIDTH = 98.0
NUMBERS_DEFAULT_ROW_HEIGHT = 20.0


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

    LOGGER.debug("IMAGE filename collision: %s", candidate)
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
        except Exception as exc:
            LOGGER.debug("STYLE unable to convert color %r: %s", rgb, exc)
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
            except (ValueError, SyntaxError) as exc:
                LOGGER.warning("RICH_TEXT unable to parse stringified metadata: %s", exc)
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
        except Exception as exc:
            LOGGER.debug("DATE_CELL strftime failed; using fallback: %s", exc)

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
    LOGGER.debug(
        "IMAGE wrote file=%s bytes=%d",
        destination,
        len(image_data),
    )

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
    except Exception as exc:
        LOGGER.warning("MERGE unable to read horizontal span; using span=1: %s", exc)
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

    elif item_type == "empty":
        # A text-empty cell with a visible background is still canonical layout
        # information and must not collapse into an ordinary white blank.
        if style.get("background") is not None:
            props["background"] = style["background"]
            LOGGER.debug("CELL preserve styled empty background=%s", style["background"])

    if hyperlinks:
        LOGGER.debug("CELL hyperlinks=%d", len(hyperlinks))

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


# ----------------------------------------------------------------------
# Diary date / output hierarchy
# ----------------------------------------------------------------------

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _find_diary_date(doc, numbers_file):
    """
    Prefer the actual DateCell stored in the Numbers diary. This removes the
    need for a manually configured TEST_YEAR in V1.

    Fallbacks are intentionally conservative: parse month/day from the filename
    and a 4-digit year from the source path. If no year can be established, fail
    rather than silently exporting into the wrong year.
    """
    for sheet in doc.sheets:
        for table in sheet.tables:
            for r, row in enumerate(table.rows()):
                for c in range(len(row)):
                    cell = table.cell(r, c)
                    if not isinstance(cell, numbers_parser.cell.DateCell):
                        continue

                    value = getattr(cell, "value", None)
                    if isinstance(value, datetime):
                        resolved = value.date()
                        LOGGER.debug(
                            "DATE resolved from DateCell sheet=%s table=%s row=%d col=%d date=%s",
                            getattr(sheet, "name", "?"),
                            getattr(table, "name", "?"),
                            r + 1,
                            c + 1,
                            resolved,
                        )
                        return resolved
                    if isinstance(value, date):
                        LOGGER.debug(
                            "DATE resolved from DateCell sheet=%s table=%s row=%d col=%d date=%s",
                            getattr(sheet, "name", "?"),
                            getattr(table, "name", "?"),
                            r + 1,
                            c + 1,
                            value,
                        )
                        return value

    stem = Path(numbers_file).stem
    month_pattern = "|".join(_MONTH_NAMES)
    match = re.search(
        rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
        stem,
        flags=re.IGNORECASE,
    )

    if not match:
        LOGGER.error("DATE unresolved from content and filename: %s", numbers_file)
        raise ValueError(
            "Could not determine diary month/day from Numbers content or filename: "
            f"{Path(numbers_file).name}"
        )

    month_name = match.group(1).capitalize()
    month = _MONTH_NAMES.index(month_name) + 1
    day = int(match.group(2))

    year = None
    for part in reversed(Path(numbers_file).parts):
        year_match = re.fullmatch(r"(19|20)\d{2}", part)
        if year_match:
            year = int(part)
            break

    if year is None:
        LOGGER.error("DATE filename fallback found month/day but no year in path: %s", numbers_file)
        raise ValueError(
            "The Numbers file has no usable DateCell and its path does not contain "
            "a 4-digit year. V1 will not guess the diary year."
        )

    resolved = date(year, month, day)
    LOGGER.warning(
        "DATE using filename/path fallback source=%s resolved=%s",
        numbers_file,
        resolved,
    )
    return resolved


def _build_output_paths(output_root, diary_date):
    output_root = Path(output_root).expanduser().resolve()
    month_name = _MONTH_NAMES[diary_date.month - 1]
    day_name = f"{month_name} {diary_date.day}"

    data_root = output_root / "data"
    inspection_root = output_root / "_inspection"

    data_day_folder = data_root / str(diary_date.year) / month_name / day_name
    inspection_day_folder = (
        inspection_root / str(diary_date.year) / month_name / day_name
    )

    return {
        "output_root": output_root,
        "data_root": data_root,
        "inspection_root": inspection_root,
        "month_name": month_name,
        "day_name": day_name,
        "data_day_folder": data_day_folder,
        "inspection_day_folder": inspection_day_folder,
        "csv": data_day_folder / f"{day_name}.csv",
        "properties_csv": data_day_folder / f"{day_name}.properties.csv",
        "img_folder": data_day_folder / IMG_FOLDER_NAME,
        "inspection_html": inspection_day_folder / f"{day_name}.html",
    }


# ----------------------------------------------------------------------
# Transactional per-day output
# ----------------------------------------------------------------------

def _remove_path(path):
    path = Path(path)
    if not path.exists():
        return
    LOGGER.debug("REMOVE path=%s", path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _commit_staged_day_outputs(staged_paths, final_paths):
    """
    Install one fully-built diary day without exposing a half-written result.

    Both the canonical day directory and its inspection day directory are
    constructed under OUTPUT_ROOT/.staging first.  Existing final directories
    are renamed to temporary backups, the staged directories are renamed into
    place, and backups are removed only after both installs succeed.

    If an ordinary Python/filesystem exception occurs during the commit phase,
    any newly installed directories are removed and the previous directories
    are restored from their backups.
    """
    transaction_id = uuid.uuid4().hex
    LOGGER.info("COMMIT begin transaction=%s", transaction_id)
    pairs = [
        (
            Path(staged_paths["data_day_folder"]),
            Path(final_paths["data_day_folder"]),
        ),
        (
            Path(staged_paths["inspection_day_folder"]),
            Path(final_paths["inspection_day_folder"]),
        ),
    ]

    backups = []
    installed = []

    try:
        for _, final_dir in pairs:
            final_dir.parent.mkdir(parents=True, exist_ok=True)

        # Move the previous known-good version out of the way, but keep it
        # available for rollback until BOTH staged directories are installed.
        for _, final_dir in pairs:
            backup_dir = final_dir.with_name(
                f".{final_dir.name}.backup-{transaction_id}"
            )
            _remove_path(backup_dir)

            if final_dir.exists():
                LOGGER.debug("COMMIT backup %s -> %s", final_dir, backup_dir)
                final_dir.rename(backup_dir)
                backups.append((final_dir, backup_dir))

        # Staging lives under the same OUTPUT_ROOT, so these are same-filesystem
        # directory renames rather than copy-and-delete operations.
        for staged_dir, final_dir in pairs:
            LOGGER.debug("COMMIT install %s -> %s", staged_dir, final_dir)
            staged_dir.rename(final_dir)
            installed.append(final_dir)

    except Exception:
        LOGGER.exception("COMMIT failed transaction=%s; rolling back", transaction_id)
        # Remove any partially installed new version.
        for final_dir in reversed(installed):
            _remove_path(final_dir)

        # Restore every previous known-good version that was backed up.
        for final_dir, backup_dir in reversed(backups):
            if backup_dir.exists():
                backup_dir.rename(final_dir)
        raise

    else:
        for _, backup_dir in backups:
            _remove_path(backup_dir)
        LOGGER.info("COMMIT complete transaction=%s", transaction_id)


# ----------------------------------------------------------------------
# Canonical export
# ----------------------------------------------------------------------

def _numbers_row_height(table, row_index):
    try:
        return float(table.row_height(row_index))
    except Exception as exc:
        LOGGER.warning(
            "ROW_HEIGHT unable to read table=%s row=%d: %s",
            getattr(table, "name", "?"),
            row_index + 1,
            exc,
        )
        return None


def _explicit_row_height(table, row_index):
    """
    numbers-parser returns 20.0 for the diary template's normal/default row
    height. Numbers.app then auto-fits those rows to wrapped text when it
    renders them. A stored height different from 20.0 is treated as an
    explicit/manual row height and is preserved in the canonical properties.
    """
    height = _numbers_row_height(table, row_index)
    if height is None:
        return None

    if abs(height - NUMBERS_DEFAULT_ROW_HEIGHT) < 1e-9:
        return None

    return height


def _table_header_counts(table):
    """
    Return the Numbers table's structural header counts.

    These are presentation metadata used only by the inspection viewer:
      - num_header_rows: table header rows (normally 1 in the diary template)
      - num_header_cols: table header columns (normally 1, the time column)

    Canonical CSV/properties content is intentionally unchanged.
    """
    def _read(name):
        value = getattr(table, name, 0)
        if callable(value):
            value = value()
        try:
            return max(0, int(value or 0))
        except Exception as exc:
            LOGGER.warning(
                "HEADER unable to read table=%s %s=%r: %s",
                getattr(table, "name", "?"),
                name,
                value,
                exc,
            )
            return 0

    return _read("num_header_rows"), _read("num_header_cols")


def dry_run_numbers_file(numbers_file):
    """
    Read and validate one Numbers diary without writing canonical output.

    The dry run opens the document, resolves its diary date, walks every
    sheet/table/row/cell, exercises the same text/style/link/span/background-image
    reads used by the real parser, and reads stored row heights. It deliberately
    does not create CSV, IMG, HTML, .staging, or anything under Diary Export.
    """
    numbers_file = Path(numbers_file).expanduser().resolve()
    started = perf_counter()
    LOGGER.info("DRY_RUN begin source=%s", numbers_file)

    if not numbers_file.exists():
        LOGGER.error("DRY_RUN source missing: %s", numbers_file)
        raise FileNotFoundError(f"Numbers file not found: {numbers_file}")

    if numbers_file.suffix.lower() != ".numbers":
        LOGGER.error("DRY_RUN wrong extension: %s", numbers_file)
        raise ValueError(f"Expected a .numbers file: {numbers_file}")

    try:
        LOGGER.debug("DRY_RUN Document open begin source=%s", numbers_file)
        doc = Document(str(numbers_file))
        LOGGER.debug("DRY_RUN Document open complete source=%s", numbers_file)
        diary_date = _find_diary_date(doc, numbers_file)

        sheet_count = 0
        table_count = 0
        row_count = 0
        cell_count = 0
        meaningful_cell_count = 0
        image_count = 0
        hyperlink_count = 0
        explicit_height_count = 0

        for sheet_index, sheet in enumerate(doc.sheets):
            sheet_count += 1
            LOGGER.debug(
                "DRY_RUN sheet index=%d name=%s",
                sheet_index,
                getattr(sheet, "name", "?"),
            )

            for table_index, table in enumerate(sheet.tables):
                table_count += 1
                source_rows = table.rows()
                num_header_rows, num_header_cols = _table_header_counts(table)
                LOGGER.debug(
                    "DRY_RUN table sheet=%d table=%d name=%s rows=%d header_rows=%d header_cols=%d",
                    sheet_index,
                    table_index,
                    getattr(table, "name", "?"),
                    len(source_rows),
                    num_header_rows,
                    num_header_cols,
                )

                for source_row_index, row in enumerate(source_rows):
                    row_count += 1
                    explicit_height = _explicit_row_height(table, source_row_index)
                    if explicit_height is not None:
                        explicit_height_count += 1

                    row_meaningful = 0
                    for source_col_index in range(len(row)):
                        cell = table.cell(source_row_index, source_col_index)
                        cell_count += 1

                        if isinstance(cell, numbers_parser.cell.MergedCell):
                            continue

                        text_value = _cell_value_as_text(cell)
                        style = _extract_cell_style(cell)
                        links = _extract_hyperlinks(cell)
                        span = _horizontal_span(cell)
                        hyperlink_count += len(links)

                        bg_image = getattr(getattr(cell, "style", None), "bg_image", None)
                        has_image = isinstance(bg_image, numbers_parser.cell.BackgroundImage)
                        if has_image:
                            image_count += 1
                            # Access the same payload that real extraction writes, but
                            # intentionally do not write it anywhere.
                            image_data = getattr(bg_image, "data", None)
                            LOGGER.debug(
                                "DRY_RUN image row=%d col=%d file=%s bytes=%s",
                                source_row_index + 1,
                                source_col_index + 1,
                                getattr(bg_image, "filename", None),
                                len(image_data) if image_data is not None else None,
                            )

                        is_meaningful = bool(
                            text_value
                            or has_image
                            or style.get("background") is not None
                        )
                        if is_meaningful:
                            meaningful_cell_count += 1
                            row_meaningful += 1

                        if span > 1 or links or style.get("background") is not None:
                            LOGGER.debug(
                                "DRY_RUN cell row=%d col=%d span=%d text=%s image=%s links=%d background=%s",
                                source_row_index + 1,
                                source_col_index + 1,
                                span,
                                bool(text_value),
                                has_image,
                                len(links),
                                style.get("background"),
                            )

                    LOGGER.debug(
                        "DRY_RUN row=%d physical_cells=%d meaningful_cells=%d explicit_height=%s",
                        source_row_index + 1,
                        len(row),
                        row_meaningful,
                        explicit_height,
                    )

        elapsed = perf_counter() - started
        result = {
            "source": str(numbers_file),
            "date": diary_date.isoformat(),
            "status": "OK",
            "mode": "dry_run",
            "sheets": sheet_count,
            "tables": table_count,
            "rows": row_count,
            "cells": cell_count,
            "meaningful_cells": meaningful_cell_count,
            "images": image_count,
            "hyperlinks": hyperlink_count,
            "explicit_row_heights": explicit_height_count,
            "elapsed_seconds": elapsed,
        }
        LOGGER.info(
            "DRY_RUN success source=%s date=%s sheets=%d tables=%d rows=%d cells=%d images=%d elapsed=%s",
            numbers_file,
            diary_date,
            sheet_count,
            table_count,
            row_count,
            cell_count,
            image_count,
            _format_elapsed(elapsed),
        )
        return result

    except Exception:
        LOGGER.exception(
            "DRY_RUN failed source=%s elapsed=%s",
            numbers_file,
            _format_elapsed(perf_counter() - started),
        )
        raise


def parse_numbers_file(numbers_file, output_root):
    """
    Parse ONE Numbers diary file transactionally.

    Nothing in an existing final day output is modified while parsing.  The new
    CSV, properties CSV, IMG folder, and static inspection HTML are first built
    in a temporary OUTPUT_ROOT/.staging tree that mirrors the final hierarchy.
    Only after every part succeeds are the staged data and inspection day
    directories committed into their final locations.

    Canonical data:
        Diary Export/data/<year>/<Month>/<Month day>/
            <Month day>.csv
            <Month day>.properties.csv
            IMG/

    Human visual inspection:
        Diary Export/_inspection/<year>/<Month>/<Month day>/
            <Month day>.html

    Row-height rule:
      - Numbers row_height == 20.0: do NOT store it. Wrapped text auto-fits and
        determines the rendered inspection row height.
      - Numbers row_height != 20.0: preserve row_height=<raw Numbers value> in
        the first logical properties cell and use it as an explicit minimum
        inspection height after applying the same 98->120 display scale.
    """
    numbers_file = Path(numbers_file).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    started = perf_counter()
    LOGGER.info("PARSE begin source=%s output_root=%s", numbers_file, output_root)

    if not numbers_file.exists():
        LOGGER.error("PARSE source missing: %s", numbers_file)
        raise FileNotFoundError(f"Numbers file not found: {numbers_file}")

    if numbers_file.suffix.lower() != ".numbers":
        LOGGER.error("PARSE wrong extension: %s", numbers_file)
        raise ValueError(f"Expected a .numbers file: {numbers_file}")

    # Opening the source and determining its diary date do not touch output.
    try:
        LOGGER.debug("PARSE Document open begin source=%s", numbers_file)
        doc = Document(str(numbers_file))
        LOGGER.debug("PARSE Document open complete source=%s", numbers_file)
        diary_date = _find_diary_date(doc, numbers_file)
        final_paths = _build_output_paths(output_root, diary_date)
        LOGGER.info("PARSE date=%s source=%s", diary_date, numbers_file)
        LOGGER.debug("PARSE final_data_dir=%s", final_paths["data_day_folder"])
        LOGGER.debug("PARSE final_inspection_dir=%s", final_paths["inspection_day_folder"])
    except Exception:
        LOGGER.exception(
            "PARSE failed before staging source=%s elapsed=%s",
            numbers_file,
            _format_elapsed(perf_counter() - started),
        )
        raise

    staging_parent = output_root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=f"{diary_date.isoformat()}-",
            dir=staging_parent,
        )
    )
    staged_paths = _build_output_paths(staging_root, diary_date)
    LOGGER.debug("STAGING created root=%s", staging_root)

    try:
        staged_paths["data_day_folder"].mkdir(parents=True, exist_ok=True)
        staged_paths["inspection_day_folder"].mkdir(parents=True, exist_ok=True)
        staged_paths["img_folder"].mkdir(parents=True, exist_ok=True)

        data_rows = []
        property_rows = []
        inspection_source_rows = []

        # Structural Numbers table metadata for the derived inspection viewer.
        # The canonical dual CSV remains unchanged.
        inspection_num_header_rows = None
        inspection_num_header_cols = None

        for sheet_index, sheet in enumerate(doc.sheets):
            LOGGER.debug(
                "PARSE sheet index=%d name=%s",
                sheet_index,
                getattr(sheet, "name", "?"),
            )
            for table_index, table in enumerate(sheet.tables):
                source_rows = table.rows()
                num_header_rows, num_header_cols = _table_header_counts(table)

                if inspection_num_header_rows is None:
                    inspection_num_header_rows = num_header_rows
                    inspection_num_header_cols = num_header_cols
                elif (
                    inspection_num_header_rows != num_header_rows
                    or inspection_num_header_cols != num_header_cols
                ):
                    LOGGER.warning(
                        "HEADER multiple tables use different header counts; "
                        "inspection uses first table header_rows=%d header_cols=%d, "
                        "current table=%s has header_rows=%d header_cols=%d",
                        inspection_num_header_rows,
                        inspection_num_header_cols,
                        getattr(table, "name", "?"),
                        num_header_rows,
                        num_header_cols,
                    )

                LOGGER.debug(
                    "PARSE table sheet=%d table=%d name=%s rows=%d header_rows=%d header_cols=%d",
                    sheet_index,
                    table_index,
                    getattr(table, "name", "?"),
                    len(source_rows),
                    num_header_rows,
                    num_header_cols,
                )

                for source_row_index, row in enumerate(source_rows):
                    prepared = {}
                    meaningful_cols = []

                    for source_col_index in range(len(row)):
                        cell = table.cell(source_row_index, source_col_index)

                        if isinstance(cell, numbers_parser.cell.MergedCell):
                            continue

                        data_value, props = _logical_item_from_cell(
                            cell,
                            img_dir=staged_paths["img_folder"],
                        )
                        prepared[source_col_index] = (data_value, props)

                        if (
                            props.get("type") != "empty"
                            or props.get("background") is not None
                        ):
                            meaningful_cols.append(source_col_index)

                    LOGGER.debug(
                        "PARSE row=%d physical_cells=%d meaningful_cols=%s",
                        source_row_index + 1,
                        len(row),
                        meaningful_cols,
                    )

                    if not meaningful_cols:
                        inspection_source_rows.append(
                            {
                                "numbers_row": source_row_index + 1,
                                "record_index": None,
                            }
                        )
                        continue

                    last_meaningful_col = max(meaningful_cols)
                    explicit_row_height = _explicit_row_height(
                        table,
                        source_row_index,
                    )
                    if explicit_row_height is not None:
                        LOGGER.debug(
                            "PARSE explicit row height row=%d height=%s",
                            source_row_index + 1,
                            explicit_row_height,
                        )

                    data_row = []
                    property_row = []
                    row_height_written = False

                    for source_col_index in range(last_meaningful_col + 1):
                        if source_col_index not in prepared:
                            # Merged continuation: no logical canonical CSV field.
                            continue

                        data_value, props = prepared[source_col_index]
                        props = dict(props)

                        if (
                            explicit_row_height is not None
                            and not row_height_written
                        ):
                            props["row_height"] = explicit_row_height
                            row_height_written = True

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

        LOGGER.info(
            "PARSE extracted source=%s records=%d max_logical_items=%d images=%d",
            numbers_file,
            len(data_rows),
            max_items,
            len(list(staged_paths["img_folder"].iterdir())),
        )

        LOGGER.debug("CSV write data=%s", staged_paths["csv"])
        with staged_paths["csv"].open(
            "w",
            newline="",
            encoding=CSV_ENCODING,
        ) as fp:
            csv.writer(fp).writerows(data_rows)

        LOGGER.debug("CSV write properties=%s", staged_paths["properties_csv"])
        with staged_paths["properties_csv"].open(
            "w",
            newline="",
            encoding=CSV_ENCODING,
        ) as fp:
            csv.writer(fp).writerows(property_rows)

        LOGGER.debug("HTML build begin path=%s", staged_paths["inspection_html"])
        build_inspection_html(
            data_csv_path=staged_paths["csv"],
            properties_csv_path=staged_paths["properties_csv"],
            inspection_html_path=staged_paths["inspection_html"],
            output_folder=staged_paths["data_day_folder"],
            inspection_source_rows=inspection_source_rows,
            layout_constants={
                "time_column_width": PROPERTY_TIME_COLUMN_WIDTH,
                "basic_column_width": PROPERTY_BASIC_COLUMN_WIDTH,
                "source_basic_column_width": NUMBERS_SOURCE_BASIC_COLUMN_WIDTH,
                "num_header_rows": inspection_num_header_rows or 0,
                "num_header_cols": inspection_num_header_cols or 0,
            },
        )

        LOGGER.debug("HTML build complete path=%s", staged_paths["inspection_html"])

        # This is the only point at which the existing final day can change.
        _commit_staged_day_outputs(staged_paths, final_paths)

    except Exception:
        LOGGER.exception(
            "PARSE failed source=%s staging=%s elapsed=%s",
            numbers_file,
            staging_root,
            _format_elapsed(perf_counter() - started),
        )
        raise

    finally:
        # On parse/build failure, this only removes unfinished staging data.
        # On success, the two staged day directories have already been renamed
        # into final locations, so this removes the now-empty staging tree.
        _remove_path(staging_root)
        LOGGER.debug("STAGING cleanup root=%s", staging_root)
        try:
            staging_parent.rmdir()
        except OSError as exc:
            # Other concurrent/failed staging transactions may still exist.
            LOGGER.debug("STAGING parent retained path=%s reason=%s", staging_parent, exc)

    elapsed = perf_counter() - started
    LOGGER.info(
        "PARSE success source=%s date=%s elapsed=%s",
        numbers_file,
        diary_date,
        _format_elapsed(elapsed),
    )

    return {
        "source": str(numbers_file),
        "date": diary_date.isoformat(),
        "output_root": str(output_root),
        "data_root": str(final_paths["data_root"]),
        "inspection_root": str(final_paths["inspection_root"]),
        "output_folder": str(final_paths["data_day_folder"]),
        "csv": str(final_paths["csv"]),
        "properties_csv": str(final_paths["properties_csv"]),
        "img_folder": str(final_paths["img_folder"]),
        "inspection_folder": str(final_paths["inspection_day_folder"]),
        "inspection_html": str(final_paths["inspection_html"]),
        "transactional": True,
        "elapsed_seconds": elapsed,
    }


def list_month_folders(year_folder):
    """
    Return existing calendar-month folders under ONE hand-picked year folder,
    ordered January..December.

    This helper only handles the filesystem detail. The notebook deliberately
    owns the visible year -> month -> file control flow.
    """
    year_folder = Path(year_folder).expanduser().resolve()

    if not year_folder.is_dir():
        raise NotADirectoryError(f"Year folder not found: {year_folder}")

    by_name = {
        child.name.casefold(): child
        for child in year_folder.iterdir()
        if child.is_dir()
    }

    result = [
        by_name[month_name.casefold()]
        for month_name in _MONTH_NAMES
        if month_name.casefold() in by_name
    ]
    LOGGER.debug(
        "DISCOVERY year_folder=%s month_folders=%s",
        year_folder,
        [p.name for p in result],
    )
    return result


def _natural_path_sort_key(path):
    """Natural filename ordering such as May 2 before May 10."""
    parts = re.split(r"(\d+)", Path(path).name.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def list_numbers_files(month_folder):
    """
    Return .numbers files below ONE month folder in stable natural order.

    Recursive discovery is intentional so the notebook does not need to know
    whether an individual month stores day files directly or one level deeper.
    """
    month_folder = Path(month_folder).expanduser().resolve()

    if not month_folder.is_dir():
        raise NotADirectoryError(f"Month folder not found: {month_folder}")

    files = [
        path
        for path in month_folder.rglob("*.numbers")
        if not path.name.startswith("~$")
    ]
    result = sorted(files, key=_natural_path_sort_key)
    LOGGER.debug(
        "DISCOVERY month_folder=%s numbers_files=%d",
        month_folder,
        len(result),
    )
    return result


# ----------------------------------------------------------------------
# Derived inspection HTML
# ----------------------------------------------------------------------
# Derived inspection HTML
# ----------------------------------------------------------------------

def _property_number(props, key, default=None, cast=float):
    value = props.get(key)

    if value in (None, ""):
        return default

    try:
        return cast(value)
    except Exception as exc:
        LOGGER.warning(
            "PROPERTY invalid numeric value key=%s value=%r cast=%s: %s",
            key,
            value,
            getattr(cast, "__name__", cast),
            exc,
        )
        return default


def _build_inspection_payload(
    data_csv_path,
    properties_csv_path,
    inspection_source_rows,
    layout_constants,
):
    """Reconstruct inspection data strictly from the canonical dual CSV."""
    data_csv_path = Path(data_csv_path)
    properties_csv_path = Path(properties_csv_path)
    LOGGER.debug(
        "INSPECTION payload begin data=%s properties=%s",
        data_csv_path,
        properties_csv_path,
    )

    with data_csv_path.open("r", encoding=CSV_ENCODING, newline="") as fp:
        data_rows = list(csv.reader(fp))

    with properties_csv_path.open("r", encoding=CSV_ENCODING, newline="") as fp:
        property_rows = list(csv.reader(fp))

    if len(data_rows) != len(property_rows):
        raise ValueError("Data CSV and properties CSV do not have the same row count.")

    records = []
    max_physical_columns = 1

    for record_index, (data_row, prop_row) in enumerate(zip(data_rows, property_rows)):
        if len(data_row) != len(prop_row):
            raise ValueError(f"CSV shape mismatch at record row {record_index + 1}.")

        cells = []
        physical_col = 0
        explicit_row_height = None

        for logical_index, (value, prop_text) in enumerate(zip(data_row, prop_row)):
            if value == "" and prop_text == "":
                continue

            props = _parse_properties(prop_text)
            item_type = props.get("type", "text")
            span = max(1, int(_property_number(props, "span", 1, int)))

            if explicit_row_height is None:
                explicit_row_height = _property_number(
                    props,
                    "row_height",
                    None,
                    float,
                )

            style = {
                "background": props.get("background"),
                "font_color": props.get("font_color"),
                "font_size": _property_number(props, "font_size", None, float),
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

            cells.append(
                {
                    "logical_index": logical_index,
                    "type": item_type,
                    "value": value,
                    "span": span,
                    "physical_col": physical_col,
                    "style": style,
                    "links": links,
                    "image_file": (
                        value if item_type == "image" else props.get("image_file")
                    ),
                }
            )
            physical_col += span

        max_physical_columns = max(max_physical_columns, physical_col)
        records.append(
            {
                "record_index": record_index,
                "row_height": explicit_row_height,
                "cells": cells,
            }
        )

    LOGGER.debug(
        "INSPECTION payload complete records=%d max_physical_columns=%d",
        len(records),
        max_physical_columns,
    )
    return {
        "data_csv": data_csv_path.name,
        "properties_csv": properties_csv_path.name,
        "time_column_width": float(layout_constants["time_column_width"]),
        "basic_column_width": float(layout_constants["basic_column_width"]),
        "source_basic_column_width": float(
            layout_constants["source_basic_column_width"]
        ),
        "num_header_rows": int(layout_constants.get("num_header_rows", 0) or 0),
        "num_header_cols": int(layout_constants.get("num_header_cols", 0) or 0),
        "max_physical_columns": max_physical_columns,
        "source_rows": inspection_source_rows,
        "records": records,
    }


def build_inspection_html(
    data_csv_path,
    properties_csv_path,
    inspection_html_path,
    output_folder,
    inspection_source_rows,
    layout_constants,
):
    """
    Build a static inspection page directly from the canonical dual CSV.

    There is deliberately no JSON sidecar and no web server. The resulting HTML
    can be double-clicked in Finder. It references images in data/.../IMG by
    relative path, so the whole Diary Export hierarchy remains portable.
    """
    LOGGER.debug("INSPECTION HTML begin path=%s", inspection_html_path)
    payload = _build_inspection_payload(
        data_csv_path=data_csv_path,
        properties_csv_path=properties_csv_path,
        inspection_source_rows=inspection_source_rows,
        layout_constants=layout_constants,
    )

    result = render_standalone_html(
        payload=payload,
        output_folder=output_folder,
        html_path=inspection_html_path,
    )
    LOGGER.debug("INSPECTION HTML complete path=%s", result)
    return result

