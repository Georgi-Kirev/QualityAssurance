"""
Тестове за tools/parallel_runner.py и почистващите функции.

Проверява:
  - динамичното ограничаване според ресурсния план
  - отказ по средата и връщане на недовършените задачи
  - централния rate limiter
  - почистването на partial артефакти
"""

import threading
import time
from concurrent.futures import Future as RealFuture
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from tools import resources as resources_mod
from tools.parallel_runner import run_paced


# ============================================================
# TAG: FAKE EXECUTOR
# ============================================================

class FakeFuture(RealFuture):
    """
    Реален concurrent.futures.Future, който ние ръчно
    завършваме. Наследява Future, защото run_paced извиква
    concurrent.futures.wait(), който изисква _condition.
    """

    def __init__(self, value=None, error=None):
        super().__init__()
        self._value = value
        self._error = error
        self.cancel_calls = 0

    def result(self, timeout=None):
        super().result(timeout=timeout)

        if self._error is not None:
            raise self._error

        return self._value

    def cancel(self, msg=None):
        self.cancel_calls += 1
        return super().cancel()

    def finish(self):
        if not self.done():
            super().set_result(self._value)


class FakeExecutor:
    """
    Executor, който завършва задачите мигновено.

    Записва максималния брой едновременно чакащи futures,
    за да можем да проверим темперирането.
    """

    def __init__(self):
        self.submitted = []
        self.futures = []
        self.max_in_flight = 0

    def submit(self, fn, payload, **kwargs):
        future = FakeFuture(
            value={"payload": payload}
        )
        self.submitted.append(payload)
        self.futures.append(future)
        self.max_in_flight = max(
            self.max_in_flight, len(self.futures)
        )
        # Завършваме веднага - тестваме логиката,
        # не реалната паралелизация.
        future.finish()
        return future

    def complete_all(self):
        for future in self.futures:
            future.finish()


# ============================================================
# TAG: RESOURCE PLAN PATCHING
# ============================================================

@pytest.fixture
def fixed_plan(monkeypatch):
    """
    Фиксира броя workers, без да четем реалната машина.
    """

    def apply(workers, allowed=True, reasons=None):
        def fake_plan(target_workers=None, **kwargs):
            return {
                "workers": workers,
                "allowed": allowed,
                "reasons": reasons or [],
                "ceiling": max(workers, 1),
                "cpu_count": 8,
                "cpu_workers": workers,
                "ram_workers": workers,
                "worker_ram_gb": 0.5,
                "cpu_target": 0.8,
                "ram_target": 0.8,
                "hard_limit": 12,
                "reserved_cores": 1,
                "available_ram_gb": 20.0,
                "cpu_percent": 5.0,
                "swap_percent": 0.0,
                "requested_workers": workers,
            }

        monkeypatch.setattr(
            "tools.parallel_runner.plan_workers",
            fake_plan,
        )

    return apply


# ============================================================
# TAG: CORE BEHAVIOUR
# ============================================================

def test_runs_all_items(fixed_plan):
    fixed_plan(workers=4)

    executor = FakeExecutor()

    results = []

    run = run_paced(
        list(range(10)),
        lambda payload: None,
        executor,
        on_result=lambda p, r, e: results.append(p),
    )

    assert run["submitted"] == 10
    assert run["completed"] == 10
    assert run["cancelled"] is False
    assert len(results) == 10


def test_never_exceeds_plan_workers(fixed_plan):
    """
    Ключовото: run_paced НЕ бива да пусне повече задачи
    наведнъж отколкото планът позволява.

    Задачите се завършват бавно (background thread), за да
    може да се наблюдава реалният брой in-flight futures.
    """

    fixed_plan(workers=3)

    executor = SlowExecutor()
    stop = threading.Event()

    def completer():
        while not stop.is_set():
            executor.complete_one()
            time.sleep(0.005)

    thread = threading.Thread(
        target=completer, daemon=True
    )
    thread.start()

    try:

        run_paced(
            list(range(20)),
            lambda payload: None,
            executor,
            on_result=lambda p, r, e: None,
        )

    finally:

        stop.set()
        thread.join(timeout=1)

    assert executor.max_in_flight <= 3
    assert executor.max_in_flight > 0


