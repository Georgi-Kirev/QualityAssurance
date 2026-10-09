# TAG: TESTS - RATE PACER
#
# Бъгът, който този файл държи:
#   run_paced() извиква before_submit(payload) - т.е. изисква
#   функция с ЕДИН аргумент. _RatePacer.wait() беше дефиниран
#   като wait(self), затова всяко паралелно сваляне падаше с
#
#   TypeError: _RatePacer.wait() takes 1 positional argument
#              but 2 were given
#
#   още при първия submit. Тоест fetch_all() беше напълно
#   неработещ, но нито един тест не минаваше през тази пътека -
#   test_parallel_runner.py използваше calls.append (който
#   приема payload) вместо реалния _RatePacer.

import time

import pytest

from tools.dataset_fetcher import _RatePacer
from tools.parallel_runner import run_paced


# ============================================================
# TAG: SIGNATURE COMPATIBILITY
# ============================================================

def test_wait_accepts_payload_as_run_paced_calls_it():
    """
    РЕГРЕСИЯ: точно този извик падаше преди поправката.
    """
    pacer = _RatePacer(requests_per_second=1000)

    # before_submit(payload) -> pacer.wait(payload)
    pacer.wait("some_payload")

    # и празното извикване трябва да работи
    pacer.wait()


def test_wait_with_zero_rate_returns_immediately():
    pacer = _RatePacer(requests_per_second=0)

    started = time.monotonic()
    pacer.wait("payload")

    assert time.monotonic() - started < 0.1


# ============================================================
# TAG: RATE ENFORCEMENT
# ============================================================

def test_wait_enforces_minimum_interval():
    """
    10 req/s -> минимум 0.1 s между две извиквания.
    """
    pacer = _RatePacer(requests_per_second=10)

    pacer.wait("a")

    started = time.monotonic()
    pacer.wait("b")
    elapsed = time.monotonic() - started

    assert elapsed >= 0.05


def test_wait_is_monotonic_under_repeated_calls():
    pacer = _RatePacer(requests_per_second=50)

    started = time.monotonic()
    for i in range(3):
        pacer.wait(i)
    elapsed = time.monotonic() - started

    # 3 извиквания при 50 req/s = поне 2 интервала от 0.02 s
    assert elapsed >= 0.02


# ============================================================
# TAG: INTEGRATION WITH run_paced
# ============================================================

def test_pacer_is_usable_as_before_submit(monkeypatch):
    """
    Истинският integration тест: _RatePacer като before_submit
    в реален run_paced. Преди поправката тук се хвърляше
    TypeError.
    """
    import tools.resources as resources

    monkeypatch.setattr(
        resources, "plan_workers",
        lambda target_workers=None: {
            "allowed": True,
            "workers": 2,
            "reasons": [],
        },
    )
    monkeypatch.setattr(
        "tools.parallel_runner.plan_workers",
        lambda target_workers=None: {
            "allowed": True,
            "workers": 2,
            "reasons": [],
        },
    )

    seen = []

    def fake_worker(payload):
        seen.append(payload)
        return f"done:{payload}"

    with ThreadPoolExecutorStub() as executor:

        run = run_paced(
            [1, 2, 3],
            fake_worker,
            executor,
            on_result=lambda p, r, e: None,
            before_submit=_RatePacer(1000).wait,
        )

    assert run["submitted"] == 3
    assert run["completed"] == 3
    assert run["cancelled"] is False
    assert sorted(seen) == [1, 2, 3]


class ThreadPoolExecutorStub:
    """
    Минимален синхронен заместител на ProcessPoolExecutor.

    Реалният executor не е нужен - тества се ДАЛИ run_paced
    може да извика before_submit с payload, а не дали
    изпълнява subprocess-и.
    """

    def __enter__(self):
        from concurrent.futures import ThreadPoolExecutor
        self._real = ThreadPoolExecutor(max_workers=2)
        return self

    def __exit__(self, *args):
        self._real.shutdown(wait=True)
        return False

    def submit(self, fn, payload, **kwargs):
        return self._real.submit(fn, payload, **kwargs)
