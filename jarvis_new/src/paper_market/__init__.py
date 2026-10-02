"""Local paper exchange. No brokerage credentials or real order endpoints."""

MODEL = "gpt-6-astra"
EFFORT = "high"
SYMBOLS = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META")
DECISION_INTERVAL_SECONDS = 1800
MAX_QUOTE_AGE_SECONDS = 120
FILL_POLICY = (
    "Local simulated fractional fills at freshly fetched last-trade prices, "
    "with 0.10% adverse slippage; zero commission. Not brokerage executions. "
    "USD only, no leverage or shorting; dividends, corporate actions, taxes and "
    "market impact are not modeled."
)
