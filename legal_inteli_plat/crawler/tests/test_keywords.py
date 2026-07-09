"""Keyword-extraction tests."""

from crawler.utils.keywords import extract_keywords


def test_extract_keywords_keeps_category_years_and_salient_terms():
    kws = extract_keywords(
        "Securities Contracts (Regulation) Act, 1956 (As amended by the Finance Act, 2021)",
        category="Acts",
    )
    assert kws[0] == "acts"           # category first
    assert "1956" in kws and "2021" in kws  # years anchored
    assert "securities" in kws and "contracts" in kws and "finance" in kws
    # stopwords/boilerplate dropped
    assert "the" not in kws and "amended" not in kws and "by" not in kws
    # de-duplicated
    assert len(kws) == len(set(kws))


def test_extract_keywords_respects_limit():
    title = " ".join(f"word{i}" for i in range(50))
    assert len(extract_keywords(title, category="Circulars", limit=10)) == 10
