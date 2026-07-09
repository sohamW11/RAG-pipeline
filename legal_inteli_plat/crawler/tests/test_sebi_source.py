"""SEBI adapter parsing tests.

The three parse_* functions are pure, so we feed them HTML modeled on SEBI's
real markup and assert on the extracted structures -- no network involved.
"""

from crawler.sources.sebi import (
    SebiCategory,
    SebiSource,
    parse_categories,
    parse_detail_metadata,
    parse_listing_rows,
    parse_pdf_urls,
)

BASE = "https://www.sebi.gov.in"

LEGAL_HTML = """
<html><body>
  <ul>
    <li><a href="/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=1&smid=0">Acts</a></li>
    <li><a href="/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=3&smid=0">Regulations</a></li>
    <li><a href="/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0">Circulars</a></li>
    <li><a href="/legal/online-portal.html">Online portal</a></li>
    <!-- duplicate ssid should be ignored -->
    <li><a href="/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=1&smid=0">Acts</a></li>
  </ul>
</body></html>
"""

LISTING_FRAGMENT = """
<table class="table table-striped">
  <tr><td>Issued Year</td><td>Acts</td></tr>
  <tr>
    <td>2015</td>
    <td><a href="/legal/acts/aug-2015/notifications-under-finance-act_30609.html">
        Notifications under Finance Act- Merger of FMC with SEBI</a></td>
  </tr>
  <tr>
    <td>2015</td>
    <td><a href="/legal/acts/may-2015/the-finance-act-2015_33066.html">The Finance Act.</a></td>
  </tr>
</table>
"""

DETAIL_HTML = """
<html><head><title>SEBI | The Finance Act, 2015</title></head><body>
  <a href="../../../index.html">Home</a>
  <a href="../../../legal.html">Legal</a>
  <h1>The Finance Act, 2015</h1>
  <div class="date">May 14, 2015 | Acts</div>
  <iframe src="../../../web/?file=/sebi_data/attachdocs/1441362496725.pdf"></iframe>
</body></html>
"""


def test_parse_categories_filters_by_keyword_and_dedupes():
    cats = parse_categories(LEGAL_HTML, BASE, ["Acts", "Regulations", "Circulars"])
    assert [c.name for c in cats] == ["Acts", "Regulations", "Circulars"]
    assert [c.ssid for c in cats] == ["1", "3", "7"]
    assert cats[0].url == BASE + "/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=1&smid=0"


def test_parse_categories_empty_keywords_takes_all_listing_links():
    cats = parse_categories(LEGAL_HTML, BASE, [])
    # The non-listing "Online portal" link is excluded; ssid=1 appears once.
    assert [c.ssid for c in cats] == ["1", "3", "7"]


def test_parse_listing_rows_extracts_detail_links_and_year():
    items = parse_listing_rows(LISTING_FRAGMENT, BASE)
    assert len(items) == 2  # header row without a link is skipped
    assert items[0].title.startswith("Notifications under Finance Act")
    assert items[0].detail_url.endswith("_30609.html")
    assert items[0].detail_url.startswith("https://")
    assert items[0].issued_year == 2015


def test_parse_pdf_urls_resolves_iframe_file_param():
    urls = parse_pdf_urls(DETAIL_HTML, BASE)
    assert urls == ["https://www.sebi.gov.in/sebi_data/attachdocs/1441362496725.pdf"]


def test_parse_pdf_urls_handles_direct_pdf_links():
    html = '<a href="/sebi_data/attachdocs/999.pdf">Download</a>'
    assert parse_pdf_urls(html, BASE) == ["https://www.sebi.gov.in/sebi_data/attachdocs/999.pdf"]


def test_archive_categories_derived_for_sections_with_archive():
    from crawler.config.settings import CrawlerSettings, SourceConfig

    source = SourceConfig(name="sebi", base_url=BASE)
    adapter = SebiSource(source, CrawlerSettings())
    active = [
        SebiCategory("Acts", "1", BASE + "/acts"),          # no archive
        SebiCategory("Circulars", "7", BASE + "/circulars"),  # has archive
        SebiCategory("Guidelines", "5", BASE + "/guidelines"),  # has archive
    ]
    archives = adapter.archive_categories(active)

    names = [c.name for c in archives]
    assert names == ["Circulars (Archive)", "Guidelines (Archive)"]
    circ = archives[0]
    assert circ.is_archive is True
    assert circ.ssid == "7"
    assert "doListingCirArchive=yes" in circ.url and "ssid=7" in circ.url
    assert circ.ajax_endpoint.endswith("getArchiveCircularlistinfo.jsp")


def test_parse_detail_metadata_extracts_title_date_and_pdf():
    detail = parse_detail_metadata(DETAIL_HTML, BASE)
    assert detail.title == "The Finance Act, 2015"
    assert detail.category_label == "Acts"
    assert detail.publication_date is not None
    assert (detail.publication_date.year, detail.publication_date.month) == (2015, 5)
    assert detail.pdf_urls == ["https://www.sebi.gov.in/sebi_data/attachdocs/1441362496725.pdf"]
