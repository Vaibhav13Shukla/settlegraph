"""Crash-safe publication helpers for batch artifacts."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _replace_from_temp(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_text(path: Path, text: str) -> None:
    _replace_from_temp(path, lambda handle: handle.write(text))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    def write_rows(handle) -> None:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    _replace_from_temp(path, write_rows)
