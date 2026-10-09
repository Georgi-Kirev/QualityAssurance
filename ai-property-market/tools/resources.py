# TAG: RESOURCES — динамично изчисляване на worker-и
# Използва се от normalizer.py, dataset_fetcher.py и всеки друг модул,
# който иска да работи паралелно по безопасен начин.
#
# Принципи:
#   1. Никога не се раздават повече workers отколкото реални
#      (физически) ядра минус 1 запазено ядро за операционната система.
#   2. workers се оценяват ОТ ДО, а не само веднъж на старта.
#   3. При критично натоварване не се добавя нов worker, а се ИЗЧАКВА.
#   4. Минимумът е винаги 1 worker, за да има развитие.
#   5. Worker процесите се пускат с намален приоритет, за да не
#      пречат на работата на потребителя.

import math
import os
import time

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


# ============================================================
# TAG: CONFIG — лимити на натоварването
# ============================================================

# Колко от наличните CPU ядра да ползваме (0.0 – 1.0)
CPU_TARGET = 0.80

# Колко от наличната RAM да ползваме (0.0 – 1.0)
RAM_TARGET = 0.80

# Колко RAM да заделим на всеки worker (в GB).
# Консервативна стойност — ijson стрийминг рядко яде повече.
# Измерено: 398 MB вход -> 0.017 GB връх на паметта.
WORKER_RAM_GB = 0.5

# Абсолютен таван на worker-ите, независимо от ресурсите.
MAX_WORKERS_HARD_LIMIT = 12

# --------------------------------------------------------
# TAG: RESERVED CORES
# --------------------------------------------------------
# Винаги оставяме поне RESERVED_CORES реални ядра на операционната
# система и на приложенията на потребителя. Това е основната защита
# срещу повторението на "замразяване" на компютъра.
RESERVED_CORES = 1


# ============================================================
# TAG: CRITICAL THRESHOLDS
# ============================================================
# Ако системта е под някой от тези прагове, НЕ се добавя нов worker.
# Вместо това се изчаква. Така машината никога не се натоварва
# до границата на "убиване" на desktop-а.

# Минимум свободна RAM (GB), под който спираме да добавяме workers.
CRITICAL_RAM_GB = 1.5

# Максимална CPU натовареност (%), над която спираме да добавяме.
CRITICAL_CPU_PERCENT = 90.0

# Максимално използване на swap (%), над което спираме.
CRITICAL_SWAP_PERCENT = 50.0

# Колко дълго да чакаме при критично състояние (секунди).
WAIT_POLL_SECONDS = 5.0

# Ако не стане възможно достатъчно дълго, даваме се и работим с 1.
WAIT_MAX_SECONDS = 120.0


# ============================================================
# TAG: CPU TOPOLOGY
# ============================================================

def physical_cpu_count() -> int:
    """
    Вър броя на РЕАЛНИТЕ (физически) ядра, без хипер-трейдинг.

    os.cpu_count() връща логически ядра (16 на тази машина), а
    машината има само 8 физически. Ползването на логическите ядра
    води до 2x свръх-натоварване.
    """

    if _HAS_PSUTIL:

        count = psutil.cpu_count(
            logical=False
        )

        if count:

            return int(count)

    return int(
        os.cpu_count() or 1
    )


def max_safe_workers(
    reserved_cores: int = RESERVED_CORES,
    hard_limit: int = MAX_WORKERS_HARD_LIMIT,
) -> int:
    """
    Твърдят таван на worker-ите.

    Винаги: физически_ядра - reserved_cores (минимум 1).
    Допълнително се ограничава от hard_limit.
    """

    physical = physical_cpu_count()

    ceiling = physical - reserved_cores

    if ceiling < 1:
        ceiling = 1

    return max(
        1,
        min(ceiling, hard_limit)
    )


# ============================================================
# TAG: LOAD MONITOR — живо четене на натоварването
# ============================================================

class LoadMonitor:
    """
    Чете ТЕКУЩОТО натоварване на машината.

    psutil.cpu_percent(interval=None) не блокира — връща стойността
    от последното извикване нататък. Затова първия път се "нулира"
    (prime), за да не върне безсмислено 0.0.
    """

    def __init__(self):
        self._primed = False

    def prime(self) -> None:
        """Загрява измервателя. Извиква се веднъж."""

        if not _HAS_PSUTIL:
            return

        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        self._primed = True

    def sample(self) -> dict:
        """
        Връща моментна снимка:
            {
                "cpu_percent": float | None,
                "available_ram_gb": float,
                "swap_percent": float | None,
            }
        """

        if not _HAS_PSUTIL:

            return {
                "cpu_percent": None,
                "available_ram_gb": 4.0,
                "swap_percent": None,
            }

        if not self._primed:
            self.prime()

        try:
            cpu_percent = float(
                psutil.cpu_percent(interval=None)
            )
        except Exception:
            cpu_percent = None

        try:
            available_ram_gb = (
                psutil.virtual_memory().available
                / (1024 ** 3)
            )
        except Exception:
            available_ram_gb = 4.0

        try:
            swap_percent = float(
                psutil.swap_memory().percent
            )
        except Exception:
            swap_percent = None

        return {
            "cpu_percent": cpu_percent,
            "available_ram_gb": available_ram_gb,
            "swap_percent": swap_percent,
        }


