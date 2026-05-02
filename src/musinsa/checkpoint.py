from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterable


_LOCKS: dict[Path, Lock] = {}


def _get_lock(path: Path) -> Lock:
    resolved = path.resolve()

    if resolved not in _LOCKS:
        _LOCKS[resolved] = Lock()

    return _LOCKS[resolved]


def row_to_dict(row: Any) -> dict[str, Any]:
    if is_dataclass(row):
        return asdict(row)

    if isinstance(row, dict):
        return dict(row)

    if hasattr(row, "__dict__"):
        return dict(vars(row))

    return dict(row)


def append_rows_csv(path: Path, rows: Iterable[Any]) -> None:
    rows = list(rows)

    if not rows:
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    dict_rows = [row_to_dict(row) for row in rows]
    fieldnames = list(dict_rows[0].keys())

    lock = _get_lock(path)

    with lock:
        need_header = not path.exists() or path.stat().st_size == 0

        with path.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )

            if need_header:
                writer.writeheader()

            writer.writerows(dict_rows)