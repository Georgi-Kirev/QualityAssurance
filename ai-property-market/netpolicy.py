# TAG: NETWORK POLICY
# Един-единственото място в проекта, което решава дали
# извличането на данни от външни източници е разрешено.
#
# ЗАЩО СЪЩЕСТВУВА:
#   Проектът е публичен, некомерсиален хоби проект и
#   разпространяването му в GitHub трябва да е възпроизводимо
#   БЕЗ да се свалят десетки мегабайти от чужди сървъри.
#
#   Затова мрежата е ИЗКЛЮЧЕНА ПО ПОДРАЗБИРАНЕ. Всички
#   download точки в collector/ и tools/ минават през
#   require_network() и се отказват с ясно съобщение.
#
# КАК СЕ ВКЛЮЧВА (по избор, за собствена употреба):
#   Windows   :  set AI_PROPERTY_ALLOW_NETWORK=1
#   Linux/Mac :  export AI_PROPERTY_ALLOW_NETWORK=1
#
#   Стойността е булева: "1"/"true"/"yes" включват мрежата,
#   всичко друго (включително липса на променливата) - изключва.
#
# ПОСЛЕДИЦИ:
#   Разрешени са само локални файлови операции и вграденият
#   демо набор от sample_data/. Всичко останало - обработка,
#   дедупликация, експорт, търсене, тестове - работи.

import os


# TAG: SWITCH

TRUTHY = frozenset({"1", "true", "yes", "on"})

ENV_VAR = "AI_PROPERTY_ALLOW_NETWORK"


def _raw_flag() -> str:
    return os.environ.get(ENV_VAR, "").strip().lower()


def network_enabled() -> bool:
    """
    Върна True само ако мрежата е изрично разрешена
    чрез променливата на средата.
    """

    return _raw_flag() in TRUTHY


class NetworkDisabledError(RuntimeError):
    """
    Вдига се при опит за мрежов достъп в изключен режим.
    Наследява RuntimeError, за да не чупи съществуващи
    обработчици на изключения, които ловят по-широко.
    """


def require_network(what: str) -> None:
    """
    Извиква се точно преди всеки реален мрежов обмен.

    what - кратко човешко описание какво се опитва да
          свали, напр. "SofiaPlan dataset catalog".
    """

    if network_enabled():
        return

    raise NetworkDisabledError(
        "Network access is disabled in this build.\n"
        f"Blocked operation : {what}\n"
        f"To allow it        : set {ENV_VAR}=1 and run again.\n"
        "Without network access the project still runs end to end\n"
        "on the bundled sample data in sample_data/."
    )


def describe() -> str:
    """
    Едно изречение за конзолата и /health.
    """

    if network_enabled():
        return f"ENABLED ({ENV_VAR} is set)"

    return f"DISABLED (default; set {ENV_VAR}=1 to allow downloads)"