def test_uses_worker_fn_and_kwargs(fixed_plan):
    fixed_plan(workers=2)

    executor = FakeExecutor()
    seen = []

    class Spy:
        def submit(self, fn, payload, **kwargs):
            seen.append((fn, payload, kwargs))
            future = FakeFuture(value=1)
            future.finish()
            return future

    def worker(payload, extra=None):
        return payload

    run_paced(
        ["a", "b"],
        worker,
        Spy(),
        on_result=lambda p, r, e: None,
        worker_kwargs={"extra": 7},
    )

    assert len(seen) == 2

    for fn, payload, kwargs in seen:
        assert fn is worker
        assert kwargs == {"extra": 7}


def test_errors_are_routed_to_on_result(fixed_plan):
    fixed_plan(workers=2)

    errors = []

    class ErrorExecutor:
        def submit(self, fn, payload, **kwargs):
            future = FakeFuture(
                error=ValueError("boom")
            )
            future.finish()
            return future

    run_paced(
        ["a"],
        lambda payload: None,
        ErrorExecutor(),
        on_result=lambda p, r, e: errors.append(e),
    )

    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


def test_before_submit_is_called_per_item(fixed_plan):
    fixed_plan(workers=2)

    calls = []

    run_paced(
        list(range(5)),
        lambda payload: None,
        FakeExecutor(),
        on_result=lambda p, r, e: None,
        before_submit=calls.append,
    )

    assert len(calls) == 5


def test_on_critical_fires_when_blocked(fixed_plan):
    fixed_plan(
        workers=1,
        allowed=False,
        reasons=["CPU 99%"],
    )

    fired = []

    run_paced(
        list(range(4)),
        lambda payload: None,
        FakeExecutor(),
        on_result=lambda p, r, e: None,
        on_critical=lambda plan: fired.append(plan),
        on_wait=lambda message: None,
    )

    assert len(fired) > 0


# ============================================================
# TAG: CANCEL
# ============================================================

def test_cancel_before_start_submits_nothing(fixed_plan):
    fixed_plan(workers=4)

    executor = FakeExecutor()

    cancel = threading.Event()
    cancel.set()

    run = run_paced(
        list(range(10)),
        lambda payload: None,
        executor,
        on_result=lambda p, r, e: None,
        cancel_event=cancel,
    )

    assert run["cancelled"] is True
    assert run["submitted"] == 0


class SlowExecutor(FakeExecutor):
    """
    Executor, който НЕ завършва задачите сам.

    Поддържа отделно in-flight списък, за да можем да
    проверим реалния брой едновременно изпълнявани задачи.
    """

    def __init__(self):
        super().__init__()
        self.in_flight = []
        self.all_futures = []

    def submit(self, fn, payload, **kwargs):
        future = FakeFuture(value=payload)
        self.submitted.append(payload)
        self.all_futures.append(future)
        self.in_flight.append(future)
        self.max_in_flight = max(
            self.max_in_flight, len(self.in_flight)
        )
        return future

    def complete_one(self):
        for future in list(self.in_flight):
            if not future.done():
                future.finish()
                self.in_flight.remove(future)
                return True
        return False


def test_cancel_midway_returns_abandoned(fixed_plan):
    fixed_plan(workers=2)

    executor = FakeExecutor()

    cancel = threading.Event()

    def on_result(payload, result, error):
        # Спираме след първите две задачи.
        if payload == 1:
            cancel.set()

    run = run_paced(
        list(range(100)),
        lambda payload: None,
        executor,
        on_result=on_result,
        cancel_event=cancel,
    )

    assert run["cancelled"] is True

    # Не сме подали всичките 100.
    assert run["submitted"] < 100


