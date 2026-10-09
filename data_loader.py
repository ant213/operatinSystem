"""Safe helpers for inspecting read-only SQLite event data."""
from __future__ import annotations

import os
import json
import sqlite3
import tempfile
from io import BytesIO
from pathlib import Path

import pandas as pd


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def inspect_database(uploaded_bytes: bytes) -> dict:
    """Inspect an uploaded SQLite file from a temporary copy opened read-only."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp.write(uploaded_bytes)
            temp_path = tmp.name
        uri = "file:" + temp_path.replace("\\", "/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            schema = {}
            for table in tables:
                schema[table] = [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]
            return {"tables": tables, "schema": schema}
        finally:
            connection.close()
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def read_sqlite_table(uploaded_bytes: bytes, table: str) -> pd.DataFrame:
    """Read every row and column from one selected SQLite table, without writes."""
    db = inspect_database(uploaded_bytes)
    if table not in db["tables"]:
        raise ValueError("Selected table is not present in this database.")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp.write(uploaded_bytes)
            temp_path = tmp.name
        uri = "file:" + temp_path.replace("\\", "/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            return pd.read_sql_query("SELECT * FROM " + _quote(table), connection)
        finally:
            connection.close()
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def read_dataset_file(filename: str, content: bytes) -> pd.DataFrame:
    """Load a CSV, JSON, or Excel worksheet as a generic event dataset."""
    suffix = Path(filename).suffix.lower()
    source = BytesIO(content)
    if suffix == ".csv":
        frame = pd.read_csv(source)
    elif suffix == ".json":
        payload = json.loads(content.decode("utf-8-sig"))
        if isinstance(payload, list):
            frame = pd.json_normalize(payload)
        elif isinstance(payload, dict):
            # Common API/export shape: {"data": [{...}, ...]}.
            records = payload.get("data") if isinstance(payload.get("data"), list) else payload
            frame = pd.json_normalize(records)
        else:
            raise ValueError("JSON must contain an object or a list of records.")
    elif suffix in (".xlsx", ".xlsm"):
        frame = pd.read_excel(source)
    else:
        raise ValueError("Supported files are CSV, JSON, XLSX, XLSM, and SQLite databases.")
    if frame.empty:
        raise ValueError("The selected file contains no data rows.")
    if not len(frame.columns):
        raise ValueError("The selected file has no columns.")
    return frame


def dataset_to_processes(
    frame: pd.DataFrame,
    *,
    id_column: str | None,
    arrival_column: str | None,
    arrival_is_timestamp: bool,
    burst_column: str | None,
    priority_column: str | None,
    queue_column: str | None,
    description_columns: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Map arbitrary dataset rows into scheduler inputs, simulating missing CPU fields."""
    if frame.empty:
        raise ValueError("The selected dataset contains no rows.")
    data = frame.reset_index(drop=True)
    count = len(data)
    notes: list[str] = []

    if arrival_column:
        if arrival_is_timestamp:
            stamps = pd.to_datetime(data[arrival_column], errors="coerce", utc=True)
            if stamps.isna().any():
                raise ValueError(f"{int(stamps.isna().sum())} rows have an unreadable timestamp in {arrival_column!r}.")
            arrivals = ((stamps - stamps.min()).dt.total_seconds() / 60).round().astype(int).tolist()
            notes.append(f"Arrival times were converted from {arrival_column!r} to minutes from the earliest timestamp.")
        else:
            numeric = pd.to_numeric(data[arrival_column], errors="coerce")
            if numeric.isna().any():
                raise ValueError(f"{int(numeric.isna().sum())} rows have a non-numeric arrival value in {arrival_column!r}.")
            arrivals = numeric.round().astype(int).tolist()
            notes.append(f"Arrival values came from {arrival_column!r} and were rounded to integer time units.")
    else:
        arrivals = list(range(count))
        notes.append("Arrival times were generated from row order (0, 1, 2, ...).")

    def read_numeric(column: str | None, field_name: str) -> list[int] | None:
        if not column:
            return None
        numeric = pd.to_numeric(data[column], errors="coerce")
        if numeric.isna().any():
            raise ValueError(f"{int(numeric.isna().sum())} rows have a non-numeric {field_name} value in {column!r}.")
        return numeric.round().astype(int).tolist()

    bursts = read_numeric(burst_column, "burst")
    priorities = read_numeric(priority_column, "priority")
    queues = read_numeric(queue_column, "queue")
    if bursts is None:
        bursts = [1 + i % 5 for i in range(count)]
        notes.append("CPU burst times were generated simulated values (1–5 time units).")
    if priorities is None:
        priorities = [1 + i % 5 for i in range(count)]
        notes.append("Priority values were generated simulated values (1 is highest).")
    if queues is None:
        queues = [1 if i % 2 == 0 else 2 for i in range(count)]
        notes.append("Queue assignments were generated simulated values (alternating queues 1 and 2).")

    ids: list[str] = []
    used: dict[str, int] = {}
    for i in range(count):
        value = str(data.iloc[i][id_column]).strip() if id_column and pd.notna(data.iloc[i][id_column]) else f"P{i + 1}"
        if not value:
            value = f"P{i + 1}"
        used[value] = used.get(value, 0) + 1
        ids.append(value if used[value] == 1 else f"{value}_{used[value]}")
    if id_column:
        notes.append(f"Process IDs came from {id_column!r}; duplicate IDs received suffixes.")
    else:
        notes.append("Process IDs were generated as P1, P2, P3, ... .")

    descriptions = []
    for i, (_, row) in enumerate(data.iterrows()):
        parts = [f"{column}: {row[column]}" for column in description_columns if column in data.columns and pd.notna(row[column])]
        descriptions.append(" | ".join(parts) if parts else f"Imported row {i + 1}")

    output = pd.DataFrame({
        "Process": ids,
        "Name": [f"Task {pid}" for pid in ids],
        "Source Event": descriptions,
        "Arrival": arrivals,
        "Burst": bursts,
        "Priority": priorities,
        "Queue": queues,
    })
    return output, notes


