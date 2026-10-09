# TAG: SOFIAPLAN COLLECTOR
# Collector за получаване на реални данни от SofiaPlan API.

import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from netpolicy import require_network

from .base import BaseCollector


TIMEZONE = ZoneInfo("Europe/Sofia")


class SofiaPlanCollector(BaseCollector):

    # TAG: SOURCE NAME
    SOURCE_NAME = "sofiaplan"

    # TAG: API ADDRESS
    API_URL = "https://api.sofiaplan.bg/datasets"


    def __init__(self, raw_dir: Path):

        super().__init__(
            self.SOURCE_NAME,
            raw_dir
        )


    def collect(self) -> Path:

        # TAG: CURRENT DATE + TIME
        now = datetime.now(TIMEZONE)

        date_folder = now.strftime("%d-%m-%Y_%H")


        # TAG: RAW OUTPUT DIRECTORY
        output_dir = (
            self.raw_dir /
            date_folder
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True
        )


        # TAG: NETWORK GATE
        require_network("SofiaPlan dataset catalog")

        # TAG: API REQUEST
        print(f"[INFO] Requesting data from:")
        print(self.API_URL)

        with urlopen(
            self.API_URL,
            timeout=30
        ) as response:

            data = response.read()


        # TAG: CHECK JSON
        json_data = json.loads(
            data.decode("utf-8")
        )


        # TAG: RAW OUTPUT FILE
        output_file = (
            output_dir /
            "sofiaplan_datasets.json"
        )


        # TAG: SAVE RAW DATA
        output_file.write_text(
            json.dumps(
                json_data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )


        # TAG: RESULT
        print()
        print("[OK] SofiaPlan API data saved to:")
        print(output_file)


        return output_file