# Модулен монитор — споделен навсякъде.
monitor = LoadMonitor()


# ============================================================
# TAG: CRITICAL GATE
# ============================================================

def check_critical(
    sample: dict,
    ram_floor_gb: float = CRITICAL_RAM_GB,
    cpu_ceiling_percent: float = CRITICAL_CPU_PERCENT,
    swap_ceiling_percent: float = CRITICAL_SWAP_PERCENT,
) -> list:
    """
    Проверява дали машината е в критично състояние.

    Връща списък от ЧОВЕШКИ-ЧЕТИМИ причини. Празен списък =
    всичко е наред.
    """

    reasons = []

    available_ram_gb = sample.get(
        "available_ram_gb"
    )

    if (
        available_ram_gb is not None
        and available_ram_gb < ram_floor_gb
    ):
        reasons.append(
            f"свободна RAM {available_ram_gb:.2f} GB "
            f"< праг {ram_floor_gb} GB"
        )

    cpu_percent = sample.get(
        "cpu_percent"
    )

    if (
        cpu_percent is not None
        and cpu_percent > cpu_ceiling_percent
    ):
        reasons.append(
            f"CPU {cpu_percent:.0f}% "
            f"> праг {cpu_ceiling_percent:.0f}%"
        )

    swap_percent = sample.get(
        "swap_percent"
    )

    if (
        swap_percent is not None
        and swap_percent > swap_ceiling_percent
    ):
        reasons.append(
            f"swap {swap_percent:.0f}% "
            f"> праг {swap_ceiling_percent:.0f}%"
        )

    return reasons


def wait_for_resources(
    load_monitor: LoadMonitor = None,
    poll_seconds: float = WAIT_POLL_SECONDS,
    max_wait_seconds: float = WAIT_MAX_SECONDS,
    ram_floor_gb: float = CRITICAL_RAM_GB,
    cpu_ceiling_percent: float = CRITICAL_CPU_PERCENT,
    swap_ceiling_percent: float = CRITICAL_SWAP_PERCENT,
    log=None,
) -> bool:
    """
    Изчаква машината да се освободи.

    Връща True ако ресурсите станаха достатъчни, False ако
    изтече максималното време (тогава работим с 1 worker).
    """

    load_monitor = load_monitor or monitor

    if log is None:

        def log(message):
            print(message)

    deadline = time.monotonic() + max_wait_seconds

    announced = False

    while True:

        sample = load_monitor.sample()

        reasons = check_critical(
            sample,
            ram_floor_gb=ram_floor_gb,
            cpu_ceiling_percent=cpu_ceiling_percent,
            swap_ceiling_percent=swap_ceiling_percent,
        )

        if not reasons:
            return True

        if not announced:

            log(
                f"[RESOURCE] Изчаквам ресурси: "
                f"{'; '.join(reasons)}"
            )

            announced = True

        if time.monotonic() >= deadline:

            log(
                f"[RESOURCE] Чаках {max_wait_seconds:.0f}s, "
                f"продължавам с минимум 1 worker."
            )

            return False

        time.sleep(poll_seconds)


# ============================================================
# TAG: CALCULATE WORKERS (статична оценка)
# ============================================================

def calculate_resources(
    cpu_target: float = CPU_TARGET,
    ram_target: float = RAM_TARGET,
    worker_ram_gb: float = WORKER_RAM_GB,
    hard_limit: int = MAX_WORKERS_HARD_LIMIT,
    reserved_cores: int = RESERVED_CORES,
) -> dict:
    """
    Изчислява колко паралелни worker-а да пуснем.

    Основано на РЕАЛНИ физически ядра (без хипер-трейдинг) и
    винаги с поне 1 запазено ядро за операционната система.

    Връща речник с пълна диагностика, за да може да се логва.
    """

    cpu_count = physical_cpu_count()

    ceiling = max_safe_workers(
        reserved_cores=reserved_cores,
        hard_limit=hard_limit,
    )

    # ---- RAM ----
    sample = monitor.sample()

    available_ram_gb = sample["available_ram_gb"]

    # ---- CPU лимит ----
    cpu_workers = max(
        1,
        math.floor(
            cpu_count * cpu_target
        ),
    )

    # ---- RAM лимит ----
    if worker_ram_gb > 0:

        ram_workers = max(
            1,
            math.floor(
                (available_ram_gb * ram_target)
                / worker_ram_gb
            ),
        )

    else:

        ram_workers = cpu_count

    # ---- Финален брой ----
    workers = min(
        cpu_workers,
        ram_workers,
        cpu_count,
        hard_limit,
        ceiling,
    )

    workers = max(1, workers)

    return {
        "cpu_count": cpu_count,
        "available_ram_gb": round(
            available_ram_gb, 2
        ),
        "cpu_workers": cpu_workers,
        "ram_workers": ram_workers,
        "worker_ram_gb": worker_ram_gb,
        "cpu_target": cpu_target,
        "ram_target": ram_target,
        "hard_limit": hard_limit,
        "reserved_cores": reserved_cores,
        "ceiling": ceiling,
        "workers": workers,
    }


