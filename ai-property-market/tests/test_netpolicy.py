"""
Тестове за netpolicy.py - мрежевата политика на проекта.

Проектът се разпространява публично и трябва да може да се
клонира и да се пусне БЕЗ да се тегли стотици мегабайти
от чужди сървъри. Затова мрежата е изключена по подразбиране
и всяка точка на сваляне минава през require_network().

Тук пазим ТРИ условия:

  1. По подразбиране require_network() хвърля.
  2. С изрично разрешение тя минава.
  3. РЕАЛНИТЕ точки на сваляне наистина я викат.

Третото е най-важното. Една заспала проверка в
download_dataset() би оставила проекта да прави мрежови
заявки, докато документацията и README твърдят, че не го
прави - и никой тест няма да го забележи.
"""

import pytest

import netpolicy


# ============================================================
# TAG: ИЗКЛЮЧЕНО ПО ПОДРАЗБИРАНЕ
# ============================================================

def test_network_is_disabled_without_the_env_var(monkeypatch):
    monkeypatch.delenv(netpolicy.ENV_VAR, raising=False)

    assert netpolicy.network_enabled() is False


def test_require_network_raises_by_default(monkeypatch):
    monkeypatch.delenv(netpolicy.ENV_VAR, raising=False)

    with pytest.raises(netpolicy.NetworkDisabledError):
        netpolicy.require_network("some download")


def test_error_message_names_the_blocked_operation(monkeypatch):
    """
    Съобщението трябва да казва КОЕ е блокирано, иначе
    човекът няма да разбере защо нещо не работи.
    """

    monkeypatch.delenv(netpolicy.ENV_VAR, raising=False)

    with pytest.raises(netpolicy.NetworkDisabledError) as exc:
        netpolicy.require_network("SofiaPlan dataset 624")

    message = str(exc.value)

    assert "SofiaPlan dataset 624" in message
    assert netpolicy.ENV_VAR in message


def test_error_is_a_runtime_error(monkeypatch):
    """
    Наследява RuntimeError, за да не чупи съществуващи
    обработчики, които ловят по-широко (напр. retry loops).
    """

    monkeypatch.delenv(netpolicy.ENV_VAR, raising=False)

    assert issubclass(
        netpolicy.NetworkDisabledError, RuntimeError
    )


# ============================================================
# TAG: ИЗКЛЮЧВАНЕ
# ============================================================

@pytest.mark.parametrize("value", [
    "1", "true", "TRUE", "yes", "Yes", "on", " 1 ",
])
def test_truthy_values_enable_the_network(monkeypatch, value):
    monkeypatch.setenv(netpolicy.ENV_VAR, value)

    assert netpolicy.network_enabled() is True

    netpolicy.require_network("anything")   # must not raise


@pytest.mark.parametrize("value", [
    "0", "false", "no", "off", "", "   ", "maybe", "2",
])
def test_everything_else_keeps_it_disabled(monkeypatch, value):
    """
    Всичко, което не е изрично истина, значи НЕ.
    По-лесно е да разрешим нещо по погрешка, отколкото
    да го забраним по невнимание.
    """

    monkeypatch.setenv(netpolicy.ENV_VAR, value)

    assert netpolicy.network_enabled() is False


# ============================================================
# TAG: DESCRIBE
# ============================================================

def test_describe_reports_disabled(monkeypatch):
    monkeypatch.delenv(netpolicy.ENV_VAR, raising=False)

    assert netpolicy.describe().startswith("DISABLED")


def test_describe_reports_enabled(monkeypatch):
    monkeypatch.setenv(netpolicy.ENV_VAR, "1")

    assert netpolicy.describe().startswith("ENABLED")


# ============================================================
# TAG: РЕАЛНИТЕ ТОЧКИ НА СВАЛЯНЕ ВИКАТ ПОЛИТИКАТА
# ============================================================

@pytest.mark.parametrize("source", [
    "sofiaplan", "egov", "nsi",
])
def test_every_collector_gates_its_http(source, monkeypatch):
    """
    Всеки collector трябва да вика require_network()
    преди да отвори връзка.

    Проверява се не чрез изпълнение, а чрез текста на
    модула: иначе тестът щеше да прави истинска мрежова
    заявка, което е точно товато, което проектът обещава
    да не прави.
    """

    import importlib
    import inspect

    module = importlib.import_module(f"collector.{source}")
    source_text = inspect.getsource(module)

    assert "require_network(" in source_text, (
        f"collector/{source}.py не вика require_network() - "
        "проекта би правил мрежова заявка без проверка"
    )


def test_dataset_fetcher_gates_its_http(monkeypatch):
    import inspect

    from tools import dataset_fetcher as mod

    assert "require_network(" in inspect.getsource(mod)


def test_collector_network_is_refused_end_to_end(tmp_path, monkeypatch):
    """
    Реален опит за collect() при изключена мрежа трябва да
    падне ПРЕДИ свалянето, а не да удари сървъра.

    Това е единственият тест, който реално извиква
    require_network() през цял collector.
    """

    from collector.sofiaplan import SofiaPlanCollector

    monkeypatch.delenv(netpolicy.ENV_VAR, raising=False)

    collector = SofiaPlanCollector(raw_dir=tmp_path / "raw")

    with pytest.raises(netpolicy.NetworkDisabledError):
        collector.collect()