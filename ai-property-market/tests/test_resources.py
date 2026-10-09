"""
Тестове за tools/resources.py

Проверява изчисляването на броя worker-и, динамичното
мащабиране, критичните прагове и таванът от физически ядра.
"""

import pytest


class FakeMonitor:
    """Подменя реалното натоварване с детерминирани стойности."""

    def __init__(self, cpu=10.0, ram=20.0, swap=0.0):
        self.sample_value = {
            "cpu_percent": cpu,
            "available_ram_gb": ram,
            "swap_percent": swap,
        }

    def sample(self):
        return dict(self.sample_value)


# ============================================================
# TAG: CPU TOPOLOGY
# ============================================================

def test_physical_cpu_count_is_positive(resources):
    assert resources.physical_cpu_count() >= 1


def test_physical_cpu_count_not_greater_than_logical(
    resources,
):
    import os

    assert (
        resources.physical_cpu_count() <= (os.cpu_count() or 1)
    )


def test_max_safe_workers_reserves_one_core(resources):
    physical = resources.physical_cpu_count()

    result = resources.max_safe_workers(
        reserved_cores=1
    )

    assert result == max(1, physical - 1)


def test_max_safe_workers_respects_hard_limit(resources):
    result = resources.max_safe_workers(
        hard_limit=2
    )

    assert result <= 2


def test_max_safe_workers_never_returns_zero(resources):
    # Дори при абсурден брой запазени ядра трябва да остане 1.
    result = resources.max_safe_workers(
        reserved_cores=999
    )

    assert result == 1


def test_workers_never_exceed_reserved_ceiling(resources):
    """
    Основната защита: workers <= физически ядра - 1.
    """

    result = resources.calculate_resources()

    assert result["workers"] <= result["ceiling"]

    assert result["ceiling"] <= max(
        1,
        resources.physical_cpu_count() - 1,
    )


# ============================================================
# TAG: LOAD MONITOR
# ============================================================

def test_load_monitor_sample_has_expected_keys(resources):
    sample = resources.LoadMonitor().sample()

    assert "cpu_percent" in sample
    assert "available_ram_gb" in sample
    assert "swap_percent" in sample

    assert sample["available_ram_gb"] > 0


def test_load_monitor_is_not_blocking(resources):
    """
    Мониторът трябва да чете мигновено (interval=None),
    иначе pipeline-ът ще чака при всяка задача.
    """

    import time

    monitor = resources.LoadMonitor()
    monitor.prime()

    started = time.monotonic()
    monitor.sample()
    elapsed = time.monotonic() - started

    assert elapsed < 0.25


# ============================================================
# TAG: CRITICAL THRESHOLDS
# ============================================================

def test_check_critical_passes_when_idle(resources):
    reasons = resources.check_critical(
        FakeMonitor(cpu=5.0, ram=20.0, swap=1.0).sample()
    )

    assert reasons == []


def test_check_critical_flags_low_ram(resources):
    reasons = resources.check_critical(
        FakeMonitor(ram=0.5).sample()
    )

    assert len(reasons) == 1
    assert "RAM" in reasons[0]


def test_check_critical_flags_high_cpu(resources):
    reasons = resources.check_critical(
        FakeMonitor(cpu=99.0).sample()
    )

    assert len(reasons) == 1
    assert "CPU" in reasons[0]


def test_check_critical_flags_high_swap(resources):
    reasons = resources.check_critical(
        FakeMonitor(swap=95.0).sample()
    )

    assert len(reasons) == 1
    assert "swap" in reasons[0]


def test_check_critical_ignores_none_values(resources):
    sample = {
        "cpu_percent": None,
        "available_ram_gb": 10.0,
        "swap_percent": None,
    }

    assert resources.check_critical(sample) == []


# ============================================================
# TAG: DYNAMIC PLAN
# ============================================================

def test_plan_workers_idle_uses_full_plan(resources):
    plan = resources.plan_workers(
        load_monitor=FakeMonitor(
            cpu=5.0, ram=20.0
        )
    )

    assert plan["allowed"] is True
    assert plan["workers"] >= 1


def test_plan_workers_clamps_request_to_ceiling(resources):
    plan = resources.plan_workers(
        target_workers=999,
        load_monitor=FakeMonitor(
            cpu=5.0, ram=20.0
        )
    )

    assert plan["workers"] <= plan["ceiling"]


def test_plan_workers_critical_cpu_drops_to_one(resources):
    plan = resources.plan_workers(
        target_workers=6,
        load_monitor=FakeMonitor(cpu=97.0, ram=20.0),
    )

    assert plan["allowed"] is False
    assert plan["workers"] == 1
    assert len(plan["reasons"]) == 1


