import asyncio
import csv
import hashlib
import importlib
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from .config import (
    delete_guild_config,
    get_config_value,
    get_guild_config,
    get_war_number,
    set_guild_config,
)

BRAND_NAME = "HESTIA | Konsortium"
BRAND_COLOR = 0xF0A66A
# 0 means no dedicated role is configured; @everyone is used instead.
VERIFIED_ROLE_ID = int(get_config_value("stockpile_verified_role_id", os.getenv("STOCKPILE_VERIFIED_ROLE_ID", 0)) or 0)
STOCKPILE_LOG_DIR = os.path.join(os.path.dirname(__file__), "stockpile_logs")
STOCKPILE_AGGREGATE_SHEET_NAME = "Aggregate"
STOCKPILE_LOG_META_SHEET_NAME = "_meta"
STOCKPILE_AGGREGATE_KEY = "__aggregate__"
STOCKPILE_REGIONS_FILE = os.getenv("STOCKPILE_REGIONS_FILE", os.path.join(os.path.dirname(__file__), "RegionNames.csv"))
_MATPLOTLIB_PYPLOT: Any = None
_MATPLOTLIB_LOAD_ATTEMPTED = False


def _get_matplotlib_pyplot() -> Any:
    global _MATPLOTLIB_PYPLOT, _MATPLOTLIB_LOAD_ATTEMPTED
    if _MATPLOTLIB_LOAD_ATTEMPTED:
        return _MATPLOTLIB_PYPLOT

    _MATPLOTLIB_LOAD_ATTEMPTED = True
    try:
        matplotlib = importlib.import_module("matplotlib")
        matplotlib.use("Agg")
        _MATPLOTLIB_PYPLOT = importlib.import_module("matplotlib.pyplot")
    except Exception:
        _MATPLOTLIB_PYPLOT = None

    return _MATPLOTLIB_PYPLOT


def get_configured_stockpile_role_ids(guild_id: int) -> list[int]:
    configured = get_guild_config(guild_id, "stockpile_access_roles", [])
    if not isinstance(configured, list):
        configured = [configured] if configured not in (None, "") else []
    role_ids: list[int] = []
    for value in configured[:4]:
        try:
            role_id = int(value)
        except (TypeError, ValueError):
            continue
        if role_id not in role_ids:
            role_ids.append(role_id)
    return role_ids


def has_stockpile_admin_access(member: discord.Member, guild: discord.Guild) -> bool:
    if member.guild_permissions.administrator:
        return True
    configured_ids = set(get_configured_stockpile_role_ids(guild.id))
    return bool(configured_ids.intersection(role.id for role in member.roles))


STOCKPILE_DEFAULT_ROLE_KEY = "stockpile_default_role"


