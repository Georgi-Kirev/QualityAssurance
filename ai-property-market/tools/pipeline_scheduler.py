# TAG: PIPELINE SCHEDULER + WORKER
# Управление на ръчно/автоматично изпълнение на pipeline (main.py --full / --dedup ...)
# Работи в отделен thread. Поддържа статус: IDLE / RUNNING / ERROR / CANCELLING.
# Автоматично на всеки X часа (по подразбиране 12) пусна pipeline-a.
# Може да се спира и стартира по бутони през UI.

import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Europe/Sofia")


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _now() -> datetime:
    return datetime.now(TIMEZONE)


def _ts() -> str:
    return _now().strftime("%d-%m-%Y_%H")


class PipelineWorker:
    def __init__(self):
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._cancel_flag = threading.Event()
        self._auto_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self.status: str = "IDLE"  # IDLE / RUNNING / ERROR / DONE / CANCELLING
        self.current_step: Optional[str] = None
        self.progress_percent: int = 0
        self.last_run_started_at: Optional[str] = None
        self.last_run_finished_at: Optional[str] = None
        self.last_run_status: Optional[str] = None
        self.last_run_exit_code: Optional[int] = None
        self.last_run_output: List[str] = []
        self.next_scheduled_run_at: Optional[str] = None
        self.auto_enabled: bool = False
        self.auto_every_hours: int = 12
        self._stop_auto = threading.Event()

    # ============================================================
    # Internal helpers
    # ============================================================

    def _set_status(self, s: str, step: Optional[str] = None):
        with self._lock:
            self.status = s
            if step is not None:
                self.current_step = step
            if s == "RUNNING":
                self.progress_percent = 5
            elif s in ("DONE",):
                self.progress_percent = 100

    def _append_log(self, line: str) -> None:
        with self._lock:
            # keep last 500 lines
            self.last_run_output.append(f"[{_ts()}] {line}")
            if len(self.last_run_output) > 500:
                self.last_run_output = self.last_run_output[-500:]

    def _load_schedule_settings(self) -> None:
        try:
            from tools.settings import get_schedule
            s = get_schedule()
            with self._lock:
                self.auto_enabled = bool(s.get("enabled", False))
                try:
                    self.auto_every_hours = int(s.get("every_hours", 12))
                except Exception:
                    self.auto_every_hours = 12
                self.next_scheduled_run_at = s.get("next_run_at")
                self.last_run_finished_at = s.get("last_run_at")
                self.last_run_status = s.get("last_run_status")
        except Exception:
            pass

    def _persist_schedule_settings(self) -> None:
        try:
            from tools.settings import set_schedule_field
            set_schedule_field("enabled", self.auto_enabled)
            set_schedule_field("every_hours", self.auto_every_hours)
            set_schedule_field("next_run_at", self.next_scheduled_run_at)
            set_schedule_field("last_run_at", self.last_run_finished_at)
            set_schedule_field("last_run_status", self.last_run_status)
        except Exception:
            pass

    # ============================================================
    # Status getter (thread-safe snapshot)
    # ============================================================

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "current_step": self.current_step,
                "progress_percent": self.progress_percent,
                "last_run_started_at": self.last_run_started_at,
                "last_run_finished_at": self.last_run_finished_at,
                "last_run_status": self.last_run_status,
                "last_run_exit_code": self.last_run_exit_code,
                "last_log_tail": self.last_run_output[-50:],
                "auto_enabled": self.auto_enabled,
                "auto_every_hours": self.auto_every_hours,
                "next_scheduled_run_at": self.next_scheduled_run_at,
                "cancel_flag_set": self._cancel_flag.is_set(),
            }

    # ============================================================
    # Actual pipeline runner
    # ============================================================

    def _run_pipeline(self, options: Optional[Dict[str, Any]] = None) -> int:
        """Изпълнява pipeline в текущия thread. Връща exit_code."""
        options = options or {}
        import sys as _sys

        original_argv = list(_sys.argv)
        try:
            import netpolicy

            self._append_log("Starting pipeline")

            # Стъпките, които свалят от външен източник, се
            # пропускат, ако мрежата е изключена. Dashboard-ът
            # извиква run_manual() с collect=False, но тук
            # пазим условието и при извикване по друг начин.
            collect = bool(options.get("collect", False))
            fetch_all = bool(options.get("fetch_all", False))
            fetch_ids = options.get("fetch_ids") or []
            fresh_matcher = bool(options.get("fresh_matcher", False))
            setup = bool(options.get("setup", False))

            if not netpolicy.network_enabled():
                if collect or fetch_all or fetch_ids:
                    self._append_log(
                        "SKIP collect/fetch: " + netpolicy.describe()
                    )
                collect = False
                fetch_all = False
                fetch_ids = []

            steps = []
            if setup:
                steps.append("setup")
            if collect:
                steps.append("collect")
            if fetch_all or fetch_ids:
                steps.append("fetch")
            steps += ["normalize", "dedup", "consolidate", "export"]
            self._append_log(f"Steps: {steps}")

            # Импортираме main и викаме функциите директно,
            # за да няма os.fork/exec проблеми.
            from main import (
                step_setup,
                step_normalize,
                step_dedup,
                step_consolidate,
                step_export_finished,
            )

            extra = sum([setup, collect, bool(fetch_all or fetch_ids)])
            total_steps = 4 + extra

            progress = 0
            def bump(step_name: str, pct: int):
                if self._cancel_flag.is_set():
                    return
                self._set_status("RUNNING", step_name)
                with self._lock:
                    self.progress_percent = pct

            if setup and not self._cancel_flag.is_set():
                progress += 1
                bump("setup", int(100 * progress / total_steps))
                self._append_log("Step: SETUP (sample data)")
                step_setup()

            if not self._cancel_flag.is_set():
                progress += 1
                bump("normalize", int(100 * progress / total_steps))
                self._append_log("Step: NORMALIZE")
                norm_rc = step_normalize(
                    cancel_event=self._cancel_flag
                )
                if norm_rc == 130:
                    self._append_log(
                        "CANCELLED during normalize "
                        "(partial output cleaned)"
                    )
                    return 130

            if not self._cancel_flag.is_set():
                progress += 1
                bump("dedup", int(100 * progress / total_steps))
                self._append_log("Step: DEDUPLICATE")
                step_dedup()

            if not self._cancel_flag.is_set():
                progress += 1
                bump("consolidate", int(100 * progress / total_steps))
                self._append_log("Step: CONSOLIDATE (entity match + cluster)")
                step_consolidate(fresh_matcher=fresh_matcher)

            if not self._cancel_flag.is_set():
                progress += 1
                bump("export", int(100 * progress / total_steps))
                self._append_log("Step: EXPORT FINISHED")
                step_export_finished()

            if self._cancel_flag.is_set():
                self._append_log("CANCELLED by user")
                return 130

            self._append_log("Pipeline finished OK")
            return 0
        except Exception as e:
            tb = traceback.format_exc()
            self._append_log(f"ERROR: {type(e).__name__}: {e}")
            for line in tb.splitlines()[-15:]:
                self._append_log("  " + line)
            return 1
        finally:
            _sys.argv = original_argv

    # ============================================================
    # Manual run (in background thread)
    # ============================================================

    def run_manual(self, options: Optional[Dict[str, Any]] = None) -> bool:
        with self._run_lock:
            if self.status == "RUNNING":
                return False
            self._cancel_flag.clear()
            self.last_run_output = []
            self.last_run_started_at = _ts()
            self.last_run_finished_at = None
            self.last_run_status = None
            self.last_run_exit_code = None
            self._set_status("RUNNING", "starting")
            self.progress_percent = 2

            def _target():
                try:
                    with self._lock:
                        self._worker_thread = threading.current_thread()
                    code = self._run_pipeline(options)
                    with self._lock:
                        self.last_run_finished_at = _ts()
                        self.last_run_exit_code = code
                        if self._cancel_flag.is_set():
                            self.last_run_status = "CANCELLED"
                        elif code == 0:
                            self.last_run_status = "SUCCESS"
                        else:
                            self.last_run_status = f"ERROR ({code})"
                        self.status = "DONE" if code == 0 and not self._cancel_flag.is_set() else (
                            "IDLE" if self._cancel_flag.is_set() else "ERROR"
                        )
                        self.current_step = None
                        self.progress_percent = 100 if code == 0 else self.progress_percent
                    self._persist_schedule_settings()
                except Exception:
                    with self._lock:
                        self.last_run_finished_at = _ts()
                        self.last_run_status = "EXCEPTION"
                        self.status = "ERROR"
                    self._persist_schedule_settings()

            t = threading.Thread(target=_target, name="PipelineWorker", daemon=True)
            t.start()
            return True

    def cancel(self) -> None:
        self._cancel_flag.set()
        with self._lock:
            self.status = "CANCELLING"
            self._append_log("Cancel requested...")

    # ============================================================
    # Auto scheduler loop
    # ============================================================

    def set_auto(self, enabled: bool, every_hours: int = 12) -> None:
        with self._lock:
            self.auto_enabled = bool(enabled)
            try:
                every_hours = max(1, int(every_hours))
            except Exception:
                every_hours = 12
            self.auto_every_hours = every_hours
            if enabled:
                self.next_scheduled_run_at = (
                    _now() + timedelta(hours=self.auto_every_hours)
                ).strftime("%d-%m-%Y_%H")
            else:
                self.next_scheduled_run_at = None
                self._stop_auto.set()
        self._persist_schedule_settings()
        if enabled and (self._auto_thread is None or not self._auto_thread.is_alive()):
            self._stop_auto.clear()
            self._auto_thread = threading.Thread(
                target=self._auto_loop, name="PipelineScheduler", daemon=True,
            )
            self._auto_thread.start()

    def _auto_loop(self) -> None:
        self._append_log(f"Auto scheduler started (every {self.auto_every_hours}h)")
        while not self._stop_auto.is_set():
            try:
                # check every 15s
                for _ in range(int(self.auto_every_hours * 60 * 4)):
                    if self._stop_auto.is_set():
                        return
                    # recalculate next run periodically
                    if self.next_scheduled_run_at:
                        try:
                            next_dt = datetime.strptime(
                                self.next_scheduled_run_at, "%d-%m-%Y_%H"
                            ).replace(tzinfo=TIMEZONE)
                        except Exception:
                            next_dt = None
                        if next_dt and _now() >= next_dt:
                            if self.status != "RUNNING":
                                self._append_log("Auto run triggered by schedule")
                                self.run_manual({
                                    "collect": True,
                                    "fetch_all": False,
                                    "fetch_ids": [],
                                })
                            # Schedule next
                            with self._lock:
                                self.next_scheduled_run_at = (
                                    _now() + timedelta(hours=self.auto_every_hours)
                                ).strftime("%d-%m-%Y_%H")
                            self._persist_schedule_settings()
                    time.sleep(15)
            except Exception as e:
                with self._lock:
                    self._append_log(f"Scheduler error: {type(e).__name__}: {e}")
                time.sleep(60)
        self._append_log("Auto scheduler stopped")

    def ensure_started(self) -> None:
        """Зарежда настройките и ако auto е включен - стартира scheduler thread."""
        self._load_schedule_settings()
        if self.auto_enabled:
            self.set_auto(True, self.auto_every_hours)


worker = PipelineWorker()
