# TAG: PARALLEL RUNNER — динамично темперирано изпълнение
# Обща логика за паралелна обработка на много задачи, която:
#
#   1. НЕ пуска повече задачи наведнъж, отколкото моментно позволява
#      ресурсният план (динамично качване/сваляне на workers).
#   2. Изчаква, когато машината е в критично състояние.
#   3. Поддържа отказ по средата (cancel_event) и връща кои задачи
#      са останали недовършени, за да могат да се изчистят.
#   4. Ограничава броя на едновременно чакащи futures в паметта.
#
# Rate limiting се прави ТУК, в родителския процес, а не във всеки
# worker. При multiprocessing "spawn" всеки worker има собствено копие
# от module-level състояние, така че limiter във worker-а НЕ работи
# глобално (12 workers x 3 req/s = 36 req/s вместо 3).

import time

from concurrent.futures import CancelledError, wait, FIRST_COMPLETED

from tools.resources import (
    plan_workers,
    wait_for_resources,
)


# Колко дълго да чакаме за следващото събитие, ако няма какво да
# правим (вместо да се върти в busy-loop).
IDLE_WAIT_SECONDS = 0.5


def run_paced(
    items,
    worker_fn,
    executor,
    on_result,
    worker_kwargs=None,
    cancel_event=None,
    target_workers=None,
    before_submit=None,
    on_wait=None,
    on_critical=None,
):
    """
    Изпълнява items паралелно с динамично ограничаване.

    Параметри:
        items          — iterable от payload-и (по един на задача)
        worker_fn      — функцията, която ProcessPoolExecutor изпълнява
        executor       — вече създаден ProcessPoolExecutor
        on_result      — fn(payload, result, error)
        worker_kwargs  — допълнителни kwargs за executor.submit
        cancel_event   — threading.Event; ако се зададе, прекратява
        target_workers — колко worker-а максимално (None -> ресурсен
                         таван от физически ядра)
        before_submit  — fn(payload) -> None, извиква се преди всяко
                         submit (използва се за rate limiting)
        on_wait        — fn(message) за логване на изчакване
        on_critical    — fn(plan) при блокиране по критичен праг

    Връща:
        {
            "cancelled": bool,
            "submitted": int,
            "completed": int,
            "abandoned": [payload, ...],
        }
    """

    worker_kwargs = worker_kwargs or {}

    if on_wait is None:

        def on_wait(message):
            print(message)

    item_iter = iter(items)

    pending = {}

    exhausted = False

    cancelled = False

    submitted = 0

    completed = 0

    while True:

        # --------------------------------------------------------
        # TAG: CANCEL CHECK
        # --------------------------------------------------------

        if (
            cancel_event is not None
            and cancel_event.is_set()
        ):

            cancelled = True

            break

        # --------------------------------------------------------
        # TAG: REAP COMPLETED
        # --------------------------------------------------------

        finished = [
            future
            for future in list(pending)
            if future.done()
        ]

        for future in finished:

            payload = pending.pop(future)

            try:

                result = future.result()

            except CancelledError:

                continue

            except Exception as error:

                on_result(
                    payload,
                    None,
                    error,
                )

            else:

                on_result(
                    payload,
                    result,
                    None,
                )

            completed += 1

        # --------------------------------------------------------
        # TAG: TERMINATION
        # --------------------------------------------------------

        if exhausted and not pending:

            break

        # --------------------------------------------------------
        # TAG: RESOURCE PLAN (dynamic)
        # --------------------------------------------------------

        plan = plan_workers(
            target_workers=target_workers
        )

        if not plan["allowed"]:

            if on_critical is not None:

                on_critical(plan)

            wait_for_resources(
                log=on_wait,
            )

            plan = plan_workers(
                target_workers=target_workers
            )

        # --------------------------------------------------------
        # TAG: FILL UP TO PLAN
        # --------------------------------------------------------

        while (
            not exhausted
            and len(pending) < plan["workers"]
        ):

            try:

                payload = next(item_iter)

            except StopIteration:

                exhausted = True

                break

            if before_submit is not None:

                before_submit(payload)

            if (
                cancel_event is not None
                and cancel_event.is_set()
            ):

                cancelled = True

                break

            future = executor.submit(
                worker_fn,
                payload,
                **worker_kwargs
            )

            pending[future] = payload

            submitted += 1

        if cancelled:

            break

        # --------------------------------------------------------
        # TAG: IDLE — нека простървене
        # --------------------------------------------------------

        if pending and not finished:

            wait(
                pending,
                timeout=IDLE_WAIT_SECONDS,
                return_when=FIRST_COMPLETED,
            )

        elif not pending and not exhausted:

            time.sleep(IDLE_WAIT_SECONDS)

    # --------------------------------------------------------
    # TAG: CANCEL — почистване на недовършените
    # --------------------------------------------------------

    if cancelled:

        for future in list(pending):

            future.cancel()

    return {
        "cancelled": cancelled,
        "submitted": submitted,
        "completed": completed,
        "abandoned": list(pending.values()),
    }
