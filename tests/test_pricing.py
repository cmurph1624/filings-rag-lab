import pytest

from filings_rag.pricing import Usage, embedding_cost, load_prices, model_cost

PRICES = {
    "models": {
        "m": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5,
              "cache_write_1h": 4.0},
        "tiered": {
            "input": 0.1, "output": 0.5, "cache_read": 0.01, "long_context_threshold": 100,
            "long_input": 0.5, "long_output": 2.5,
        },
    },
    "embeddings": {"e": {"input": 0.12}},
}  # fmt: skip


def test_model_cost_counts_every_token_class() -> None:
    u = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=500_000,
              cache_write_tokens=200_000, cache_write_1h_tokens=250_000)  # fmt: skip
    assert model_cost("m", u, PRICES) == pytest.approx(2.0 + 1.0 + 0.1 + 0.5 + 1.0)


def test_long_context_rate_applies_over_threshold() -> None:
    short = Usage(input_tokens=100, output_tokens=10)
    long = Usage(input_tokens=101, output_tokens=10)
    assert model_cost("tiered", short, PRICES) == pytest.approx((100 * 0.1 + 10 * 0.5) / 1e6)
    assert model_cost("tiered", long, PRICES) == pytest.approx((101 * 0.5 + 10 * 2.5) / 1e6)
    # cached tokens count toward the threshold and scale to the long rate (0.01 * 5)
    cached = Usage(input_tokens=1, output_tokens=0, cache_read_tokens=200)
    assert model_cost("tiered", cached, PRICES) == pytest.approx((1 * 0.5 + 200 * 0.05) / 1e6)


def test_unknown_model_raises_instead_of_costing_zero() -> None:
    with pytest.raises(KeyError):
        model_cost("nope", Usage(1, 1), PRICES)
    with pytest.raises(KeyError):
        embedding_cost("nope", 1, PRICES)


def test_embedding_cost() -> None:
    assert embedding_cost("e", 2_000_000, PRICES) == pytest.approx(0.24)


def test_usage_adds() -> None:
    assert Usage(1, 2, 3, 4, 5) + Usage(10, 20, 30, 40, 50) == Usage(11, 22, 33, 44, 55)


def test_shipped_price_table_covers_default_models() -> None:
    prices = load_prices()
    for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
        assert model_cost(m, Usage(1000, 1000), prices) > 0
    assert embedding_cost("voyage-finance-2", 1000, prices) > 0


def test_plain_and_batch_views_drop_the_cache_pattern() -> None:
    from filings_rag.pricing import batch_api_cost, plain_api_cost

    # a claude -p call: whole prompt written to the 1-hour cache at 2x input
    u = Usage(input_tokens=2, output_tokens=400, cache_write_1h_tokens=8_698)
    assert model_cost("claude-sonnet-5-5", u) == pytest.approx((2 * 2 + 400 * 10 + 8_698 * 4) / 1e6)
    assert plain_api_cost("claude-sonnet-5-5", u) == pytest.approx((8_700 * 2 + 400 * 10) / 1e6)
    assert batch_api_cost("claude-sonnet-5-5", u) == pytest.approx(
        plain_api_cost("claude-sonnet-5-5", u) / 2
    )


def test_sonnet_cache_read_rate_is_published_rate() -> None:
    assert model_cost("claude-sonnet-5-5", Usage(cache_read_tokens=1_000_000)) == pytest.approx(
        0.10
    )
