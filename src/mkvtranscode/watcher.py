"""Watch the incoming directory and start jobs.

Two modes are available:
  inotify  Linux file events. The default. It reacts within one second.
  poll     A timed scan. Use it on file systems where inotify does not work,
           for example some NFS or FUSE mounts.
"""

from __future__ import annotations

import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from .config import Config
from .log import get_logger
from .metadata import build_provider
from .pipeline import Outcome, Pipeline
from .scan import Candidate, is_ready, list_candidates
from .state import State
from .transcode.gpu import GpuPool
from .util import ensure_dir

log = get_logger("watch")


class Watcher:
    """The daemon loop."""

    def __init__(self, config: Config, state: Optional[State] = None):
        self.config = config
        self.state = state or State(os.path.join(config.paths.transcoded, ".state.sqlite"))
        self.provider = build_provider(config)
        self.pipeline = Pipeline(config, self.state, self.provider)
        self.pending: Dict[str, Candidate] = {}
        self.running: List[str] = []
        self.finished: set = set()
        self.attempts: Dict[str, int] = {}
        self.failures = 0
        self.stop_requested = False
        self.last_rescan = 0.0
        self.gpu_pool = GpuPool(config.gpu.devices) if config.gpu.devices else None
        self._gpu_by_path: Dict[str, Optional[int]] = {}

    # ---- setup ---------------------------------------------------------

    def prepare(self) -> None:
        for path in (self.config.paths.incoming, self.config.paths.transcoded,
                     self.config.paths.work, self.config.paths.logs):
            ensure_dir(path)

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

    def _on_signal(self, signum, frame) -> None:
        log.info("signal %d received, finishing running jobs", signum)
        self.stop_requested = True

    # ---- main loop -----------------------------------------------------

    def run(self, once: bool = False, dry_run: bool = False, wait_seconds: float = 0.0) -> int:
        """Watch and process. Return a process exit code."""
        self.prepare()
        if not os.path.isdir(self.config.paths.incoming):
            log.error("incoming directory does not exist: %s", self.config.paths.incoming)
            return 2
        self.install_signal_handlers()
        mode = self._choose_mode()
        watcher = None
        if mode == "inotify":
            from .inotify import Inotify

            try:
                watcher = Inotify()
                watcher.watch(self.config.paths.incoming)
            except OSError as exc:
                log.warning("inotify unavailable (%s), polling instead", exc)
                if watcher is not None:
                    watcher.close()
                watcher, mode = None, "poll"
        log.info("watching %s (%s)", self.config.paths.incoming, mode)
        log.info("provider status: %s", self.provider.status())

        # In 'once' mode, never wait forever. Use the caller's bound, or a
        # default that covers a normal stability window.
        deadline = 0.0
        if once:
            allowed = wait_seconds or max(60.0, self.config.watch.stable_seconds * 6)
            deadline = time.time() + allowed
        try:
            while not self.stop_requested:
                self._refresh_pending()
                self._service_ready(dry_run)
                if once:
                    if not self.pending and not self.running:
                        break
                    if time.time() > deadline:
                        log.warning("waited %.0f seconds, giving up on: %s",
                                    allowed, ", ".join(sorted(os.path.basename(p) for p in self.pending)))
                        self.failures += 1
                        break
                if watcher is not None:
                    self._step_inotify(watcher)
                else:
                    time.sleep(self.config.watch.poll_interval)
                self._rescan_if_due()
        finally:
            if watcher is not None:
                watcher.close()
            self._drain(dry_run)
        return 0 if self.failures == 0 else 1

    def _choose_mode(self) -> str:
        if self.config.watch.mode in ("inotify", "poll"):
            return self.config.watch.mode
        from .inotify import supported

        return "inotify" if supported() else "poll"

    def _step_inotify(self, watcher) -> None:
        from .inotify import IN_CREATE, IN_DELETE, IN_MOVED_FROM, IN_MOVED_TO

        for event in watcher.read(timeout=self.config.watch.poll_interval):
            if event.is_directory:
                continue
            if event.mask & (IN_CREATE | IN_MOVED_TO):
                log.debug("event %s", event.name())
                self._add(event.path)
            elif event.mask & (IN_DELETE | IN_MOVED_FROM):
                self.pending.pop(event.path, None)
                self.state.forget_seen(event.path)

    def _rescan_if_due(self) -> None:
        now = time.time()
        if now - self.last_rescan < self.config.watch.rescan_seconds:
            return
        self.last_rescan = now
        self._refresh_pending()

    # ---- pending set ---------------------------------------------------

    def _refresh_pending(self) -> None:
        for candidate in list_candidates(self.config):
            if candidate.path in self.finished or self.state.is_finished(candidate.path, candidate.size):
                continue
            self._add(candidate.path, candidate)
        for path in list(self.pending):
            if not os.path.exists(path):
                self.pending.pop(path, None)
                self.state.forget_seen(path)

    def _add(self, path: str, candidate: Optional[Candidate] = None) -> None:
        if path in self.running or path in self.pending or path in self.finished:
            return
        if self.state.is_finished(path, candidate.size if candidate else 0):
            self.finished.add(path)
            return
        from .scan import acceptable

        if not acceptable(path, self.config):
            return
        try:
            stat = os.stat(path)
        except OSError:
            return
        entry = candidate or Candidate(path=path, size=stat.st_size, mtime=stat.st_mtime)
        if entry.size < self.config.watch.min_size_mb * 1024 * 1024:
            return
        self.pending[path] = entry
        self.state.note_seen(path, entry.size, entry.mtime)

    # ---- job handling --------------------------------------------------

    def _service_ready(self, dry_run: bool) -> None:
        for path in sorted(list(self.pending)):
            if len(self.running) >= self._parallel_limit():
                return
            if self.gpu_pool and self.gpu_pool.free_count() == 0:
                return
            candidate = self.pending.get(path)
            if candidate is None:
                continue
            if not is_ready(path, self.config, previous=candidate):
                # Reset the stability timer only when the file changed.
                try:
                    stat = os.stat(path)
                except OSError:
                    self.pending.pop(path, None)
                    continue
                if stat.st_size != candidate.size or stat.st_mtime != candidate.mtime:
                    self.pending[path] = Candidate(path, stat.st_size, stat.st_mtime)
                continue
            self.pending.pop(path, None)
            self._start(path, dry_run)

    def _parallel_limit(self) -> int:
        """Return how many jobs may run at once.

        A job needs a GPU when gpu.devices is set, so the GPU count caps the
        number of running jobs.
        """
        limit = self.config.job.max_parallel
        if self.gpu_pool:
            limit = min(limit, len(self.gpu_pool.devices))
        return max(1, limit)

    def _start(self, path: str, dry_run: bool) -> None:
        gpu = self.gpu_pool.acquire() if self.gpu_pool else None
        self.running.append(path)
        self._gpu_by_path[path] = gpu
        if gpu is None:
            log.info("start: %s", os.path.basename(path))
        else:
            log.info("start: %s on gpu %d", os.path.basename(path), gpu)
        if self.config.job.max_parallel <= 1:
            self._finish(self._run_job(path, dry_run, gpu))
            return
        executor = getattr(self, "_executor", None)
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=self._parallel_limit())
            self._executor = executor
        future = executor.submit(self._run_job, path, dry_run, gpu)
        future.add_done_callback(self._on_future)

    def _run_job(self, path: str, dry_run: bool, gpu: Optional[int]) -> Outcome:
        """Run one job, then give its GPU back to the pool."""
        try:
            return self.pipeline.process(path, dry_run=dry_run, gpu=gpu)
        finally:
            self._release_gpu(path)

    def _release_gpu(self, path: str) -> None:
        if not self.gpu_pool:
            return
        self.gpu_pool.release(self._gpu_by_path.pop(path, None))

    def _on_future(self, future) -> None:
        try:
            outcome = future.result()
        except Exception as exc:
            log.error("job raised: %s", exc)
            return
        self._finish(outcome)

    def _finish(self, outcome: Outcome) -> None:
        if outcome.input in self.running:
            self.running.remove(outcome.input)
        if outcome.status in ("done", "skipped"):
            self.finished.add(outcome.input)
        if outcome.status == "failed":
            self.failures += 1
            count = self.attempts.get(outcome.input, 0) + 1
            self.attempts[outcome.input] = count
            if count >= self.config.job.max_attempts:
                log.warning("giving up on %s after %d attempts",
                            os.path.basename(outcome.input), count)
                self.finished.add(outcome.input)

    def _drain(self, dry_run: bool) -> None:
        executor = getattr(self, "_executor", None)
        if executor is not None:
            executor.shutdown(wait=True)
        log.info("stopped; %d job(s) still pending", len(self.pending))
