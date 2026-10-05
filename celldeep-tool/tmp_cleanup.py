"""Delete uploaded files and generated reports from /tmp once they are older than six hours.

Only this app's own paths are touched: job folders under JOBS_DIR, extraction audit folders, and
/tmp files the pipeline writes (names starting with "celldeep_" plus a few fixed debug files). Age is
measured from the newest file inside a folder, so a job that is still running is never removed.
"""

import os
import shutil
import time
from pathlib import Path

MAX_AGE_SECONDS = 6 * 60 * 60
# Fixed-name files the pipeline writes; they can contain patient values.
_FIXED_FILES = ("pre_dedup_markers_raw.json", "post_dedup_markers.json", "extraction_completeness_log.txt",
                "dexa_page_images.txt", "dexa_scan_page_1.png")
_AUDITS = "celldeep_extraction_audits"


def _newest_mtime(path: Path) -> float:
    newest = path.lstat().st_mtime
    if path.is_dir() and not path.is_symlink():
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                try:
                    newest = max(newest, (Path(root) / name).lstat().st_mtime)
                except FileNotFoundError:
                    continue
    return newest


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def cleanup(jobs_dir: Path, tmp_root: Path = Path("/tmp"), now: float | None = None,
            max_age: float = MAX_AGE_SECONDS) -> int:
    """Remove expired job folders, audit folders and pipeline temp files. Returns how many were removed."""
    now = time.time() if now is None else now
    jobs_dir, tmp_root = Path(jobs_dir), Path(tmp_root)
    containers = {jobs_dir.resolve(), (tmp_root / _AUDITS).resolve()}
    candidates = []
    for container in containers:
        if container.is_dir():
            candidates += list(container.iterdir())
    if tmp_root.is_dir():
        candidates += [path for path in tmp_root.glob("celldeep_*") if path.resolve() not in containers]
        candidates += [tmp_root / name for name in _FIXED_FILES if (tmp_root / name).exists()]
    removed = 0
    for path in candidates:
        try:
            if now - _newest_mtime(path) > max_age:
                _remove(path)
                removed += 1
        except FileNotFoundError:
            continue
    return removed