# ============================================================
# TAG: PLAN WORKERS (динамична оценка)
# ============================================================

def plan_workers(
    target_workers: int = None,
    load_monitor: LoadMonitor = None,
    cpu_target: float = CPU_TARGET,
    ram_target: float = RAM_TARGET,
    worker_ram_gb: float = WORKER_RAM_GB,
    hard_limit: int = MAX_WORKERS_HARD_LIMIT,
    reserved_cores: int = RESERVED_CORES,
) -> dict:
    """
    Динамично решава колко worker-а да се ползват В ТОЗИ МОМЕНТ.

    Вход: колко worker-а искахме (или None -> статична оценка).
    Изход: речник с resources + "allowed" (bool) + "reasons" (list).

    Правила:
      - таван от max_safe_workers() (физически ядра - 1)
      - ако машината е в критично състояние -> allowed = False
        и workers = 1 (не 0, за да има развитие)
      - иначе се намалява според реално свободната RAM/CPU
    """

    load_monitor = load_monitor or monitor

    if target_workers is None:

        resources = calculate_resources(
            cpu_target=cpu_target,
            ram_target=ram_target,
            worker_ram_gb=worker_ram_gb,
            hard_limit=hard_limit,
            reserved_cores=reserved_cores,
        )

        base_workers = resources["workers"]

    else:

        resources = calculate_resources(
            cpu_target=cpu_target,
            ram_target=ram_target,
            worker_ram_gb=worker_ram_gb,
            hard_limit=hard_limit,
            reserved_cores=reserved_cores,
        )

        base_workers = int(target_workers)

    sample = load_monitor.sample()

    resources["cpu_percent"] = sample["cpu_percent"]

    resources["swap_percent"] = sample["swap_percent"]

    resources["available_ram_gb"] = round(
        sample["available_ram_gb"], 2
    )

    ceiling = resources["ceiling"]

    base_workers = max(
        1,
        min(base_workers, ceiling)
    )

    # --------------------------------------------------------
    # TAG: CRITICAL GATE
    # --------------------------------------------------------

    reasons = check_critical(sample)

    if reasons:

        resources["workers"] = 1
        resources["allowed"] = False
        resources["reasons"] = reasons
        resources["requested_workers"] = base_workers

        return resources

    # --------------------------------------------------------
    # TAG: SOFT SCALING по реална налична RAM
    # --------------------------------------------------------

    available_ram_gb = sample["available_ram_gb"]

    if worker_ram_gb > 0:

        ram_allowed = max(
            1,
            math.floor(
                (available_ram_gb * ram_target)
                / worker_ram_gb
            ),
        )

    else:

        ram_allowed = ceiling

    workers = max(
        1,
        min(
            base_workers,
            ram_allowed,
            ceiling,
        )
    )

    resources["workers"] = workers
    resources["allowed"] = True
    resources["reasons"] = []
    resources["requested_workers"] = base_workers

    return resources


# ============================================================
# TAG: PROCESS PRIORITY
# ============================================================

def lower_process_priority() -> bool:
    """
    Сваля приоритета на ТЕКУЩИЯ процес.

    Извиква се в worker-ите (чрез ProcessPoolExecutor initializer),
    за да не се борят с браузъра и другите приложения на потребителя.

    Връща True ако е приложено.
    """

    try:

        if os.name == "nt":

            import psutil as _psutil

            _psutil.Process().nice(
                _psutil.BELOW_NORMAL_PRIORITY_CLASS
            )

            return True

        os.nice(5)

        return True

    except Exception:

        return False


# ============================================================
# TAG: LOGGING
# ============================================================

def describe_workers(resources: dict) -> str:
    """
    Връща човешки-четим текст за логване.
    """

    cpu_percent = resources.get("cpu_percent")

    cpu_text = "n/a"

    if cpu_percent is not None:

        cpu_text = f"{cpu_percent:.0f}%"

    return (
        f"Workers: {resources['workers']} "
        f"(CPU: {resources['cpu_workers']}/{resources['cpu_count']} "
        f"@ {int(resources['cpu_target'] * 100)}%, "
        f"load {cpu_text}, "
        f"RAM: {resources['ram_workers']} @ {resources['worker_ram_gb']}GB, "
        f"available {resources['available_ram_gb']}GB, "
        f"ceiling {resources.get('ceiling', '?')})"
    )


def describe_plan(resources: dict) -> str:
    """
    Описва динамичния план, включително блокиранията.
    """

    lines = [describe_workers(resources)]

    if not resources.get("allowed", True):

        lines.append(
            "  BLOCKED: "
            + "; ".join(resources.get("reasons", []))
        )

    return "\n".join(lines)
