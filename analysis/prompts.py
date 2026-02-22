"""Sentiment analysis prompt templates for Bedrock Claude"""


SYSTEM_PROMPT = """<role>You are a market sentiment analyst specializing in social media analysis for financial markets.</role>

<task>Analyze the social media posts (text and screenshots) about {ticker} and determine the overall sentiment.</task>

<rules>
- Filter out noise: ads, spam, jokes, and posts with no real market opinion
- Weight credible accounts (finance professionals, known analysts) higher than random accounts
- Look for SPECIFIC claims: price targets, catalysts, earnings expectations, flow observations
- Screenshots may contain charts, option flow tables, or other visual data — analyze these carefully
- Separate sentiment from hype — strong conviction with reasoning > many low-quality posts
- If posts are mixed or insufficient, say so honestly with low confidence
</rules>"""


def build_sentiment_prompt(ticker, posts):
    """
    Build a text-only sentiment prompt (no screenshots).
    Used as fallback when no screenshots are available.
    """
    post_block = "\n\n".join(
        f"@{p['handle']} ({p.get('timestamp', 'unknown time')}):\n{p['text']}"
        for p in posts
    )

    return f"""<role>You are a market sentiment analyst specializing in social media analysis for financial markets.</role>

<task>Analyze the following social media posts about ${ticker} and determine the overall sentiment.</task>

<rules>
- Filter out noise: ads, spam, jokes, and posts with no real market opinion
- Weight credible accounts (finance professionals, known analysts) higher than random accounts
- Look for SPECIFIC claims: price targets, catalysts, earnings expectations, flow observations
- Separate sentiment from hype — strong conviction with reasoning > many low-quality posts
- If posts are mixed or insufficient, say so honestly with low confidence
</rules>

<posts ticker="${ticker}">
{post_block}
</posts>"""


def build_multimodal_content(ticker, posts, screenshots):
    """
    Build a multimodal Bedrock messages content array with text + images.

    Args:
        ticker: Ticker symbol
        posts: List of post dicts
        screenshots: Dict mapping post index -> base64 PNG bytes

    Returns:
        List of content blocks for Bedrock messages API
    """
    content = []

    # System instruction as first text block
    content.append({
        "type": "text",
        "text": SYSTEM_PROMPT.format(ticker=ticker),
    })

    # Each post as text + optional screenshot
    for i, p in enumerate(posts):
        header = f"@{p['handle']} ({p.get('timestamp', 'unknown time')}):"
        content.append({
            "type": "text",
            "text": f"\n<post index=\"{i}\">\n{header}\n{p['text']}\n</post>",
        })

        if i in screenshots:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshots[i],
                },
            })

    return content


def build_ticker_inference_prompt(posts):
    """
    Build a prompt that asks the LLM to identify which tickers
    untagged posts are about (posts without explicit $TICKER mentions).
    """
    post_block = "\n\n".join(
        f"[{i}] @{p['handle']} ({p.get('timestamp', '?')}):\n{p['text']}"
        for i, p in enumerate(posts)
    )

    return f"""<role>You are a financial text analyst specializing in ticker identification from social media posts.</role>

<task>The following social media posts do NOT contain explicit $TICKER cashtag mentions. For each post, identify which ticker(s) it most likely refers to. Only assign tickers when you are confident — do not guess.</task>

<reference>
Common mappings:
- "the market", "SPX", "S&P", "indices" -> SPY
- "tech", "nasdaq", "QQQ" -> QQQ
- Company names -> their ticker (e.g. "Apple" -> AAPL, "Tesla" -> TSLA, "Nvidia" -> NVDA)
- Generic market commentary with no specific stock -> MARKET
- Posts with no financial relevance (memes, personal, spam) -> use empty tickers array
</reference>

<posts>
{post_block}
</posts>"""


def build_research_query_prompt(query):
    """
    Convert a natural-language research query into tickers + keywords
    for the Chrome extension to scan on X.
    """
    return f"""<role>You are a financial research assistant that maps natural-language queries to stock tickers and search keywords.</role>

<task>Convert the user's query into specific stock tickers and search keywords for scanning social media (X/Twitter).</task>

<examples>
- "robinhood stock" -> tickers: [HOOD], keywords: [robinhood, HOOD]
- "is nvidia overvalued" -> tickers: [NVDA], keywords: [nvidia, NVDA, overvalued]
- "bitcoin etf flows" -> tickers: [IBIT, GBTC, BITO], keywords: [bitcoin etf, BTC etf, inflows]
- "tech earnings this week" -> tickers: [AAPL, MSFT, GOOGL, META], keywords: [tech earnings, earnings week]
- "EV market sentiment" -> tickers: [TSLA, RIVN, LCID, NIO], keywords: [EV, electric vehicle]
</examples>

<rules>
- Return 1-5 tickers, most relevant first
- Keywords should be good X/Twitter search terms (cashtags are added automatically)
- If the query mentions a specific company, always include its ticker
- For broad themes, pick the top 3-4 most representative tickers
</rules>

<query>{query}</query>"""


def build_market_sentiment_prompt(posts):
    """
    Build a prompt for overall market sentiment across ALL posts.
    This captures the general mood regardless of specific tickers.
    """
    post_block = "\n\n".join(
        f"@{p['handle']} ({p.get('timestamp', '?')}):\n{p['text']}"
        for p in posts
    )

    return f"""<role>You are a market sentiment analyst specializing in broad market mood analysis.</role>

<task>Analyze ALL the following social media posts to determine the OVERALL market sentiment — the general mood of traders and investors right now. This is NOT about any single ticker. Focus on: risk appetite, fear vs greed, sector rotation themes, and macro sentiment.</task>

<rules>
- Filter noise — focus on posts with real market opinions
- Weight credible accounts higher
- Look for: risk-on vs risk-off mood, sector themes, macro concerns, event catalysts
- Note the TIME RANGE of posts — sentiment at market open differs from close
</rules>

<posts>
{post_block}
</posts>"""


def build_recall_insight_prompt(ticker, facts, question=None):
    """
    Build a prompt that analyzes historical sentiment memories for a ticker.
    If no question is given, defaults to sentiment evolution / phase change analysis.
    """
    facts_block = "\n\n".join(f"[{i+1}] {f.strip()}" for i, f in enumerate(facts))

    if question:
        task = question
    else:
        task = (
            "Identify sentiment phase changes, trend reversals, and key shifts over time. "
            "What was the trajectory? Were there inflection points? "
            "What catalysts drove changes? Is the current sentiment consistent or diverging from recent history?"
        )

    return f"""<role>You are a market sentiment analyst specializing in historical trend analysis.</role>

<task>{task}</task>

<rules>
- Focus on CHANGES over time — not just the latest reading
- Identify phase transitions: bullish->bearish, low confidence->high confidence, etc.
- Call out specific dates and catalysts when sentiment shifted
- Note if themes are evolving, recurring, or contradicting each other
- Be concise and actionable — a trader should read this in 30 seconds
</rules>

<data ticker="${ticker}">
{facts_block}
</data>"""
