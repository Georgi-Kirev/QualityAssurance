# TAG: BASE COLLECTOR
# Общ интерфейс за всички бъдещи Collector-и.

from abc import ABC, abstractmethod
from pathlib import Path


class BaseCollector(ABC):

    # TAG: INITIALIZATION
    def __init__(self, source_name: str, raw_dir: Path):
        self.source_name = source_name

        self.raw_dir = raw_dir / source_name

        # Създава папката, ако я няма
        self.raw_dir.mkdir(
            parents=True,
            exist_ok=True
        )

    # TAG: COLLECT CONTRACT
    @abstractmethod
    def collect(self) -> Path:
        """
        Всеки Collector трябва да има collect().
        """
        raise NotImplementedError