def load_events(uploaded_bytes: bytes, table: str, timestamp_column: str,
                description_columns: list[str]) -> pd.DataFrame:
    """Read selected columns and map timestamp offsets to integer arrival units."""
    db = inspect_database(uploaded_bytes)
    if table not in db["tables"]:
        raise ValueError("Selected table is not present in this database.")
    allowed = set(db["schema"][table])
    selected = [timestamp_column] + [c for c in description_columns if c in allowed and c != timestamp_column]
    if timestamp_column not in allowed:
        raise ValueError("Selected timestamp column is not present in this table.")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp.write(uploaded_bytes)
            temp_path = tmp.name
        connection = sqlite3.connect("file:" + temp_path.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            sql = ("SELECT " + ", ".join(_quote(c) for c in selected) + " FROM " + _quote(table)
                   + " ORDER BY " + _quote(timestamp_column))
            frame = pd.read_sql_query(sql, connection)
        finally:
            connection.close()
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    stamps = pd.to_datetime(frame[timestamp_column], errors="coerce", utc=True)
    valid = stamps.notna()
    frame = frame.loc[valid].copy()
    stamps = stamps.loc[valid]
    if frame.empty:
        raise ValueError("No usable timestamp values were found in that column.")
    origin = stamps.min()
    frame["Arrival"] = ((stamps - origin).dt.total_seconds() / 60).round().astype(int)
    frame["Source Event"] = frame[selected[1:]].astype(str).agg(" | ".join, axis=1) if len(selected) > 1 else table
    # Explicitly simulated example values: the database does not supply CPU measurements.
    frame["Burst"] = [1 + (i % 5) for i in range(len(frame))]
    frame["Priority"] = [1 + (i % 5) for i in range(len(frame))]
    frame["Queue"] = [1 if i % 2 == 0 else 2 for i in range(len(frame))]
    frame["Process"] = [f"P{i + 1}" for i in range(len(frame))]
    frame["Name"] = [f"Task P{i + 1}" for i in range(len(frame))]
    return frame[["Process", "Name", "Source Event", "Arrival", "Burst", "Priority", "Queue"]]
