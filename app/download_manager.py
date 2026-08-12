"""
Download manager: one persistent worker thread draining a priority queue.

Two job kinds share a single worker:
- FULL_REFRESH (low priority) expands into one BULK job per stale page.
- SINGLE_PAGE (high priority) is a user-requested card that jumps ahead of the
  bulk backlog and runs as soon as the in-flight download finishes.

Rate-limiting waits between downloads apply only to BULK jobs; a high-priority
single is never made to wait behind the full-refresh backoff. Enqueue calls are
non-blocking and return {queued, position} immediately.
"""

import threading
import time
import os
import queue
import itertools
import random
from enum import Enum

# Import selenium downloader functions
from app.selenium_downloader import (
    create_browser,
    is_session_valid,
    get_already_downloaded,
    download_page_with_selenium
)
from app.watcherbase import watcherbase
from app.config import PAGES_DIR, get_setting, set_setting


class DownloadStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    WAITING = "waiting"
    IMPORTING = "importing"


class DownloadManager:
    """Singleton manager for background downloads with a priority queue."""

    _instance = None
    _lock = threading.Lock()

    # Job priorities (lower number = served first).
    PRIORITY_SINGLE = 10
    PRIORITY_FULL = 20
    PRIORITY_BULK = 30

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._status = DownloadStatus.IDLE

        # Queue + persistent worker
        self._queue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._worker_thread = None
        self._worker_lock = threading.Lock()
        self._stop_requested = False

        # Rate limiting (bulk only)
        self._next_bulk_time = 0.0
        self._dl_counter = 0

        # True while a full refresh's bulk jobs are still draining (WP8). Used to
        # stamp last_auto_finished when the batch completes.
        self._full_refresh_active = False

        # Progress tracking for the current active session
        self._completed_pages = 0
        self._skipped_pages = 0
        self._failed_pages = 0
        self._current_page = ""
        self._current_job_kind = None
        self._wait_remaining = 0
        self._last_error = ""
        self._last_import_report = None
        self._finished_names = []  # page names completed since last idle
        # Page names whose *download* failed (e.g. wrong URL -> no listings
        # table). Kept across idle so the user can see which cards didn't update;
        # cleared when a new full refresh starts.
        self._failed_names = []

    # ------------------------------------------------------------------ status

    def get_status(self):
        """Get current download status, progress, and queue depth."""
        report = self._last_import_report or {}
        pending = self._snapshot()
        pending_page_jobs = sum(1 for p in pending if p[2] in ("single", "bulk"))
        current_is_page = self._current_job_kind in ("single", "bulk")
        total = self._completed_pages + self._failed_pages + pending_page_jobs + (
            1 if current_is_page else 0)
        return {
            "status": self._status.value,
            "total": total,
            "completed": self._completed_pages,
            "skipped": self._skipped_pages,
            "failed": self._failed_pages,
            "current_page": self._current_page,
            "current_job_kind": self._current_job_kind,
            "queue_length": pending_page_jobs,
            "wait_remaining": self._wait_remaining,
            "last_error": self._last_error,
            "progress_percent": self._calculate_progress(total),
            "import_failed": len(report.get("failed", [])),
            "import_rows_skipped": report.get("rows_skipped", 0),
            "import_report": self._last_import_report,
            "finished": list(self._finished_names),
            "failed_names": list(self._failed_names),
        }

    def _calculate_progress(self, total):
        if total == 0:
            return 0
        return int((self._completed_pages + self._failed_pages) / total * 100)

    def _snapshot(self):
        """Thread-safe shallow copy of the pending queue entries."""
        with self._queue.mutex:
            return list(self._queue.queue)

    # ----------------------------------------------------------- enqueue / API

    def _enqueue(self, priority, kind, page_name=None):
        self._stop_requested = False
        self._queue.put((priority, next(self._seq), kind, page_name))
        self._ensure_worker()

    def _ensure_worker(self):
        """Start the worker thread if it isn't already running."""
        with self._worker_lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_thread = threading.Thread(
                    target=self._worker_loop, daemon=True)
                self._worker_thread.start()

    def _bulk_in_progress(self):
        """True if a full refresh / bulk job is queued or currently running."""
        if self._current_job_kind in ("full_refresh", "bulk"):
            return True
        return any(p[2] in ("full_refresh", "bulk") for p in self._snapshot())

    def _count_pages_ahead(self):
        """Single jobs already queued plus the in-flight page download."""
        singles = sum(1 for p in self._snapshot() if p[2] == "single")
        return singles + (1 if self._current_job_kind in ("single", "bulk") else 0)

    def start(self):
        """Queue a full refresh of all stale pages."""
        with self._lock:
            if self._bulk_in_progress():
                return {"success": False, "message": "Download already in progress"}
            self._failed_names = []  # fresh slate for this refresh
            self._enqueue(self.PRIORITY_FULL, "full_refresh")
            return {"success": True, "message": "Full refresh queued"}

    def stop(self):
        """Cancel queued work; the in-flight download finishes on its own."""
        with self._lock:
            if self._status == DownloadStatus.IDLE and self._queue.empty():
                return {"success": False, "message": "No download in progress"}
            self._stop_requested = True
            self._drain_queue()
            self._status = DownloadStatus.STOPPING
            return {"success": True, "message": "Stop requested"}

    def _drain_queue(self):
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def download_from_url(self, url):
        """Parse a CardMarket URL and queue that page (non-blocking)."""
        from app.rename import canonical_from_url

        try:
            canonical_name = canonical_from_url(url)
        except ValueError as e:
            return {"success": False, "message": str(e)}

        return self.download_single_page(canonical_name)

    def download_single_page(self, page_name):
        """Queue a single high-priority page. Returns immediately."""
        if page_name.endswith('.json'):
            page_name = page_name[:-5]
        position = self._count_pages_ahead()
        self._enqueue(self.PRIORITY_SINGLE, "single", page_name)
        return {
            "success": True,
            "queued": True,
            "position": position,
            "page_name": page_name,
            "message": f"Queued ({position} ahead)" if position else "Queued",
        }

    # --------------------------------------------------------------- the worker

    def _expand_full_refresh(self):
        """Enqueue one BULK job per stale page (skips already-downloaded ones)."""
        page_files = []
        for f in os.listdir(PAGES_DIR):
            if f.endswith(".json"):
                filepath = os.path.join(PAGES_DIR, f)
                page_files.append((f, os.path.getmtime(filepath)))
        page_files.sort(key=lambda x: x[1])  # oldest first

        already_downloaded = get_already_downloaded()
        queued = 0
        for page, _mtime in page_files:
            if page[:-5] in already_downloaded:
                self._skipped_pages += 1
            else:
                self._queue.put((self.PRIORITY_BULK, next(self._seq), "bulk", page))
                queued += 1
        if queued > 0:
            self._full_refresh_active = True
        else:
            # Nothing to download — the refresh is already complete.
            set_setting('last_auto_finished', time.time())
            if self._skipped_pages == 0:
                self._last_error = "No pages found in pages/ directory"
        return queued

    def _maybe_stamp_finished(self):
        """Stamp last_auto_finished once a full refresh's bulk jobs have drained."""
        if not self._full_refresh_active:
            return
        if self._current_job_kind in ("bulk", "full_refresh"):
            return
        if any(p[2] in ("bulk", "full_refresh") for p in self._snapshot()):
            return
        set_setting('last_auto_finished', time.time())
        self._full_refresh_active = False

    def _has_single_waiting(self):
        return any(p[2] == "single" for p in self._snapshot())

    def _wait_before_bulk(self):
        """Honour the inter-bulk rate limit. Returns False if a single arrived
        (caller should requeue the bulk job and let the single run first)."""
        if time.time() >= self._next_bulk_time:
            return True
        self._status = DownloadStatus.WAITING
        self._current_page = "Waiting before next download..."
        while True:
            if self._stop_requested:
                return True
            if self._has_single_waiting():
                self._wait_remaining = 0
                return False
            remaining = self._next_bulk_time - time.time()
            if remaining <= 0:
                self._wait_remaining = 0
                self._status = DownloadStatus.RUNNING
                return True
            self._wait_remaining = int(remaining)
            time.sleep(1)

    def _schedule_next_bulk(self):
        wait_min = get_setting('download_wait_min', 5)
        wait_max = get_setting('download_wait_max', 10)
        self._next_bulk_time = time.time() + random.uniform(wait_min, wait_max) * 60

    def _worker_loop(self):
        driver = None
        try:
            while True:
                # Settle the UI to idle once everything pending is done, while
                # keeping the worker (and browser) alive for a short window.
                if (self._queue.empty() and self._current_job_kind is None
                        and (self._completed_pages or self._failed_pages
                             or self._skipped_pages)):
                    self._status = DownloadStatus.IDLE
                    self._current_page = ""

                try:
                    priority, seq, kind, page_name = self._queue.get(timeout=30)
                except queue.Empty:
                    break  # idle long enough: let the thread exit (see finally)

                if self._stop_requested:
                    continue  # queue was drained by stop(); wind down to idle

                if kind == "full_refresh":
                    self._status = DownloadStatus.RUNNING
                    self._current_job_kind = "full_refresh"
                    self._current_page = "Scanning for stale pages..."
                    self._expand_full_refresh()
                    self._current_job_kind = None
                    continue

                # A page job (single or bulk)
                if kind == "bulk":
                    if not self._wait_before_bulk():
                        # A single jumped the queue: requeue this bulk job.
                        self._queue.put((self.PRIORITY_BULK, next(self._seq),
                                         "bulk", page_name))
                        continue

                driver = self._run_page_job(kind, page_name, driver)
                self._current_job_kind = None
                self._maybe_stamp_finished()
        except Exception as e:
            self._last_error = str(e)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            with self._worker_lock:
                self._worker_thread = None
                # A job may have arrived during teardown; respawn rather than
                # strand it (closes the enqueue/exit race with _ensure_worker).
                if not self._queue.empty() and not self._stop_requested:
                    self._worker_thread = threading.Thread(
                        target=self._worker_loop, daemon=True)
                    self._worker_thread.start()
                else:
                    self._go_idle()

    def _run_page_job(self, kind, page_name, driver):
        """Download + import a single page. Returns the (possibly new) driver."""
        self._status = DownloadStatus.RUNNING
        self._current_job_kind = kind
        base = page_name[:-5] if page_name.endswith('.json') else page_name
        self._current_page = base

        if driver is None:
            self._current_page = "Initializing browser..."
            driver = create_browser()
            if driver is None:
                self._last_error = "Failed to initialize browser"
                self._failed_pages += 1
                return None
            self._current_page = base

        if not is_session_valid(driver):
            self._current_page = "Restarting browser..."
            try:
                driver.quit()
            except Exception:
                pass
            driver = create_browser()
            if driver is None:
                self._last_error = "Failed to restart browser"
                self._failed_pages += 1
                return None
            self._current_page = base

        file_name = page_name if page_name.endswith('.json') else page_name + ".json"
        result = download_page_with_selenium(driver, file_name, self._dl_counter)
        self._dl_counter += 1

        if result == "invalid_session":
            # Restart and retry once by requeuing at the same priority.
            try:
                driver.quit()
            except Exception:
                pass
            driver = create_browser()
            prio = self.PRIORITY_SINGLE if kind == "single" else self.PRIORITY_BULK
            self._queue.put((prio, next(self._seq), kind, page_name))
            return driver

        if result == "success":
            self._completed_pages += 1
            self._status = DownloadStatus.IMPORTING
            self._current_page = "Importing..."
            try:
                self._last_import_report = watcherbase.import_all_pages()
            except Exception as e:
                print(f"[WARNING] Import failed: {e}")
            self._finished_names.append(base)
            self._status = DownloadStatus.RUNNING
        else:  # "failed"
            self._failed_pages += 1
            self._finished_names.append(base)
            self._last_error = f"Download failed: {base} (page may have moved or the URL is wrong)"
            if base not in self._failed_names:
                self._failed_names.append(base)

        # Rate-limit the *next* bulk download; singles never wait.
        self._schedule_next_bulk()
        return driver

    def _go_idle(self):
        """Reset the active-session counters and return to idle."""
        self._status = DownloadStatus.IDLE
        self._current_page = ""
        self._current_job_kind = None
        self._wait_remaining = 0
        self._stop_requested = False
        self._completed_pages = 0
        self._skipped_pages = 0
        self._failed_pages = 0
        self._finished_names = []
        self._full_refresh_active = False


# Global instance
download_manager = DownloadManager()