def test_cancelled_futures_are_cancelled(fixed_plan):
    """
    При отказ по средата всички pending futures се отменят
    и се връщат като "abandoned" за почистване.
    """

    fixed_plan(workers=4)

    executor = SlowExecutor()

    cancel = threading.Event()

    # Отказът идва от друг thread, докато run_paced
    # чака бавното изпълнение.
    timer = threading.Timer(0.2, cancel.set)
    timer.daemon = True
    timer.start()

    run = run_paced(
        list(range(50)),
        lambda payload: None,
        executor,
        on_result=lambda p, r, e: None,
        cancel_event=cancel,
    )

    timer.cancel()

    assert run["cancelled"] is True

    # Нищо не е обработено (задачите не завършиха).
    assert run["completed"] == 0

    # Pending futures са отменени.
    assert len(executor.all_futures) > 0

    for future in executor.all_futures:
        assert future.cancel_calls == 1

    # Върнати са за почистване.
    assert len(run["abandoned"]) == len(executor.all_futures)


def test_cancel_after_first_result_cancels_pending(fixed_plan):
    """
    След отказ не се подават повече задачи.
    """

    fixed_plan(workers=4)

    executor = SlowExecutor()

    cancel = threading.Event()

    original_submit = executor.submit

    def submit(fn, payload, **kwargs):
        future = original_submit(fn, payload, **kwargs)
        future.finish()
        return future

    executor.submit = submit

    def on_result(payload, result, error):
        cancel.set()

    run = run_paced(
        list(range(50)),
        lambda payload: None,
        executor,
        on_result=on_result,
        cancel_event=cancel,
    )

    assert run["cancelled"] is True

    # Само първите 4 са подадени, не всичките 50.
    assert len(executor.submitted) == 4


# ============================================================
# TAG: RATE PACER
# ============================================================

def test_rate_pacer_enforces_interval():
    from tools.dataset_fetcher import _RatePacer

    pacer = _RatePacer(requests_per_second=20)

    started = time.monotonic()

    for _ in range(4):
        pacer.wait()

    elapsed = time.monotonic() - started

    # 4 хода => 3 паузи по 0.05s = 0.15s минимум
    assert elapsed >= 0.10


def test_rate_pacer_disabled_when_zero():
    from tools.dataset_fetcher import _RatePacer

    pacer = _RatePacer(requests_per_second=0)

    started = time.monotonic()

    for _ in range(50):
        pacer.wait()

    assert (time.monotonic() - started) < 0.05


def test_worker_skips_local_limiter_when_parent_paced(
    monkeypatch,
    tmp_path,
    network_allowed
):
    """
    Worker-ът НЕ трябва да вика локалния limiter, когато
    лимитът вече е наложен централно. Иначе се получава
    двойно забавяне.

    HTTP-то е мокнато и RAW_DIR сочи към tmp_path. Без
    това тестът написваше в РЕАЛНАТА storage_raw/ и правеше
    мрежова заявка към api.sofiaplan.bg.
    """

    from tools import dataset_fetcher as mod

    monkeypatch.setattr(mod, "RAW_DIR", tmp_path / "raw")

    class _Response:
        headers = {"Content-Type": "application/json"}

        def read(self, size=-1):
            return b'{"type":"FeatureCollection","features":[]}'

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(mod, "urlopen", lambda *a, **k: _Response())

    called = []

    monkeypatch.setattr(
        mod,
        "_wait_for_rate_limit",
        lambda: called.append(1),
    )

    mod._download_once(
        1,
        "sofiaplan",
        "01-01-2026_10",
        rate_limited_by_parent=True,
    )

    assert called == []


# ============================================================
# TAG: REGRESSION - липсващият import os
# ============================================================

def test_dataset_fetcher_imports_os():
    """
    Регресия: tools/dataset_fetcher.py използваше os.replace()
    без import os -> NameError при запис на всеки файл.
    """

    from tools import dataset_fetcher as mod

    assert hasattr(mod, "os"), (
        "dataset_fetcher.py трябва да импортира os - "
        "os.replace() се използва за атомарно записване"
    )