def test_plan_workers_critical_ram_drops_to_one(resources):
    plan = resources.plan_workers(
        target_workers=6,
        load_monitor=FakeMonitor(cpu=5.0, ram=0.4),
    )

    assert plan["allowed"] is False
    assert plan["workers"] == 1


def test_plan_workers_scales_down_with_ram(resources):
    # 3 GB налични, 0.5 GB на worker, 80% ->
    # floor(3 * 0.8 / 0.5) = 4
    plan = resources.plan_workers(
        load_monitor=FakeMonitor(
            cpu=5.0, ram=3.0
        )
    )

    assert plan["workers"] == 4


def test_plan_workers_scales_up_when_ram_frees(resources):
    low = resources.plan_workers(
        load_monitor=FakeMonitor(
            cpu=5.0, ram=3.0
        )
    )

    high = resources.plan_workers(
        load_monitor=FakeMonitor(
            cpu=5.0, ram=30.0
        )
    )

    assert high["workers"] > low["workers"]


def test_plan_workers_always_at_least_one(resources):
    for monitor in (
        FakeMonitor(cpu=99.0, ram=0.1, swap=99.0),
        FakeMonitor(cpu=0.0, ram=0.0, swap=0.0),
    ):
        plan = resources.plan_workers(
            load_monitor=monitor
        )

        assert plan["workers"] == 1
        assert isinstance(plan["workers"], int)


def test_plan_workers_reports_live_cpu(resources):
    plan = resources.plan_workers(
        load_monitor=FakeMonitor(cpu=42.0, ram=20.0)
    )

    assert plan["cpu_percent"] == 42.0


# ============================================================
# TAG: WAIT FOR RESOURCES
# ============================================================

def test_wait_for_resources_returns_immediately_when_ok(
    resources,
):
    class OkMonitor:
        def sample(self):
            return {
                "cpu_percent": 1.0,
                "available_ram_gb": 20.0,
                "swap_percent": 0.0,
            }

    assert resources.wait_for_resources(
        load_monitor=OkMonitor(),
        max_wait_seconds=0.1,
    ) is True


def test_wait_for_resources_gives_up_after_timeout(
    resources,
):
    class BadMonitor:
        def sample(self):
            return {
                "cpu_percent": 100.0,
                "available_ram_gb": 0.1,
                "swap_percent": 0.0,
            }

    result = resources.wait_for_resources(
        load_monitor=BadMonitor(),
        poll_seconds=0.01,
        max_wait_seconds=0.05,
        log=lambda message: None,
    )

    assert result is False


# ============================================================
# TAG: PROCESS PRIORITY
# ============================================================

def test_lower_process_priority_returns_bool(resources):
    assert isinstance(
        resources.lower_process_priority(), bool
    )


# ============================================================
# TAG: DESCRIPTION
# ============================================================

def test_describe_plan_mentions_block(resources):
    plan = resources.plan_workers(
        target_workers=6,
        load_monitor=FakeMonitor(cpu=99.0, ram=20.0),
    )

    text = resources.describe_plan(plan)

    assert "BLOCKED" in text


def test_calculate_resources_returns_dict(resources):
    result = resources.calculate_resources()

    assert isinstance(result, dict)

    for key in (
        "cpu_count",
        "available_ram_gb",
        "cpu_workers",
        "ram_workers",
        "workers",
    ):
        assert key in result, f"липсва ключ {key}"


def test_workers_at_least_one(resources):
    result = resources.calculate_resources()

    assert result["workers"] >= 1
    assert isinstance(result["workers"], int)


def test_workers_never_exceeds_hard_limit(resources):
    hard_limit = 3

    result = resources.calculate_resources(
        hard_limit=hard_limit
    )

    assert result["workers"] <= hard_limit


def test_workers_never_exceeds_cpu_count(resources):
    result = resources.calculate_resources()

    assert result["workers"] <= max(
        1,
        result["cpu_count"]
    )


def test_very_low_ram_target_still_returns_one(resources):
    result = resources.calculate_resources(
        ram_target=0.0000001,
        worker_ram_gb=1000.0
    )

    assert result["workers"] == 1


def test_describe_workers_is_string(resources):
    result = resources.calculate_resources()

    text = resources.describe_workers(result)

    assert isinstance(text, str)
    assert len(text) > 0


def test_psutil_available(resources):
    assert resources._HAS_PSUTIL is True, (
        "psutil е задължителен - startApp.bat го инсталира, "
        "resources.py го ползва за паметта"
    )