def get_default_stockpile_role_id(guild: discord.Guild) -> int:
    try:
        configured = int(get_guild_config(guild.id, STOCKPILE_DEFAULT_ROLE_KEY) or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured and guild.get_role(configured) is not None:
        return configured
    return VERIFIED_ROLE_ID or guild.id


def get_stockpiles(guild_id: int) -> list[dict[str, Any]]:
    stockpiles = get_guild_config(guild_id, "stockpiles", [])
    if isinstance(stockpiles, list):
        return stockpiles
    return []


def set_stockpiles(guild_id: int, stockpiles: list[dict[str, Any]]) -> bool:
    return set_guild_config(guild_id, "stockpiles", stockpiles)


STOCKPILE_REMINDER_ENABLED_KEY = "stockpile_reminders_enabled"
STOCKPILE_REMINDER_CHANNEL_KEY = "stockpile_reminder_channel"
STOCKPILE_REMINDER_SECONDS = 36 * 60 * 60


def _stockpile_location_choices() -> list[str]:
    choices: set[str] = set()
    try:
        with open(STOCKPILE_REGIONS_FILE, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    for row in rows:
        if str(row.get("marker_type", "")).casefold() != "major":
            continue
        name = str(row.get("region_name", "")).strip()
        region = str(row.get("hex_name", "")).strip().removesuffix("Hex")
        if name and region:
            choices.add(f"{name}, {region}")
    return sorted(choices, key=str.casefold)


async def stockpile_location_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    query = str(current or "").casefold().strip()
    return [
        app_commands.Choice(name=location[:100], value=location)
        for location in _stockpile_location_choices()
        if not query or query in location.casefold()
    ][:25]


async def send_due_stockpile_reminders(bot: commands.Bot) -> int:
    sent_count = 0
    now = int(time.time())
    for guild in bot.guilds:
        if not get_guild_config(guild.id, STOCKPILE_REMINDER_ENABLED_KEY, False):
            continue
        channel_id = get_guild_config(guild.id, STOCKPILE_REMINDER_CHANNEL_KEY)
        try:
            channel = guild.get_channel(int(channel_id)) if channel_id else None
        except (TypeError, ValueError):
            channel = None
        if not isinstance(channel, discord.TextChannel):
            continue

        stockpiles = get_stockpiles(guild.id)
        changed = False
        for entry in stockpiles:
            created_at = int(entry.get("created_at", 0) or 0)
            timer_started = int(entry.get("reminder_reset_at", 0) or created_at)
            if not timer_started or now - timer_started < STOCKPILE_REMINDER_SECONDS:
                continue
            if int(entry.get("reminder_sent_at", 0) or 0) >= timer_started:
                continue
            creator_id = str(entry.get("creator_id") or entry.get("added_by") or "")
            mention = f"<@{creator_id}>" if creator_id.isdigit() else "The stockpile creator"
            embed = discord.Embed(
                title="Stockpile expiry warning",
                description=(
                    f"{mention}\n\nThis stockpile will lapse within 13h.\n"
                    "Use the **Reset reminder timer** button in its detailed `/stockpile-list` view to start the 36h timer again."
                ),
                color=0x992D22,
            )
            embed.add_field(name="Stockpile", value=str(entry.get("name", "Unknown")), inline=True)
            embed.add_field(name="Location", value=str(entry.get("location", "Unknown")), inline=True)
            await channel.send(embed=embed)
            entry["reminder_sent_at"] = now
            changed = True
            sent_count += 1
        if changed:
            set_stockpiles(guild.id, stockpiles)
    return sent_count


def _stockpile_log_key_from_entry(entry: dict[str, Any]) -> str:
    existing = str(entry.get("log_key", "")).strip()
    if existing:
        return existing

    basis = "|".join(
        [
            str(entry.get("code", "")).strip(),
            str(entry.get("name", "")).strip(),
            str(entry.get("location", "")).strip(),
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:12]
    generated = f"sp-{digest}"
    entry["log_key"] = generated
    return generated


def _stockpile_log_table_title(entry: dict[str, Any]) -> str:
    name = str(entry.get("name", "Unknown")).strip() or "Unknown"
    location = str(entry.get("location", "Unknown")).strip() or "Unknown"
    code = str(entry.get("code", "")).strip()
    base = f"{name} - {location}"
    return f"{base} [{code}]" if code else base


def _stockpile_log_timestamp_zulu(unix_ts: Optional[int] = None) -> str:
    timestamp = int(unix_ts if unix_ts is not None else time.time())
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%SZ")


def _stockpile_log_workbook_path(guild_id: int, war_number: str) -> str:
    output_dir = str(
        get_config_value("stockpile_log_output_dir", os.getenv("HESTIA_STOCKPILE_LOG_OUTPUT_DIR", STOCKPILE_LOG_DIR))
        or STOCKPILE_LOG_DIR
    ).strip()

    target_dir = output_dir or STOCKPILE_LOG_DIR
    os.makedirs(target_dir, exist_ok=True)

    safe_war = str(war_number or "Unknown").strip() or "Unknown"
    file_name = f"{safe_war} Stockpiles - Guild {guild_id}.xlsx"
    return os.path.join(target_dir, file_name)


def _stockpile_log_meta_value(raw: Any) -> str:
    return str(raw).strip() if raw is not None else ""


def _stockpile_log_find_or_create_sheet(wb: Any, title: str):
    if title in wb.sheetnames:
        return wb[title]
    return wb.create_sheet(title)


def _stockpile_log_ensure_meta_headers(meta_ws: Any) -> None:
    if _stockpile_log_meta_value(meta_ws.cell(row=1, column=1).value) != "table_key":
        meta_ws.cell(row=1, column=1, value="table_key")
    if _stockpile_log_meta_value(meta_ws.cell(row=1, column=2).value) != "sheet_name":
        meta_ws.cell(row=1, column=2, value="sheet_name")
    if _stockpile_log_meta_value(meta_ws.cell(row=1, column=3).value) != "display_title":
        meta_ws.cell(row=1, column=3, value="display_title")


def _stockpile_log_get_meta_sheet_map(meta_ws: Any) -> dict[str, tuple[str, str]]:
    key_map: dict[str, tuple[str, str]] = {}
    max_row = int(meta_ws.max_row or 1)
    for row in range(2, max_row + 1):
        key = _stockpile_log_meta_value(meta_ws.cell(row=row, column=1).value)
        sheet_name = _stockpile_log_meta_value(meta_ws.cell(row=row, column=2).value)
        display_title = _stockpile_log_meta_value(meta_ws.cell(row=row, column=3).value)
        if not key:
            continue
        if not sheet_name:
            continue
        key_map[key] = (sheet_name, display_title)
    return key_map


def _stockpile_log_set_meta_entry(meta_ws: Any, key: str, sheet_name: str, display_title: str) -> None:
    max_row = int(meta_ws.max_row or 1)
    for row in range(2, max_row + 1):
        existing_key = _stockpile_log_meta_value(meta_ws.cell(row=row, column=1).value)
        if existing_key != key:
            continue
        meta_ws.cell(row=row, column=2, value=sheet_name)
        meta_ws.cell(row=row, column=3, value=display_title)
        return

    new_row = max_row + 1
    meta_ws.cell(row=new_row, column=1, value=key)
    meta_ws.cell(row=new_row, column=2, value=sheet_name)
    meta_ws.cell(row=new_row, column=3, value=display_title)


def _stockpile_log_make_safe_sheet_name(title: str) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", " ", str(title or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "Stockpile"
    return cleaned[:31]


def _stockpile_log_unique_sheet_name(wb: Any, preferred_title: str) -> str:
    base = _stockpile_log_make_safe_sheet_name(preferred_title)
    if base not in wb.sheetnames:
        return base

    for suffix in range(2, 500):
        suffix_text = f" ({suffix})"
        candidate = f"{base[: max(1, 31 - len(suffix_text))]}{suffix_text}"
        if candidate not in wb.sheetnames:
            return candidate

    return f"SP-{int(time.time())}"[:31]


def _stockpile_log_move_sheet_to_front(wb: Any, ws: Any) -> None:
    try:
        while wb.worksheets and wb.worksheets[0] is not ws:
            offset = -wb.worksheets.index(ws)
            wb.move_sheet(ws, offset)
    except Exception:
        pass


def _stockpile_log_ensure_aggregate_sheet(wb: Any) -> Any:
    ws = _stockpile_log_find_or_create_sheet(wb, STOCKPILE_AGGREGATE_SHEET_NAME)
    _stockpile_log_move_sheet_to_front(wb, ws)
    return ws


def _stockpile_log_ensure_stockpile_sheet(wb: Any, meta_ws: Any, key: str, display_title: str) -> Any:
    sheet_map = _stockpile_log_get_meta_sheet_map(meta_ws)
    mapped_sheet_name, _ = sheet_map.get(key, ("", ""))

    target_ws = None
    if mapped_sheet_name and mapped_sheet_name in wb.sheetnames:
        target_ws = wb[mapped_sheet_name]

    if target_ws is None:
        desired_name = _stockpile_log_unique_sheet_name(wb, display_title)
        target_ws = wb.create_sheet(desired_name)
        _stockpile_log_set_meta_entry(meta_ws, key, desired_name, display_title)
        return target_ws

    desired_display_name = _stockpile_log_make_safe_sheet_name(display_title)
    if desired_display_name and target_ws.title != desired_display_name and desired_display_name not in wb.sheetnames:
        target_ws.title = desired_display_name
        _stockpile_log_set_meta_entry(meta_ws, key, desired_display_name, display_title)
    else:
        _stockpile_log_set_meta_entry(meta_ws, key, target_ws.title, display_title)

    return target_ws


def _stockpile_log_remove_legacy_combined_sheet(wb: Any) -> None:
    legacy_name = "Stockpile Log"
    if legacy_name in wb.sheetnames:
        try:
            del wb[legacy_name]
        except Exception:
            pass


def _stockpile_log_table_last_row(log_ws: Any, start_row: int) -> int:
    row = start_row + 2
    while row <= log_ws.max_row:
        item_value = _stockpile_log_meta_value(log_ws.cell(row=row, column=1).value)
        if not item_value:
            return row - 1
        row += 1
    return max(start_row + 1, log_ws.max_row)


def _stockpile_log_table_existing_items(log_ws: Any, start_row: int) -> list[str]:
    items: list[str] = []
    row = start_row + 2
    while row <= log_ws.max_row:
        item_name = _stockpile_log_meta_value(log_ws.cell(row=row, column=1).value)
        if not item_name:
            break
        items.append(item_name)
        row += 1
    return items


def _stockpile_log_ensure_single_table_header(log_ws: Any, title: str, start_row: int = 1) -> None:
    log_ws.cell(row=start_row, column=1, value=title)
    if _stockpile_log_meta_value(log_ws.cell(row=start_row + 1, column=1).value) != "Item":
        log_ws.cell(row=start_row + 1, column=1, value="Item")


def _stockpile_log_next_column(log_ws: Any, header_row: int) -> int:
    col = 2
    while col <= log_ws.max_column:
        value = _stockpile_log_meta_value(log_ws.cell(row=header_row, column=col).value)
        if not value:
            return col
        col += 1
    return max(2, log_ws.max_column + 1)


def _stockpile_log_append_snapshot(
    log_ws: Any,
    start_row: int,
    timestamp_label: str,
    item_totals: dict[str, float],
    known_item_names: Optional[set[str]] = None,
) -> None:
    existing_items = _stockpile_log_table_existing_items(log_ws, start_row)
    combined_item_names = set(existing_items)
    combined_item_names.update(item_totals.keys())
    if known_item_names:
        combined_item_names.update(name for name in known_item_names if str(name).strip())

    sorted_items = sorted(combined_item_names, key=lambda value: value.casefold())
    first_item_row = start_row + 2

    for idx, item_name in enumerate(sorted_items):
        log_ws.cell(row=first_item_row + idx, column=1, value=item_name)

    old_last_row = _stockpile_log_table_last_row(log_ws, start_row)
    new_last_row = first_item_row + len(sorted_items) - 1 if sorted_items else start_row + 1
    if old_last_row > new_last_row:
        for row in range(new_last_row + 1, old_last_row + 1):
            log_ws.cell(row=row, column=1, value=None)

    target_col = _stockpile_log_next_column(log_ws, start_row + 1)
    log_ws.cell(row=start_row + 1, column=target_col, value=timestamp_label)

    for idx, item_name in enumerate(sorted_items):
        qty = float(item_totals.get(item_name, 0.0))
        qty_value: float | int
        if qty.is_integer():
            qty_value = int(qty)
        else:
            qty_value = round(qty, 4)
        log_ws.cell(row=first_item_row + idx, column=target_col, value=qty_value)


def _stockpile_log_style_table(log_ws: Any, start_row: int) -> None:
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    title_fill = PatternFill(fill_type="solid", fgColor="101114")
    header_fill = PatternFill(fill_type="solid", fgColor="F0A66A")
    item_fill = PatternFill(fill_type="solid", fgColor="FDF3EA")
    thin = Side(style="thin", color="D0D7DE")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    last_row = _stockpile_log_table_last_row(log_ws, start_row)
    last_col = max(1, _stockpile_log_next_column(log_ws, start_row + 1) - 1)

    # Keep the title row as a single visual banner over the table width.
    for merged in list(log_ws.merged_cells.ranges):
        if merged.min_row == start_row and merged.max_row == start_row:
            log_ws.unmerge_cells(str(merged))
    if last_col > 1:
        log_ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=last_col)

    title_cell = log_ws.cell(row=start_row, column=1)
    title_cell.font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    title_cell.fill = title_fill
    title_cell.alignment = Alignment(horizontal="left", vertical="center")

    for col in range(1, last_col + 1):
        header_cell = log_ws.cell(row=start_row + 1, column=col)
        header_cell.font = Font(name="Calibri", size=10, bold=True, color="1F2937")
        header_cell.fill = header_fill
        header_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header_cell.border = border

        width = 42 if col == 1 else 23
        log_ws.column_dimensions[get_column_letter(col)].width = max(
            width,
            float(log_ws.column_dimensions[get_column_letter(col)].width or 0),
        )

    for row in range(start_row + 2, last_row + 1):
        item_cell = log_ws.cell(row=row, column=1)
        item_cell.font = Font(name="Calibri", size=10, bold=False, color="111827")
        item_cell.fill = item_fill
        item_cell.alignment = Alignment(horizontal="left", vertical="center")
        item_cell.border = border

        for col in range(2, last_col + 1):
            value_cell = log_ws.cell(row=row, column=col)
            value_cell.font = Font(name="Calibri", size=10, color="111827")
            value_cell.alignment = Alignment(horizontal="right", vertical="center")
            value_cell.border = border


def _extract_item_names_from_raw_stockpile_csv(raw_contents: str) -> set[str]:
    item_names: set[str] = set()
    raw_lines = (raw_contents or "").splitlines()
    if not raw_lines:
        return item_names

    groups: list[list[str]] = []
    current_group: list[str] = []
    for raw_line in raw_lines:
        if raw_line.strip():
            current_group.append(raw_line)
            continue
        if current_group:
            groups.append(current_group)
            current_group = []
    if current_group:
        groups.append(current_group)

    for group_lines in groups:
        reader = csv.reader(group_lines)
        for row in reader:
            cells = [cell.strip() for cell in row if cell.strip()]
            if len(cells) < 2:
                continue

            qty_idx: Optional[int] = None
            for idx, cell in enumerate(cells):
                parsed_qty = _parse_csv_quantity(cell)
                if parsed_qty is not None:
                    qty_idx = idx
                    break

            if qty_idx is None:
                continue

            item_parts = [cell for idx, cell in enumerate(cells) if idx != qty_idx]
            item_name = ", ".join(item_parts).strip()
            if item_name:
                item_names.add(item_name)

    return item_names


def log_stockpile_workbook_update(
    guild_id: int,
    stockpiles: list[dict[str, Any]],
    *,
    updated_stockpile_key: Optional[str],
    event_unix_ts: Optional[int] = None,
    extra_item_names: Optional[set[str]] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    try:
        from openpyxl import Workbook, load_workbook
    except Exception as exc:
        return False, None, f"openpyxl is unavailable: {exc}"

    try:
        war_number = str(get_war_number() or "Unknown").strip() or "Unknown"
        workbook_path = _stockpile_log_workbook_path(guild_id, war_number)

        if os.path.exists(workbook_path):
            wb = load_workbook(workbook_path)
        else:
            wb = Workbook()
            if wb.active is not None:
                wb.active.title = STOCKPILE_AGGREGATE_SHEET_NAME

        meta_ws = _stockpile_log_find_or_create_sheet(wb, STOCKPILE_LOG_META_SHEET_NAME)
        aggregate_ws = _stockpile_log_ensure_aggregate_sheet(wb)
        _stockpile_log_remove_legacy_combined_sheet(wb)
        if wb.active is meta_ws:
            wb.active = wb.index(aggregate_ws)
        meta_ws.sheet_state = "hidden"
        _stockpile_log_ensure_meta_headers(meta_ws)

        timestamp_label = _stockpile_log_timestamp_zulu(event_unix_ts)

        aggregate_totals: dict[str, float] = {}
        stockpile_by_key: dict[str, dict[str, Any]] = {}
        aggregate_known_items: set[str] = set(extra_item_names or set())

        for entry in stockpiles:
            key = _stockpile_log_key_from_entry(entry)
            stockpile_by_key[key] = entry

            entry_totals = _extract_stockpile_items_from_contents(str(entry.get("contents", "")))
            aggregate_known_items.update(entry_totals.keys())
            for item_name, qty in entry_totals.items():
                aggregate_totals[item_name] = aggregate_totals.get(item_name, 0.0) + qty

        aggregate_start_row = 1
        _stockpile_log_ensure_single_table_header(aggregate_ws, "Aggregate", start_row=aggregate_start_row)
        _stockpile_log_append_snapshot(
            aggregate_ws,
            aggregate_start_row,
            timestamp_label,
            aggregate_totals,
            known_item_names=aggregate_known_items,
        )
        _stockpile_log_style_table(aggregate_ws, aggregate_start_row)

        if updated_stockpile_key and updated_stockpile_key in stockpile_by_key:
            entry = stockpile_by_key[updated_stockpile_key]
            entry_title = _stockpile_log_table_title(entry)
            entry_ws = _stockpile_log_ensure_stockpile_sheet(
                wb,
                meta_ws,
                updated_stockpile_key,
                entry_title,
            )
            entry_start_row = 1
            _stockpile_log_ensure_single_table_header(entry_ws, entry_title, start_row=entry_start_row)
            entry_totals = _extract_stockpile_items_from_contents(str(entry.get("contents", "")))
            _stockpile_log_append_snapshot(
                entry_ws,
                entry_start_row,
                timestamp_label,
                entry_totals,
                known_item_names=set(extra_item_names or set()) | aggregate_known_items,
            )
            _stockpile_log_style_table(entry_ws, entry_start_row)

        _stockpile_log_move_sheet_to_front(wb, aggregate_ws)

        wb.save(workbook_path)
        return True, workbook_path, None
    except Exception as exc:
        return False, None, str(exc)


def export_current_stockpile_workbook_snapshot(
    guild_id: int,
    *,
    event_unix_ts: Optional[int] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    try:
        from openpyxl import Workbook, load_workbook
    except Exception as exc:
        return False, None, f"openpyxl is unavailable: {exc}"

    try:
        stockpiles = get_stockpiles(guild_id)
        war_number = str(get_war_number() or "Unknown").strip() or "Unknown"
        workbook_path = _stockpile_log_workbook_path(guild_id, war_number)

        if os.path.exists(workbook_path):
            wb = load_workbook(workbook_path)
        else:
            wb = Workbook()
            if wb.active is not None:
                wb.active.title = STOCKPILE_AGGREGATE_SHEET_NAME

        meta_ws = _stockpile_log_find_or_create_sheet(wb, STOCKPILE_LOG_META_SHEET_NAME)
        aggregate_ws = _stockpile_log_ensure_aggregate_sheet(wb)
        _stockpile_log_remove_legacy_combined_sheet(wb)
        if wb.active is meta_ws:
            wb.active = wb.index(aggregate_ws)
        meta_ws.sheet_state = "hidden"
        _stockpile_log_ensure_meta_headers(meta_ws)

        timestamp_label = _stockpile_log_timestamp_zulu(event_unix_ts)

        aggregate_totals: dict[str, float] = {}
        aggregate_known_items: set[str] = set()

        per_stockpile_totals: dict[str, dict[str, float]] = {}
        per_stockpile_titles: dict[str, str] = {}

        for entry in stockpiles:
            key = _stockpile_log_key_from_entry(entry)
            totals = _extract_stockpile_items_from_contents(str(entry.get("contents", "")))
            per_stockpile_totals[key] = totals
            per_stockpile_titles[key] = _stockpile_log_table_title(entry)

            aggregate_known_items.update(totals.keys())
            for item_name, qty in totals.items():
                aggregate_totals[item_name] = aggregate_totals.get(item_name, 0.0) + qty

        aggregate_start_row = 1
        _stockpile_log_ensure_single_table_header(aggregate_ws, "Aggregate", start_row=aggregate_start_row)
        _stockpile_log_append_snapshot(
            aggregate_ws,
            aggregate_start_row,
            timestamp_label,
            aggregate_totals,
            known_item_names=aggregate_known_items,
        )
        _stockpile_log_style_table(aggregate_ws, aggregate_start_row)

        for key, totals in per_stockpile_totals.items():
            title = per_stockpile_titles.get(key, key)
            stockpile_ws = _stockpile_log_ensure_stockpile_sheet(wb, meta_ws, key, title)
            start_row = 1
            _stockpile_log_ensure_single_table_header(stockpile_ws, title, start_row=start_row)
            _stockpile_log_append_snapshot(
                stockpile_ws,
                start_row,
                timestamp_label,
                totals,
                known_item_names=aggregate_known_items,
            )
            _stockpile_log_style_table(stockpile_ws, start_row)

        _stockpile_log_move_sheet_to_front(wb, aggregate_ws)

        wb.save(workbook_path)
        return True, workbook_path, None
    except Exception as exc:
        return False, None, str(exc)

async def _defer_ephemeral_interaction(interaction: discord.Interaction) -> bool:
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.HTTPException as e:
        if getattr(e, "code", None) == 10062:
            return False
        raise
    return True


@app_commands.command(name="stockpile-add", description="Add a stockpile entry to the bot database")
@app_commands.describe(
    stockpile_name="Name: Name of your stockpile",
    stockpile_code="Code: Code of your stockpile",
    hex_region="Hex;Region: location information",
    kind="Kind: Specify if the stockpile is in a Seaport, Storage Depot, or Hangar",
    created_by="Optional: Select the stockpile creator",
)
@app_commands.choices(
    kind=[
        app_commands.Choice(name="Seaport", value="Seaport"),
        app_commands.Choice(name="Storage Depot", value="Storage Depot"),
        app_commands.Choice(name="Hangar", value="Hangar"),
    ]
)
@app_commands.autocomplete(hex_region=stockpile_location_autocomplete)
async def stockpile_add(
    interaction: discord.Interaction,
    stockpile_name: str,
    stockpile_code: str,
    hex_region: str,
    kind: app_commands.Choice[str],
    created_by: Optional[discord.Member] = None,
):
    if not await _defer_ephemeral_interaction(interaction):
        return

    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    verified_role_id = get_default_stockpile_role_id(interaction.guild)
    if verified_role_id not in {role.id for role in interaction.user.roles}:
        await interaction.followup.send(
            "You must have the verified role to use this command.",
            ephemeral=True,
        )
        return

    name_value = stockpile_name.strip()
    code_value = stockpile_code.strip()
    location_value = hex_region.strip()

    if not name_value or not code_value or not location_value:
        await interaction.followup.send(
            "Name, Code, and Hex;Region cannot be empty.",
            ephemeral=True,
        )
        return

    stockpiles = get_stockpiles(interaction.guild.id)
    normalized_name = name_value.casefold()
    normalized_code = code_value.casefold()

    for entry in stockpiles:
        if str(entry.get("name", "")).casefold() == normalized_name:
            await interaction.followup.send(
                "A stockpile with this name already exists.",
                ephemeral=True,
            )
            return
        if str(entry.get("code", "")).casefold() == normalized_code:
            await interaction.followup.send(
                "A stockpile with this code already exists.",
                ephemeral=True,
            )
            return

    stockpiles.append(
        {
            "name": name_value,
            "code": code_value,
            "location": location_value,
            "kind": kind.value,
            "contents": "",
            "accessible_to": str(verified_role_id),
            "last_updated": None,
            "added_by": str(interaction.user.id),
            "creator_id": str((created_by or interaction.user).id),
            "creator_name": str(created_by or interaction.user),
            "created_at": int(time.time()),
            "reminder_reset_at": int(time.time()),
            "reminder_sent_at": 0,
            "log_key": f"sp-{hashlib.sha1(f'{interaction.guild.id}|{name_value}|{code_value}|{location_value}'.encode('utf-8', errors='ignore')).hexdigest()[:12]}",
        }
    )

    if not set_stockpiles(interaction.guild.id, stockpiles):
        await interaction.followup.send("Failed to save stockpile entry.", ephemeral=True)
        return

    added_key = _stockpile_log_key_from_entry(stockpiles[-1])
    log_ok, log_path, log_error = log_stockpile_workbook_update(
        interaction.guild.id,
        stockpiles,
        updated_stockpile_key=added_key,
    )

    log_suffix = ""
    if log_ok and log_path:
        log_suffix = f"\nLog updated: **{os.path.basename(log_path)}**"
    elif log_error:
        log_suffix = f"\nStockpile log update failed: `{log_error}`"

    await interaction.followup.send(
        (
            "Stockpile added successfully.\n"
            f"Name: **{name_value}**\n"
            f"Code: **{code_value}**\n"
            f"Hex;Region: **{location_value}**\n"
            f"Kind: **{kind.value}**\n"
            f"Accessible to role: <@&{verified_role_id}>"
            f"{log_suffix}"
        ),
        ephemeral=True,
    )


async def stockpile_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return []

    caller_role_ids = {role.id for role in interaction.user.roles}
    stockpiles = get_stockpiles(interaction.guild.id)
    if not stockpiles:
        return []

    search = current.lower().strip()
    choices: list[app_commands.Choice[str]] = []

    for entry in stockpiles:
        if not (_stockpile_access_role_ids(entry) & caller_role_ids):
            continue

        name = str(entry.get("name", "Unknown"))
        if search and search not in name.lower():
            continue

        choices.append(app_commands.Choice(name=name, value=name))
        if len(choices) >= 25:
            break

    return choices


async def stockpile_name_admin_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return []

    if interaction.guild is None or not has_stockpile_admin_access(interaction.user, interaction.guild):
        return []

    stockpiles = get_stockpiles(interaction.guild.id)
    if not stockpiles:
        return []

    search = current.lower().strip()
    choices: list[app_commands.Choice[str]] = []

    for entry in stockpiles:
        name = str(entry.get("name", "Unknown"))
        if search and search not in name.lower():
            continue

        choices.append(app_commands.Choice(name=name, value=name))
        if len(choices) >= 25:
            break

    return choices


def _stockpile_last_updated_footer_value(last_updated_raw: Any) -> str:
    if last_updated_raw in (None, "", "Never"):
        return "Never"

    if isinstance(last_updated_raw, (int, float)):
        return f"<t:{int(last_updated_raw)}:F>"

    raw_value = str(last_updated_raw).strip()
    if not raw_value:
        return "Never"

    if raw_value.isdigit():
        return f"<t:{int(raw_value)}:F>"

    return "Never"


def _stockpile_access_role_id(raw_value: Any) -> int:
    if isinstance(raw_value, int):
        return raw_value

    text = str(raw_value or "").strip()
    if not text:
        return VERIFIED_ROLE_ID

    # Support plain IDs and mention-like values (e.g. <@&1234567890>).
    digit_groups = re.findall(r"\d+", text)
    if digit_groups:
        try:
            return int(digit_groups[-1])
        except ValueError:
            pass

    return VERIFIED_ROLE_ID


def _stockpile_access_role_ids(entry: dict[str, Any]) -> set[int]:
    role_ids = {
        _stockpile_access_role_id(entry.get("accessible_to", VERIFIED_ROLE_ID)),
    }
    secondary_raw = entry.get("accessible_to_secondary")
    if secondary_raw not in (None, "", 0):
        role_ids.add(_stockpile_access_role_id(secondary_raw))
    return role_ids


def _stockpile_access_role_display(entry: dict[str, Any], guild: discord.Guild) -> str:
    display_parts: list[str] = []
    for role_id in sorted(_stockpile_access_role_ids(entry)):
        role = guild.get_role(role_id)
        display_parts.append(role.name if role is not None else f"Role {role_id}")
    return " / ".join(display_parts)


def _parse_csv_quantity(value: str) -> Optional[float]:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_stockpile_contents_with_shares(item_totals: dict[str, float]) -> str:
    if not item_totals:
        return ""

    sorted_items = sorted(item_totals.items(), key=lambda entry: (-entry[1], entry[0].casefold()))
    total_qty = sum(item_totals.values())
    if total_qty <= 0:
        return ""

    formatted_lines: list[str] = []
    for item_idx, (item_name, qty) in enumerate(sorted_items, start=1):
        if qty.is_integer():
            qty_text = str(int(qty))
        else:
            qty_text = f"{qty:.2f}".rstrip("0").rstrip(".")
        share_percent = (qty / total_qty) * 100
        formatted_lines.append(f"{item_idx}. [{qty_text}] {item_name} - {share_percent:.2f}%")

    return "\n".join(formatted_lines)


STOCKPILE_CATEGORY_LABELS = [
    "Items (Crates)",
    "Vehicles (Singles)",
    "Vehicles (Crates)",
    "Equipment (Singles)",
    "Equipment (Crates)",
]

STOCKPILE_ITEM_CRATE_EXCLUSION_LABELS = {
    "petrol (crate)",
    "diesel (crate)",
    "processed construction materials (crate)",
    "steel construction materials (crate)",
    "assembly materials i (crate)",
    "assembly materials ii (crate)",
    "assembly materials iii (crate)",
    "assembly materials iv (crate)",
    "assembly materials v (crate)",
    "rare alloys (crate)",
    "heavy oil (crate)",
    "enriched oil (crate)",
    "oil (crate)",
    "pipe (crate)",
    "aluminum alloy (crate)",
    "basic materials (crate)",
    "copper alloy (crate)",
    "explosive powder (crate)",
    "construction materials (crate)",
    "rare materials (crate)",
    "gravel (crate)",
    "heavy explosive powder (crate)",
    "iron alloy (crate)",
    "maintenance supplies (crate)",
    "rare metal (crate)",
    "relic materials (crate)",
    "refined materials (crate)",
}


def _stockpile_total_from_group_items(group_items: dict[str, float]) -> float:
    return sum(float(qty) for qty in group_items.values())


def _stockpile_totals_from_contents(contents: str) -> dict[str, float]:
    group_items = _extract_stockpile_group_items_from_contents(contents)
    grouped_map = {label.casefold(): items for label, items in group_items.items()}
    item_crates = grouped_map.get("items (crates)", {})
    reduced_item_crates = sum(
        float(quantity)
        for item_name, quantity in item_crates.items()
        if item_name.strip().casefold() not in STOCKPILE_ITEM_CRATE_EXCLUSION_LABELS
    )

    return {
        "Total Item Crates": _stockpile_total_from_group_items(item_crates),
        "Total Item Crates (excluding listed materials)": reduced_item_crates,
        "Total Vehicle Crates": _stockpile_total_from_group_items(grouped_map.get("vehicles (crates)", {})),
        "Total vehicles": _stockpile_total_from_group_items(grouped_map.get("vehicles (singles)", {})),
    }


def _format_stockpile_total_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _extract_stockpile_items_from_contents(contents: str) -> dict[str, float]:
    items: dict[str, float] = {}

    for raw_line in (contents or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # New format: - Item Name [123] - 45.67%
        new_match = re.match(r"^- (.+?) \[([0-9]+(?:\.[0-9]+)?)\](?: - [0-9]+(?:\.[0-9]+)?%)?$", line)
        if new_match:
            item_name = new_match.group(1).strip()
            qty = float(new_match.group(2))
            if qty >= 1:
                items[item_name] = items.get(item_name, 0) + qty
            continue

        # Legacy format: - Item Name: 123
        legacy_match = re.match(r"^- (.+?):\s*([0-9]+(?:\.[0-9]+)?)$", line)
        if legacy_match:
            item_name = legacy_match.group(1).strip()
            qty = float(legacy_match.group(2))
            if qty >= 1:
                items[item_name] = items.get(item_name, 0) + qty

        # Numbered format (old): 1. Item Name [123] - 45.67%
        numbered_match = re.match(r"^[0-9]+\.\s+(.+?)\s\[([0-9]+(?:\.[0-9]+)?)\](?:\s-\s[0-9]+(?:\.[0-9]+)?%)?$", line)
        if numbered_match:
            item_name = numbered_match.group(1).strip()
            qty = float(numbered_match.group(2))
            if qty >= 1:
                items[item_name] = items.get(item_name, 0) + qty
            continue

        # Numbered format (new): 1. [123] Item Name - 45.67%
        qty_first_match = re.match(r"^[0-9]+\.\s+\[([0-9]+(?:\.[0-9]+)?)\]\s+(.+?)(?:\s-\s[0-9]+(?:\.[0-9]+)?%)?$", line)
        if qty_first_match:
            qty = float(qty_first_match.group(1))
            item_name = qty_first_match.group(2).strip()
            if qty >= 1 and item_name:
                items[item_name] = items.get(item_name, 0) + qty

    return items


def _extract_stockpile_group_items_from_contents(contents: str) -> dict[str, dict[str, float]]:
    groups: dict[str, dict[str, float]] = {}
    current_label = "Uncategorized"

    for raw_line in (contents or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        qty_first_match = re.match(r"^[0-9]+\.\s+\[([0-9]+(?:\.[0-9]+)?)\]\s+(.+?)(?:\s-\s[0-9]+(?:\.[0-9]+)?%)?$", line)
        legacy_numbered_match = re.match(r"^[0-9]+\.\s+(.+?)\s\[([0-9]+(?:\.[0-9]+)?)\](?:\s-\s[0-9]+(?:\.[0-9]+)?%)?$", line)

        if qty_first_match:
            qty = float(qty_first_match.group(1))
            item_name = qty_first_match.group(2).strip()
            if qty >= 1 and item_name:
                groups.setdefault(current_label, {})[item_name] = groups.setdefault(current_label, {}).get(item_name, 0.0) + qty
            continue

        if legacy_numbered_match:
            item_name = legacy_numbered_match.group(1).strip()
            qty = float(legacy_numbered_match.group(2))
            if qty >= 1 and item_name:
                groups.setdefault(current_label, {})[item_name] = groups.setdefault(current_label, {}).get(item_name, 0.0) + qty
            continue

        # Non-numbered line is treated as a category/header line.
        current_label = line
        groups.setdefault(current_label, {})

    return {label: items for label, items in groups.items() if items}


def _condense_items_for_chart(item_totals: dict[str, float], max_items: int = 20) -> list[tuple[str, float]]:
    if not item_totals:
        return []

    sorted_items = sorted(item_totals.items(), key=lambda entry: (-entry[1], entry[0].casefold()))
    if len(sorted_items) <= max_items:
        return sorted_items

    top_items = sorted_items[: max_items - 1]
    other_total = sum(qty for _, qty in sorted_items[max_items - 1 :])
    if other_total > 0:
        top_items.append(("Other", other_total))
    return top_items


def _draw_stockpile_pie_chart(pie_ax: Any, legend_ax: Any, item_totals: dict[str, float], title: str) -> None:
    condensed_items = _condense_items_for_chart(item_totals, max_items=20)
    if not condensed_items:
        pie_ax.axis("off")
        pie_ax.text(0.5, 0.5, "No items", ha="center", va="center", fontsize=10)
        pie_ax.set_title(title, fontsize=11)
        legend_ax.axis("off")
        return

    values = [qty for _, qty in condensed_items]
    total_qty = sum(values)

    def autopct_if_large(pct: float) -> str:
        return f"{pct:.1f}%" if pct >= 4.0 else ""

    wedges, _, _ = pie_ax.pie(
        values,
        labels=None,
        autopct=autopct_if_large,
        startangle=90,
        pctdistance=0.78,
        textprops={"fontsize": 8},
        wedgeprops={"linewidth": 0.5, "edgecolor": "white"},
    )
    pie_ax.axis("equal")
    pie_ax.set_title(title, fontsize=11)

    legend_labels: list[str] = []
    for name, qty in condensed_items:
        share = (qty / total_qty) * 100 if total_qty > 0 else 0.0
        qty_text = str(int(qty)) if float(qty).is_integer() else f"{qty:.2f}".rstrip("0").rstrip(".")
        legend_labels.append(f"{name} [{qty_text}] - {share:.1f}%")

    legend_ax.axis("off")
    legend_ax.legend(
        wedges,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        fontsize=7,
        frameon=False,
        ncol=1,
        borderaxespad=0.0,
    )


def _build_stockpile_pie_chart_bytes(contents: str, stockpile_name: Optional[str] = None, location: Optional[str] = None) -> Optional[bytes]:
    plt = _get_matplotlib_pyplot()
    if plt is None:
        return None

    group_items = _extract_stockpile_group_items_from_contents(contents)
    if not group_items:
        return None

    vehicle_singles_items = group_items.get("Vehicles (Singles)", {})
    rest_items: dict[str, float] = {}
    for label, items in group_items.items():
        if label == "Vehicles (Singles)":
            continue
        for item_name, qty in items.items():
            rest_items[item_name] = rest_items.get(item_name, 0.0) + qty

    fig = plt.figure(figsize=(18, 11), dpi=160, constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=[3.3, 2.0], wspace=0.25, hspace=0.08)

    pie_ax_left = fig.add_subplot(grid[0, 0])
    pie_ax_right = fig.add_subplot(grid[0, 1])
    legend_ax_left = fig.add_subplot(grid[1, 0])
    legend_ax_right = fig.add_subplot(grid[1, 1])

    _draw_stockpile_pie_chart(pie_ax_left, legend_ax_left, rest_items, "All Other Categories")
    _draw_stockpile_pie_chart(pie_ax_right, legend_ax_right, vehicle_singles_items, "Vehicles (Singles)")

    # Build title with stockpile name and location if provided
    if stockpile_name and location:
        title = f"[{BRAND_NAME}] Stockpile overview - {stockpile_name}, {location}"
    elif stockpile_name:
        title = f"[{BRAND_NAME}] Stockpile overview - {stockpile_name}"
    else:
        title = f"[{BRAND_NAME}] Stockpile overview"
    fig.suptitle(title, fontsize=14)

    image_buffer = io.BytesIO()
    fig.savefig(image_buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    image_buffer.seek(0)
    return image_buffer.getvalue()


def _parse_stockpile_contents_csv(raw_contents: str) -> str:
    raw_lines = (raw_contents or "").splitlines()
    if not raw_lines:
        return ""

    groups: list[list[str]] = []
    current_group: list[str] = []
    for raw_line in raw_lines:
        if raw_line.strip():
            current_group.append(raw_line)
            continue
        if current_group:
            groups.append(current_group)
            current_group = []
    if current_group:
        groups.append(current_group)

    if not groups:
        return ""

    formatted_groups: list[str] = []
    multi_group = len(groups) > 1

    for group_index, group_lines in enumerate(groups, start=1):
        item_totals: dict[str, float] = {}
        reader = csv.reader(group_lines)

        for row in reader:
            cells = [cell.strip() for cell in row if cell.strip()]
            if len(cells) < 2:
                continue

            qty: Optional[float] = None
            qty_idx: Optional[int] = None
            for idx, cell in enumerate(cells):
                parsed_qty = _parse_csv_quantity(cell)
                if parsed_qty is not None:
                    qty = parsed_qty
                    qty_idx = idx
                    break

            if qty is None or qty < 1 or qty_idx is None:
                continue

            item_parts = [cell for idx, cell in enumerate(cells) if idx != qty_idx]
            item_name = ", ".join(item_parts).strip()
            if not item_name:
                continue

            existing_total = item_totals.get(item_name, 0)
            item_totals[item_name] = existing_total + qty

        if not item_totals:
            continue

        sorted_items = sorted(item_totals.items(), key=lambda entry: (-entry[1], entry[0].casefold()))
        group_total = sum(item_totals.values())
        lines: list[str] = []
        if group_index <= len(STOCKPILE_CATEGORY_LABELS):
            lines.append(STOCKPILE_CATEGORY_LABELS[group_index - 1])
        elif multi_group:
            lines.append(f"Category {group_index}")

        for item_idx, (item_name, qty) in enumerate(sorted_items, start=1):
            qty_text = str(int(qty)) if float(qty).is_integer() else f"{qty:.2f}".rstrip("0").rstrip(".")
            share_percent = (qty / group_total) * 100 if group_total > 0 else 0.0
            lines.append(f"{item_idx}. [{qty_text}] {item_name} - {share_percent:.2f}%")

        formatted_groups.append("\n".join(lines))

    return "\n\n".join(formatted_groups)


async def _read_stockpile_contents_upload(upload: discord.Attachment) -> str:
    filename = (upload.filename or "").lower()
    if not (filename.endswith(".txt") or filename.endswith(".csv")):
        raise ValueError("Only .txt or .csv uploads are supported for stockpile contents.")

    # Keep parsing bounded for responsiveness and to avoid abuse.
    max_bytes = 2 * 1024 * 1024
    if upload.size is not None and upload.size > max_bytes:
        raise ValueError("Uploaded file is too large. Maximum size is 2 MB.")

    data = await upload.read()
    if len(data) > max_bytes:
        raise ValueError("Uploaded file is too large. Maximum size is 2 MB.")

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError("Could not decode uploaded file. Please upload UTF-8 text.")


def _build_stockpile_embed(entry: dict[str, Any], guild: discord.Guild) -> discord.Embed:
    name = str(entry.get("name", "Unknown"))
    raw_contents = str(entry.get("contents", "")).strip()
    contents = raw_contents or "No contents set."
    details_suffix = f"\n\n-# Last updated: {_stockpile_last_updated_footer_value(entry.get('last_updated'))}"

    summary_lines = []
    if raw_contents:
        totals = _stockpile_totals_from_contents(raw_contents)
        summary_lines.extend(
            [
                f"**Total Item Crates:** {_format_stockpile_total_value(totals.get('Total Item Crates', 0.0))} ({_format_stockpile_total_value(totals.get('Total Item Crates (excluding listed materials)', 0.0))})",
                f"**Total Vehicle Crates:** {_format_stockpile_total_value(totals.get('Total Vehicle Crates', 0.0))}",
                f"**Total vehicles:** {_format_stockpile_total_value(totals.get('Total vehicles', 0.0))}",
            ]
        )

    max_contents_len = 3900 - len("## Contents\n") - len(details_suffix)
    if len(contents) > max_contents_len:
        contents = contents[: max_contents_len - 24].rstrip() + "\n... (truncated)"
    location = str(entry.get("location", "Unknown"))
    code = str(entry.get("code", "Unknown"))
    access_display = _stockpile_access_role_display(entry, guild)
    creator_id = str(entry.get("creator_id") or entry.get("added_by") or "")
    creator = guild.get_member(int(creator_id)) if creator_id.isdigit() else None
    creator_display = creator.mention if creator is not None else str(entry.get("creator_name", "Unknown"))

    description_sections = []
    if summary_lines:
        description_sections.append("## Totals\n" + "\n".join(f"- {line}" for line in summary_lines))
    description_sections.append(f"## Contents\n{contents}")
    description = "\n\n".join(description_sections) + details_suffix

    embed = discord.Embed(
        title=f"# {name}",
        description=description,
        color=BRAND_COLOR,
    )
    embed.set_author(name=f"{BRAND_NAME} Stockpiles")
    embed.set_footer(
        text=(
            f"{location} ; {code} ; {access_display}"
        )
    )
    embed.add_field(name="Created by", value=creator_display, inline=True)
    return embed


class StockpileResetReminderButton(discord.ui.Button):
    def __init__(self, entry_index: int):
        super().__init__(label="Reset reminder timer", style=discord.ButtonStyle.danger, row=1)
        self.entry_index = entry_index

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, StockpileSelectView):
            await interaction.response.send_message("This control is no longer active.", ephemeral=True)
            return
        entry = view.entries[self.entry_index]
        creator_id = str(entry.get("creator_id") or entry.get("added_by") or "")
        allowed = creator_id == str(interaction.user.id)
        if interaction.guild is not None and isinstance(interaction.user, discord.Member):
            allowed = allowed or has_stockpile_admin_access(interaction.user, interaction.guild)
        if not allowed:
            await interaction.response.send_message("Only the stockpile creator or staff can reset this timer.", ephemeral=True)
            return
        now = int(time.time())
        entry["reminder_reset_at"] = now
        entry["reminder_sent_at"] = 0
        if interaction.guild is None or not set_stockpiles(interaction.guild.id, view.entries):
            await interaction.response.send_message("Failed to reset the reminder timer.", ephemeral=True)
            return
        await interaction.response.send_message("The stockpile reminder timer was reset for another 36 hours.", ephemeral=True)
        self.disabled = True


def _build_stockpile_overview_embeds(entries: list[dict[str, Any]]) -> list[discord.Embed]:
    embeds: list[discord.Embed] = []
    current_blocks: list[str] = []
    current_len = 0
    max_desc_len = 3800

    for idx, entry in enumerate(entries, start=1):
        name = str(entry.get("name", "Unknown"))
        location = str(entry.get("location", "Unknown"))
        code = str(entry.get("code", "Unknown"))
        kind = str(entry.get("kind", "Unknown"))
        access_role_mentions = ", ".join(f"<@&{role_id}>" for role_id in sorted(_stockpile_access_role_ids(entry)))
        updated = _stockpile_last_updated_footer_value(entry.get("last_updated"))

        block = (
            f"__**{idx}. {name}**__ | Location: {location}\n"
            f"Type: {kind} | Code: `{code}`\n"
            f"Access: {access_role_mentions} | Updated: {updated}"
        )

        add_len = len(block) + (2 if current_blocks else 0)
        if current_blocks and current_len + add_len > max_desc_len:
            embed = discord.Embed(
                title="Stockpile Directory",
                description="\n\n".join(current_blocks),
                color=BRAND_COLOR,
            )
            embed.set_author(name=f"{BRAND_NAME} Stockpiles")
            embeds.append(embed)
            current_blocks = [block]
            current_len = len(block)
        else:
            current_blocks.append(block)
            current_len += add_len

    if current_blocks:
        embed = discord.Embed(
            title="Stockpile Directory",
            description="\n\n".join(current_blocks),
            color=BRAND_COLOR,
        )
        embed.set_author(name=f"{BRAND_NAME} Stockpiles")
        embeds.append(embed)

    total_pages = max(1, len(embeds))
    for page_idx, embed in enumerate(embeds, start=1):
        embed.set_footer(text=f"Showing visible stockpiles only | Page {page_idx}/{total_pages}")

    return embeds


def _build_stockpile_showcase(
    entry: dict[str, Any],
    guild: discord.Guild,
) -> tuple[discord.Embed, Optional[discord.File]]:
    embed = _build_stockpile_embed(entry, guild)
    contents = str(entry.get("contents", "")).strip()
    name = str(entry.get("name", ""))
    location = str(entry.get("location", ""))
    chart_bytes = _build_stockpile_pie_chart_bytes(contents, name, location)
    if chart_bytes is None:
        return embed, None

    embed.set_image(url="attachment://stockpile_pie.png")
    chart_file = discord.File(io.BytesIO(chart_bytes), filename="stockpile_pie.png")
    return embed, chart_file


class StockpileSelect(discord.ui.Select):
    def __init__(self, entries: list[dict[str, Any]]):
        options: list[discord.SelectOption] = []
        for idx, entry in enumerate(entries[:25]):
            name = str(entry.get("name", "Unknown"))
            location = str(entry.get("location", "Unknown"))
            description = location[:100] if location else "No location"
            options.append(discord.SelectOption(label=name[:100], description=description, value=str(idx)))

        super().__init__(
            placeholder="Select a stockpile",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.entries = entries

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except discord.HTTPException as e:
            if getattr(e, "code", None) == 10062:
                return
            raise

        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return

        selected_index = int(self.values[0])
        if selected_index < 0 or selected_index >= len(self.entries):
            await interaction.followup.send("Invalid stockpile selection.", ephemeral=True)
            return

        if isinstance(self.view, StockpileSelectView):
            self.view.show_return_button()
            self.view.show_aggregate_button()
            self.view.show_reset_button(selected_index)

        selected_entry = self.entries[selected_index]
        embed = _build_stockpile_embed(selected_entry, interaction.guild)
        contents = str(selected_entry.get("contents", "")).strip()
        name = str(selected_entry.get("name", ""))
        location = str(selected_entry.get("location", ""))
        chart_bytes = await asyncio.to_thread(_build_stockpile_pie_chart_bytes, contents, name, location)
        chart_file = None
        if chart_bytes is not None:
            embed.set_image(url="attachment://stockpile_pie.png")
            chart_file = discord.File(io.BytesIO(chart_bytes), filename="stockpile_pie.png")

        attachments = [chart_file] if chart_file is not None else []
        await interaction.edit_original_response(
            content=None,
            embed=embed,
            view=self.view,
            attachments=attachments,
        )


class ReturnToListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Return To List View", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, StockpileSelectView):
            await interaction.response.send_message("This control is no longer active.", ephemeral=True)
            return

        if not view.overview_embeds:
            await interaction.response.send_message("No overview is available for this view.", ephemeral=True)
            return

        view.hide_return_button()
        view.show_aggregate_button()
        await interaction.response.edit_message(
            content="Stockpile overview. Use the dropdown below for a refined view.",
            embed=view.overview_embeds[0],
            view=view,
            attachments=[],
        )


class ShowAggregateChartButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Show Aggregate Chart", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, StockpileSelectView):
            await interaction.response.send_message("This control is no longer active.", ephemeral=True)
            return

        aggregate_contents = "\n\n".join(
            str(entry.get("contents", "")).strip()
            for entry in view.entries
            if str(entry.get("contents", "")).strip()
        )

        if not aggregate_contents:
            await interaction.response.send_message(
                "No aggregate chart can be generated because no stockpile contents are available.",
                ephemeral=True,
            )
            return

        try:
            await interaction.response.defer()
        except discord.HTTPException as e:
            if getattr(e, "code", None) == 10062:
                return
            raise

        chart_bytes = await asyncio.to_thread(
            _build_stockpile_pie_chart_bytes,
            aggregate_contents,
            "All Stockpiles",
            None,
        )

        if chart_bytes is None:
            await interaction.followup.send(
                "Could not generate aggregate chart right now.",
                ephemeral=True,
            )
            return

        view.show_return_button()
        view.hide_aggregate_button()

        aggregate_embed = discord.Embed(
            title="Aggregate Stockpile Summary",
            description="Combined contents across all visible stockpiles",
            color=BRAND_COLOR,
        )
        aggregate_embed.set_image(url="attachment://stockpile_aggregate_pie.png")
        aggregate_file = discord.File(io.BytesIO(chart_bytes), filename="stockpile_aggregate_pie.png")

        await interaction.edit_original_response(
            content=None,
            embed=aggregate_embed,
            view=view,
            attachments=[aggregate_file],
        )


class StockpileSelectView(discord.ui.View):
    def __init__(self, author_id: int, entries: list[dict[str, Any]], overview_embeds: list[discord.Embed]):
        super().__init__(timeout=60 * 10)
        self.author_id = author_id
        self.entries = entries
        self.overview_embeds = overview_embeds
        self.return_button: Optional[ReturnToListButton] = None
        self.aggregate_button: Optional[ShowAggregateChartButton] = ShowAggregateChartButton()
        self.reset_button: Optional[StockpileResetReminderButton] = None
        self.add_item(StockpileSelect(entries))
        self.add_item(self.aggregate_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command caller can use this selector.",
                ephemeral=True,
            )
            return False
        return True

    def show_return_button(self) -> None:
        if self.return_button is not None:
            return
        self.return_button = ReturnToListButton()
        self.add_item(self.return_button)

    def show_reset_button(self, entry_index: int) -> None:
        if self.reset_button is not None:
            self.remove_item(self.reset_button)
        self.reset_button = StockpileResetReminderButton(entry_index)
        self.add_item(self.reset_button)

    def hide_return_button(self) -> None:
        if self.return_button is None:
            return
        self.remove_item(self.return_button)
        self.return_button = None

    def show_aggregate_button(self) -> None:
        if self.aggregate_button is None:
            self.aggregate_button = ShowAggregateChartButton()
            self.add_item(self.aggregate_button)
            return
        if self.aggregate_button not in self.children:
            self.add_item(self.aggregate_button)

    def hide_aggregate_button(self) -> None:
        if self.aggregate_button is None:
            return
        if self.aggregate_button in self.children:
            self.remove_item(self.aggregate_button)


@app_commands.command(name="stockpile-edit", description="Edit an existing stockpile entry")
@app_commands.describe(
    stockpile_name="Name: Name of your stockpile",
    stockpile_code="Optional: New code of your stockpile",
    hex_region="Optional: New Hex;Region location information",
    kind="Optional: New kind (Seaport/Storage Depot/Hangar)",
    accessible_to="Optional: New role that can see this stockpile",
    accessible_to_secondary="Optional: Second role that can also see this stockpile",
    created_by="Optional: Stockpile creator",
)
@app_commands.autocomplete(stockpile_name=stockpile_name_admin_autocomplete, hex_region=stockpile_location_autocomplete)
@app_commands.choices(
    kind=[
        app_commands.Choice(name="Seaport", value="Seaport"),
        app_commands.Choice(name="Storage Depot", value="Storage Depot"),
        app_commands.Choice(name="Hangar", value="Hangar"),
    ]
)
async def stockpile_edit(
    interaction: discord.Interaction,
    stockpile_name: str,
    stockpile_code: Optional[str] = None,
    hex_region: Optional[str] = None,
    kind: Optional[app_commands.Choice[str]] = None,
    accessible_to: Optional[discord.Role] = None,
    accessible_to_secondary: Optional[discord.Role] = None,
    created_by: Optional[discord.Member] = None,
):
    if not await _defer_ephemeral_interaction(interaction):
        return

    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    if interaction.guild is None or not has_stockpile_admin_access(interaction.user, interaction.guild):
        await interaction.followup.send(
            "Only server administrators or the Staff role can use this command.",
            ephemeral=True,
        )
        return

    name_value = stockpile_name.strip()
    if not name_value:
        await interaction.followup.send("Stockpile name cannot be empty.", ephemeral=True)
        return

    stockpiles = get_stockpiles(interaction.guild.id)
    target_index: Optional[int] = None
    normalized_name = name_value.casefold()

    for idx, entry in enumerate(stockpiles):
        if str(entry.get("name", "")).casefold() == normalized_name:
            target_index = idx
            break

    if target_index is None:
        await interaction.followup.send(
            "No stockpile with that name exists.",
            ephemeral=True,
        )
        return

    current_entry = stockpiles[target_index]
    current_code = str(current_entry.get("code", ""))
    current_location = str(current_entry.get("location", ""))
    current_kind = str(current_entry.get("kind", "Seaport"))
    current_access = _stockpile_access_role_id(current_entry.get("accessible_to", VERIFIED_ROLE_ID))
    current_secondary_raw = current_entry.get("accessible_to_secondary")
    current_secondary_access = (
        _stockpile_access_role_id(current_secondary_raw)
        if current_secondary_raw not in (None, "", 0)
        else None
    )
    current_creator_id = str(current_entry.get("creator_id") or current_entry.get("added_by") or "")

    if stockpile_code is not None:
        updated_code = stockpile_code.strip()
        if not updated_code:
            await interaction.followup.send("Code cannot be empty when provided.", ephemeral=True)
            return
    else:
        updated_code = current_code

    if hex_region is not None:
        updated_location = hex_region.strip()
        if not updated_location:
            await interaction.followup.send("Hex;Region cannot be empty when provided.", ephemeral=True)
            return
    else:
        updated_location = current_location

    if created_by is not None:
        updated_creator_id = str(created_by.id)
        updated_creator_name = str(created_by)
    else:
        updated_creator_id = current_creator_id
        updated_creator_name = str(current_entry.get("creator_name", ""))

    updated_kind = kind.value if kind is not None else current_kind
    updated_access_role = accessible_to.id if accessible_to is not None else current_access
    updated_secondary_access_role = (
        accessible_to_secondary.id if accessible_to_secondary is not None else current_secondary_access
    )

    if updated_secondary_access_role is not None and updated_secondary_access_role == updated_access_role:
        await interaction.followup.send(
            "Primary and secondary access roles must be different.",
            ephemeral=True,
        )
        return

    normalized_code = updated_code.casefold()
    for idx, entry in enumerate(stockpiles):
        if idx == target_index:
            continue
        if str(entry.get("code", "")).casefold() == normalized_code:
            await interaction.followup.send(
                "Another stockpile already uses that code.",
                ephemeral=True,
            )
            return

    stockpiles[target_index] = {
        **current_entry,
        "name": name_value,
        "code": updated_code,
        "location": updated_location,
        "kind": updated_kind,
        "accessible_to": str(updated_access_role),
        "accessible_to_secondary": str(updated_secondary_access_role) if updated_secondary_access_role is not None else None,
        "last_updated": int(time.time()),
        "edited_by": str(interaction.user.id),
        "creator_id": updated_creator_id,
        "creator_name": updated_creator_name,
    }

    if not set_stockpiles(interaction.guild.id, stockpiles):
        await interaction.followup.send("Failed to update stockpile entry.", ephemeral=True)
        return

    await interaction.followup.send(
        (
            "Stockpile updated successfully.\n"
            f"Name: **{name_value}**\n"
            f"Code: **{updated_code}**\n"
            f"Hex;Region: **{updated_location}**\n"
            f"Kind: **{updated_kind}**\n"
            f"Accessible to role: <@&{updated_access_role}>"
            + (
                f"\nSecondary access role: <@&{updated_secondary_access_role}>"
                if updated_secondary_access_role is not None
                else ""
            )
        ),
        ephemeral=True,
    )


@app_commands.command(name="stockpile-change", description="Change the contents of a stockpile using CSV lines")
@app_commands.describe(
    stockpile_name="Select a visible stockpile",
    contents_file="Upload a .txt or .csv containing item,quantity CSV lines",
)
@app_commands.autocomplete(stockpile_name=stockpile_name_autocomplete)
async def stockpile_change(
    interaction: discord.Interaction,
    stockpile_name: str,
    contents_file: discord.Attachment,
):
    if not await _defer_ephemeral_interaction(interaction):
        return

    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    caller_role_ids = {role.id for role in interaction.user.roles}
    stockpiles = get_stockpiles(interaction.guild.id)
    target_index: Optional[int] = None
    normalized_name = stockpile_name.strip().casefold()

    for idx, entry in enumerate(stockpiles):
        if str(entry.get("name", "")).casefold() != normalized_name:
            continue

        if not (_stockpile_access_role_ids(entry) & caller_role_ids):
            await interaction.followup.send(
                "You cannot change this stockpile because it is not accessible to your roles.",
                ephemeral=True,
            )
            return

        target_index = idx
        break

    if target_index is None:
        await interaction.followup.send(
            "No visible stockpile with that name exists.",
            ephemeral=True,
        )
        return

    try:
        uploaded_text = await _read_stockpile_contents_upload(contents_file)
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return
    except Exception:
        await interaction.followup.send(
            "Failed to read uploaded file. Please try again with a .txt or .csv file.",
            ephemeral=True,
        )
        return

    parsed_contents = _parse_stockpile_contents_csv(uploaded_text)
    raw_item_names = _extract_item_names_from_raw_stockpile_csv(uploaded_text)
    target_entry = stockpiles[target_index]
    target_log_key = _stockpile_log_key_from_entry(target_entry)
    stockpiles[target_index] = {
        **target_entry,
        "contents": parsed_contents,
        "last_updated": int(time.time()),
        "updated_by": str(interaction.user.id),
        "log_key": target_log_key,
    }

    if not set_stockpiles(interaction.guild.id, stockpiles):
        await interaction.followup.send("Failed to update stockpile contents.", ephemeral=True)
        return

    log_ok, log_path, log_error = log_stockpile_workbook_update(
        interaction.guild.id,
        stockpiles,
        updated_stockpile_key=target_log_key,
        event_unix_ts=stockpiles[target_index].get("last_updated"),
        extra_item_names=raw_item_names,
    )

    kept_items = len(_extract_stockpile_items_from_contents(parsed_contents))
    log_suffix = ""
    if log_ok and log_path:
        log_suffix = f"\nLog updated: **{os.path.basename(log_path)}**"
    elif log_error:
        log_suffix = f"\nStockpile log update failed: `{log_error}`"

    await interaction.followup.send(
        (
            f"Updated contents for **{stockpiles[target_index].get('name', 'Unknown')}** from upload.\n"
            f"Kept items with qty >= 1: **{kept_items}**"
            f"{log_suffix}"
        ),
        ephemeral=True,
    )


@app_commands.command(name="stockpile-list", description="List all stockpiles in the bot database")
async def stockpile_list(interaction: discord.Interaction):
    if not await _defer_ephemeral_interaction(interaction):
        return

    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    caller_role_ids = {role.id for role in interaction.user.roles}
    stockpiles = get_stockpiles(interaction.guild.id)
    visible_entries: list[dict[str, Any]] = []
    for entry in stockpiles:
        if not (_stockpile_access_role_ids(entry) & caller_role_ids):
            continue
        visible_entries.append(entry)

    if not visible_entries:
        await interaction.followup.send(
            "No stockpiles are visible to your current role set.",
            ephemeral=True,
        )
        return

    overview_embeds = _build_stockpile_overview_embeds(visible_entries)
    view = StockpileSelectView(interaction.user.id, visible_entries, overview_embeds)
    await interaction.followup.send(
        content="Stockpile overview. Use the dropdown below for a refined view.",
        embed=overview_embeds[0],
        view=view,
        ephemeral=True,
    )
    for embed in overview_embeds[1:]:
        await interaction.followup.send(embed=embed, ephemeral=True)


@app_commands.command(name="stockpile-remove", description="Admin: Remove a stockpile entry")
@app_commands.describe(stockpile_name="Select the stockpile to remove")
@app_commands.autocomplete(stockpile_name=stockpile_name_admin_autocomplete)
async def stockpile_remove(
    interaction: discord.Interaction,
    stockpile_name: str,
):
    if not await _defer_ephemeral_interaction(interaction):
        return

    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    if not has_stockpile_admin_access(interaction.user, interaction.guild):
        await interaction.followup.send("Only server administrators or the Staff role can use this command.", ephemeral=True)
        return

    stockpiles = get_stockpiles(interaction.guild.id)
    if not stockpiles:
        await interaction.followup.send("No stockpiles are configured for this server.", ephemeral=True)
        return

    normalized_name = stockpile_name.strip().casefold()
    target_index: Optional[int] = None
    target_entry: Optional[dict[str, Any]] = None

    for idx, entry in enumerate(stockpiles):
        if str(entry.get("name", "")).casefold() == normalized_name:
            target_index = idx
            target_entry = entry
            break

    if target_index is None or target_entry is None:
        await interaction.followup.send("No stockpile with that name exists.", ephemeral=True)
        return

    removed_name = str(target_entry.get("name", "Unknown"))
    removed_location = str(target_entry.get("location", "Unknown"))
    removed_code = str(target_entry.get("code", "Unknown"))
    removed_log_key = _stockpile_log_key_from_entry(target_entry)

    del stockpiles[target_index]

    if not set_stockpiles(interaction.guild.id, stockpiles):
        await interaction.followup.send("Failed to remove stockpile entry.", ephemeral=True)
        return

    # Keep deleted stockpile tables in workbook, but update aggregate snapshot after removal.
    log_ok, log_path, log_error = log_stockpile_workbook_update(
        interaction.guild.id,
        stockpiles,
        updated_stockpile_key=None,
    )

    log_suffix = ""
    if log_ok and log_path:
        log_suffix = f"\nLog updated: **{os.path.basename(log_path)}** (deleted table history retained)"
    elif log_error:
        log_suffix = f"\nStockpile log update failed: `{log_error}`"

    await interaction.followup.send(
        (
            "Stockpile removed successfully.\n"
            f"Name: **{removed_name}**\n"
            f"Location: **{removed_location}**\n"
            f"Code: **{removed_code}**\n"
            f"Log key: `{removed_log_key}`"
            f"{log_suffix}"
        ),
        ephemeral=True,
    )



def setup_stockpile_commands(bot: commands.Bot) -> None:
    for command in (
        stockpile_add,
        stockpile_edit,
        stockpile_change,
        stockpile_list,
        stockpile_remove,
    ):
        try:
            bot.tree.add_command(command, override=True)
        except TypeError:
            bot.tree.add_command(command)
