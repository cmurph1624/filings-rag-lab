from filings_rag.eval.labels import match, norm, shingles


def test_norm_ignores_spacing_and_punctuation() -> None:
    assert norm("Transaction-basedrevenue $\n3,294,978") == norm(
        "Transaction based revenue | 3,294,978"
    )


def test_shingles() -> None:
    assert shingles("") == []
    assert shingles("abc") == ["abc"]
    s = "x" * 100
    assert len(shingles(s)) == 4  # starts 0, 20, 40, 60


EVIDENCE = (
    "CONSOLIDATED STATEMENTS OF OPERATIONS\nRevenue:\nTransaction-basedrevenue\n$\n3,294,978\n"
    "Subscriptionandservices-basedrevenue\n1,539,403\nHardwarerevenue\n91,654\nBitcoinrevenue\n4,571,543"
)


def test_match_finds_squashed_pdf_text_in_html_text() -> None:
    chunk = norm(
        "Square, Inc. CONSOLIDATED STATEMENTS OF OPERATIONS Revenue: "
        "Transaction-based revenue | $ | 3,294,978 "
        "Subscription and services-based revenue | 1,539,403 Hardware revenue | 91,654 "
        "Bitcoin revenue | 4,571,543 Total net revenue"
    )
    coverage, inside = match(EVIDENCE, chunk)
    assert coverage > 0.9 and inside > 0.5


def test_match_partial_and_unrelated() -> None:
    first_half = norm(EVIDENCE[: len(EVIDENCE) // 2]) + norm(" lots of other text " * 30)
    coverage, inside = match(EVIDENCE, first_half)
    assert 0.25 < coverage < 0.75 and inside < 0.5
    assert match(EVIDENCE, norm("Item 1A. Risk Factors " * 20)) == (0.0, 0.0)
