from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cronpot.config import AutomationConfig
from cronpot.extraction import fetch_html
from cronpot.ingest import prepare_ingested_recipe
from cronpot.vault import write_recipe_to_vault


@dataclass(slots=True)
class IngestJob:
    id: str
    url: str
    status: str
    created_at: float
    updated_at: float
    attempts: int = 0
    title: str = ""
    path: str = ""
    error: str = ""


_lock = threading.Lock()


def enqueue_ingest_job(vault_path: Path | str, url: str) -> IngestJob:
    now = time.time()
    job = IngestJob(
        id=uuid.uuid4().hex,
        url=url,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    _write_job(_job_dir(vault_path), job)
    return job


def list_jobs(vault_path: Path | str) -> list[IngestJob]:
    directory = _job_dir(vault_path)
    if not directory.exists():
        return []
    jobs = [_read_job(path) for path in sorted(directory.glob("*.json"))]
    return [job for job in jobs if job is not None]


def get_job(vault_path: Path | str, job_id: str) -> IngestJob | None:
    return _read_job(_job_dir(vault_path) / f"{job_id}.json")


def clear_jobs(vault_path: Path | str) -> int:
    directory = _job_dir(vault_path)
    if not directory.exists():
        return 0
    cleared = 0
    for path in directory.glob("*.json"):
        path.unlink()
        cleared += 1
    return cleared


def run_pending_jobs(vault_path: Path | str, config: AutomationConfig, workers: int = 1, limit: int | None = None) -> list[IngestJob]:
    reset_stale_jobs(vault_path, stale_after_seconds=config.worker_stale_after_seconds)
    pending = [job for job in list_jobs(vault_path) if job.status == "pending"]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return []

    count = max(workers, 1)
    results: list[IngestJob] = []
    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(process_job, vault_path, job.id, config) for job in pending]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def process_job(vault_path: Path | str, job_id: str, config: AutomationConfig) -> IngestJob:
    directory = _job_dir(vault_path)
    with _lock:
        job = _read_job(directory / f"{job_id}.json")
        if job is None:
            raise FileNotFoundError(f"No job found for {job_id}")
        if job.status != "pending":
            return job
        if job.attempts >= config.worker_max_attempts:
            job.status = "failed"
            job.error = f"maximum attempts reached ({config.worker_max_attempts})"
            job.updated_at = time.time()
            _write_job(directory, job)
            return job
        job.status = "running"
        job.attempts += 1
        job.updated_at = time.time()
        _write_job(directory, job)

    try:
        recipe = prepare_ingested_recipe(fetch_html(job.url), job.url, vault_path, config)
        if not recipe.has_core_content():
            raise ValueError("extraction incomplete")
        target = write_recipe_to_vault(recipe, vault_path, config=config)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.updated_at = time.time()
        _write_job(directory, job)
        return job

    job.status = "complete"
    job.title = recipe.title
    job.path = str(target)
    job.error = ""
    job.updated_at = time.time()
    _write_job(directory, job)
    return job


def retry_job(vault_path: Path | str, job_id: str) -> IngestJob:
    directory = _job_dir(vault_path)
    job = _read_job(directory / f"{job_id}.json")
    if job is None:
        raise FileNotFoundError(f"No job found for {job_id}")
    if job.status not in {"failed", "running"}:
        return job
    job.status = "pending"
    job.error = ""
    job.updated_at = time.time()
    _write_job(directory, job)
    return job


def reset_stale_jobs(vault_path: Path | str, stale_after_seconds: int = 900) -> list[IngestJob]:
    now = time.time()
    reset: list[IngestJob] = []
    directory = _job_dir(vault_path)
    for job in list_jobs(vault_path):
        if job.status == "running" and now - job.updated_at > stale_after_seconds:
            job.status = "pending"
            job.error = "reset after stale running state"
            job.updated_at = now
            _write_job(directory, job)
            reset.append(job)
    return reset


def job_to_dict(job: IngestJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "url": job.url,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "attempts": job.attempts,
        "title": job.title,
        "path": job.path,
        "error": job.error,
    }


def _job_dir(vault_path: Path | str) -> Path:
    return Path(vault_path) / ".cronpot" / "jobs"


def _write_job(directory: Path, job: IngestJob) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{job.id}.json").write_text(json.dumps(job_to_dict(job), indent=2), encoding="utf-8", newline="\n")


def _read_job(path: Path) -> IngestJob | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return IngestJob(
        id=str(raw.get("id") or path.stem),
        url=str(raw.get("url") or ""),
        status=str(raw.get("status") or "pending"),
        created_at=float(raw.get("created_at") or 0),
        updated_at=float(raw.get("updated_at") or 0),
        attempts=int(raw.get("attempts") or 0),
        title=str(raw.get("title") or ""),
        path=str(raw.get("path") or ""),
        error=str(raw.get("error") or ""),
    )
