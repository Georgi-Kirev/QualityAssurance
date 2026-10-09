# TAG: TESTS - EGOV COLLECTOR
#
# Без мрежа. RSS структурата е реална (проверена 28.09.2026:
# data.egov.bg/datasets/rss -> HTTP 200, 1 342 545 байта,
# 1000 <item>), но тук тя е построена СИНТЕТИЧНО - за да не
# натоварваме държавен портал при всяко пускане на тестовете.

import pytest

from collector.egov import EgovCollector


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>data.egov.bg</title>
    <item>
      <resource id="129521">
      <title>Added resource data - Цени на жилища по райони</title>
      <itemName>Цени на жилища по райони</itemName>
      <link>https://data.egov.bg/data/resourceView/7778b40a-a328-49fa-9f92-9d33ec16199d</link>
      <description>Added resource data - Цени на жилища по райони</description>
      <moment>2026-09-28 10:00:00</moment>
      <guid>abc-123</guid>
      </resource>
    </item>
    <item>
      <resource id="129522">
      <title>Edited dataset - ЕКАТТЕ</title>
      <itemName>ЕКАТТЕ</itemName>
      <link>https://data.egov.bg/data/resourceView/11111111-2222-3333-4444-555555555555</link>
      <description>Edited dataset - ЕКАТТЕ</description>
      <moment>2026-09-27 09:30:00</moment>
      <guid>def-456</guid>
      </resource>
    </item>
  </channel>
</rss>
"""


HTML_SAMPLE = """
<html><body>
  <a href="https://data.egov.bg/data/view/dfef50a3-f7d5-4a67-907a-ff598a829da0">Набор А</a>
  <a href="https://data.egov.bg/data/view/11111111-2222-3333-4444-555555555555">Набор Б</a>
  <a href="https://data.egov.bg/dataset/view/OLD-STYLE">Стара форма</a>
  <a href="https://evil.com/data/view/99999999-9999-9999-9999-999999999999">Чужд</a>
  <a href="https://data.egov.bg/data/view/dfef50a3-f7d5-4a67-907a-ff598a829da0">Дубликат</a>
</body></html>
"""


@pytest.fixture
def collector(tmp_path):
    return EgovCollector(tmp_path)


# ============================================================
# TAG: STRUCTURE
# ============================================================

def test_creates_source_folder(collector, tmp_path):
    assert (tmp_path / "egov").is_dir()


def test_source_name_is_egov(collector):
    assert collector.SOURCE_NAME == "egov"


def test_rss_url_is_the_verified_one(collector):
    """
    Проверено: data.egov.bg/datasets/rss -> HTTP 200.
    """
    assert collector.RSS_URL == "https://data.egov.bg/datasets/rss"


def test_user_agent_identifies_collector(collector):
    assert "AIPropertyMarket" in collector.USER_AGENT


def test_rate_limit_is_polite(collector):
    assert collector.MIN_INTERVAL_SECONDS >= 1.0


# ============================================================
# TAG: DOMAIN GUARD
# ============================================================

def test_allows_official_host(collector):
    collector._assert_allowed(
        "https://data.egov.bg/data/resourceView/abc"
    )


@pytest.mark.parametrize("url", [
    "https://evil.com/data/x.csv",
    "https://data.egov.bg.evil.com/x.csv",
    "http://data.egov.bg.evil.com/x",
])
def test_rejects_foreign_hosts(collector, url):
    with pytest.raises(ValueError) as info:
        collector._assert_allowed(url)

    assert "Refusing to fetch" in str(info.value)


# ============================================================
# TAG: RSS PARSING
# ============================================================

@pytest.fixture
def parsed_rss(collector, monkeypatch):
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: RSS_SAMPLE.encode("utf-8"),
    )
    return collector.fetch_rss_catalog()


def test_rss_finds_all_items(parsed_rss):
    assert len(parsed_rss["items"]) == 2


def test_rss_extracts_action_from_title_prefix(parsed_rss):
    """
    RSS-ът на egov НЯМА <action> таг - действието е
    префикс на <title>.
    """
    assert parsed_rss["items"][0]["action"] == "Added resource data"
    assert parsed_rss["items"][1]["action"] == "Edited dataset"


def test_rss_extracts_dataset_name(parsed_rss):
    assert (
        parsed_rss["items"][0]["dataset"] == "Цени на жилища по райони"
    )


def test_rss_extracts_moment(parsed_rss):
    assert parsed_rss["items"][0]["date"] == "2026-09-28 10:00:00"


def test_rss_extracts_resource_id_from_attribute(parsed_rss):
    """
    <resource id="129521"> - id-то е в АТРИБУТА, не в текста.
    Наивно четене би върнало заглавието.
    """
    assert parsed_rss["items"][0]["resource_id"] == "129521"


def test_rss_extracts_link(parsed_rss):
    assert parsed_rss["items"][0]["link"].startswith(
        "https://data.egov.bg/data/resourceView/"
    )


def test_rss_reports_bytes(parsed_rss):
    assert parsed_rss["bytes"] > 0


def test_rss_on_empty_feed_returns_nothing(collector, monkeypatch):
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: b"<rss><channel></channel></rss>",
    )
    assert collector.fetch_rss_catalog()["items"] == []


# ============================================================
# TAG: HTML LINK PARSING
# ============================================================

def test_parses_dataset_view_links(collector):
    items = collector._parse_dataset_links(HTML_SAMPLE)

    urls = {item["url"] for item in items}
    assert any(url.endswith("dfef50a3-f7d5-4a67-907a-ff598a829da0") for url in urls)


def test_ignores_old_style_dataset_links(collector):
    """
    Порталът ползва /data/view/<uuid>. Старият /dataset/view/
    формат не се среща в реалните страници.
    """
    items = collector._parse_dataset_links(HTML_SAMPLE)

    urls = {item["url"] for item in items}
    assert not any("OLD-STYLE" in url for url in urls)


def test_ignores_foreign_hosts(collector):
    items = collector._parse_dataset_links(HTML_SAMPLE)

    urls = {item["url"] for item in items}
    assert not any("evil.com" in url for url in urls)


def test_deduplicates_repeated_links(collector):
    items = collector._parse_dataset_links(HTML_SAMPLE)

    urls = [item["url"] for item in items]
    assert len(urls) == len(set(urls))


def test_parses_nothing_from_empty_html(collector):
    assert collector._parse_dataset_links("<html></html>") == []


# ============================================================
# TAG: XML TAG EXTRACTION
# ============================================================

def test_first_tag_handles_cdata(collector):
    block = "<title><![CDATA[Индекси на цените]]></title>"
    assert collector._first_tag(block, "title") == "Индекси на цените"


def test_first_tag_handles_attributes(collector):
    block = '<moment format="x">2026-06-30</moment>'
    assert collector._first_tag(block, "moment") == "2026-06-30"


def test_first_tag_returns_empty_when_missing(collector):
    assert collector._first_tag("<a>x</a>", "title") == ""