def test_no_bare_os_usage_without_import():
    """
    Регресия: tools/dataset_fetcher.py използваше os.replace()
    без import os -> NameError при запис на всеки файл.

    Проверява AST, а не текст, за да не се хващат
    споменавания в коментари.
    """

    import ast

    base = Path(__file__).resolve().parent.parent

    offenders = []

    folders = [base / "tools", base / "collector", base]

    for folder in folders:

        for path in folder.glob("*.py"):

            if ".venv" in str(path):

                continue

            source = path.read_text(encoding="utf-8")

            tree = ast.parse(source)

            imported = set()

            for node in ast.walk(tree):

                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(
                            alias.asname or alias.name
                        )

                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imported.add(
                            alias.asname or alias.name
                        )

            if "os" in imported:
                continue

            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                ):
                    offenders.append(
                        f"{path.relative_to(base)}:"
                        f"{node.lineno}"
                    )

    assert offenders == [], (
        f"os.* без import os: {sorted(set(offenders))}"
    )


# ============================================================
# TAG: CLEANUP
# ============================================================

def test_cleanup_partial_run_removes_tmp_files(
    tmp_path,
):
    from tools import normalizer as mod

    output_dir = tmp_path / "run"

    (output_dir / "sofiaplan").mkdir(parents=True)

    (output_dir / "sofiaplan" / "dataset_1.json.tmp").write_text(
        "partial"
    )

    (output_dir / "sofiaplan" / "dataset_2.json.tmp").write_text(
        "partial"
    )

    # Успешно завършен файл - НЕ бива да се пипа.
    (output_dir / "sofiaplan" / "dataset_3.json").write_text(
        "complete"
    )

    jobs = [
        {"source": "sofiaplan", "dataset_id": 1},
        {"source": "sofiaplan", "dataset_id": 2},
    ]

    removed = mod.cleanup_partial_run(
        output_dir, jobs
    )

    assert removed == 2

    assert not (output_dir / "sofiaplan" / "dataset_1.json.tmp").exists()
    assert not (output_dir / "sofiaplan" / "dataset_2.json.tmp").exists()

    assert (output_dir / "sofiaplan" / "dataset_3.json").exists()


def test_cleanup_partial_run_handles_missing_dir(tmp_path):
    from tools import normalizer as mod

    assert mod.cleanup_partial_run(
        tmp_path / "does-not-exist", []
    ) == 0


def test_cleanup_partial_downloads(tmp_path, monkeypatch):
    from tools import dataset_fetcher as mod

    monkeypatch.setattr(mod, "RAW_DIR", tmp_path)

    source = "sofiaplan"
    date_folder = "01-01-2026_10"

    for dataset_id in (1, 2):
        d = (
            tmp_path / source / date_folder
            / "datasets" / str(dataset_id)
        )
        d.mkdir(parents=True)
        (d / "raw_data.download").write_text("partial")

    # Завършен dataset - не се пипа.
    done_dir = (
        tmp_path / source / date_folder
        / "datasets" / "3"
    )
    done_dir.mkdir(parents=True)
    (done_dir / "raw_data.geojson").write_text("{}")

    args_list = [
        (1, source, date_folder),
        (2, source, date_folder),
        (3, source, date_folder),
    ]

    removed = mod.cleanup_partial_downloads(
        args_list, source, date_folder
    )

    assert removed == 2

    base = tmp_path / source / date_folder / "datasets"

    assert not (base / "1" / "raw_data.download").exists()
    assert not (base / "2" / "raw_data.download").exists()

    assert (base / "3" / "raw_data.geojson").exists()


# ============================================================
# TAG: REAL EXECUTOR INTEGRATION
# ============================================================

def square(value):
    """Worker на ниво модул (трябва да е picklable)."""

    return value * value


def test_run_paced_with_real_process_pool():
    """
    Интеграционен тест с истински ProcessPoolExecutor -
    проверява, че initializer=lower_process_priority
    не чупи пула.
    """

    results = []

    with ProcessPoolExecutor(
        max_workers=2,
        initializer=resources_mod.lower_process_priority,
    ) as executor:

        run = run_paced(
            list(range(8)),
            square,
            executor,
            on_result=lambda p, r, e: results.append(r),
        )

    assert run["cancelled"] is False
    assert run["submitted"] == 8
    assert len(results) == 8

    assert sorted(results) == sorted(
        x ** 2 for x in range(8)
    )
