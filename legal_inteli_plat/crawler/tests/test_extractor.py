"""Metadata extraction tests (selector-driven ListingExtractor)."""

from crawler.config.settings import CategoryConfig, SelectorConfig
from crawler.crawler.extractor import ConfigurableListingExtractor


LISTING_HTML = """
<html><body>
<table>
  <tr>
    <td class="title"><a href="/docs/circular-1.pdf">SEBI Circular One</a></td>
    <td class="docnum">CIR/2024/001</td>
    <td class="date">01 Jan 2024</td>
    <td class="dept">Market Regulation</td>
  </tr>
  <tr>
    <td class="title"><a href="/docs/circular-2.pdf">SEBI Circular Two</a></td>
    <td class="docnum">CIR/2024/002</td>
    <td class="date">15 Feb 2024</td>
    <td class="dept">Investment Management</td>
  </tr>
  <tr><td>Header row with no link</td></tr>
</table>
</body></html>
"""


def test_extractor_reads_metadata_from_rows():
    selectors = SelectorConfig(
        row="table tr",
        title="td.title",
        link="td.title a",
        pdf_link="td.title a[href$='.pdf']",
        document_number="td.docnum",
        publication_date="td.date",
        department="td.dept",
        date_format="%d %b %Y",
    )
    category = CategoryConfig(name="Circulars", document_type="circular", language="en")

    docs = ConfigurableListingExtractor().extract(
        LISTING_HTML,
        base_url="https://www.sebi.gov.in/legal/circulars",
        selectors=selectors,
        category=category,
    )

    assert len(docs) == 2  # the header row without a link is skipped
    first = docs[0]
    assert first.title == "SEBI Circular One"
    assert first.document_number == "CIR/2024/001"
    assert first.publication_date is not None
    assert first.publication_date.year == 2024 and first.publication_date.month == 1
    assert first.department == "Market Regulation"
    assert first.pdf_url == "https://www.sebi.gov.in/docs/circular-1.pdf"
    assert first.category_name == "Circulars"
    assert first.document_type == "circular"
