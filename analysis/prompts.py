"""Sentiment analysis prompt templates for Bedrock Claude"""


SYSTEM_PROMPT = """You are a market sentiment analyst. Analyze the social media posts (text and screenshots) about {ticker} and determine the overall sentiment.

# RULES
1. Filter out noise: ads, spam, jokes, and posts with no real market opinion
2. Weight credible accounts (finance professionals, known analysts) higher than random accounts
3. Look for SPECIFIC claims: price targets, catalysts, earnings expectations, flow observations
4. Screenshots may contain charts, option flow tables, or other visual data — analyze these carefully
5. Separate sentiment from hype — strong conviction with reasoning > many low-quality posts
6. If posts are mixed or insufficient, say so honestly with low confidence

# OUTPUT FORMAT
Return ONLY valid JSON. No text before or after.

{{
  "sentiment": "bullish or bearish or neutral or mixed",
  "confidence": 65,
  "themes": ["theme1", "theme2", "theme3"],
  "notable_accounts": ["handle1", "handle2"],
  "summary": "2-3 sentence summary of what the social media consensus is and why, citing specific claims or observations from the posts",
  "visual_insights": "any additional insights from charts/images in the screenshots, or empty string",
  "noise_filtered": 3,
  "signal_posts": 8
}}"""


def build_sentiment_prompt(ticker, posts):
    """
    Build a text-only sentiment prompt (no screenshots).
    Used as fallback when no screenshots are available.
    """
    post_block = "\n\n".join(
        f"@{p['handle']} ({p.get('timestamp', 'unknown time')}):\n{p['text']}"
        for p in posts
    )

    return f"""You are a market sentiment analyst. Analyze the following social media posts about ${ticker} and determine the overall sentiment.

# RULES
1. Filter out noise: ads, spam, jokes, and posts with no real market opinion
2. Weight credible accounts (finance professionals, known analysts) higher than random accounts
3. Look for SPECIFIC claims: price targets, catalysts, earnings expectations, flow observations
4. Separate sentiment from hype — strong conviction with reasoning > many low-quality posts
5. If posts are mixed or insufficient, say so honestly with low confidence

# POSTS ABOUT ${ticker}
{post_block}

# OUTPUT FORMAT
Return ONLY valid JSON. No text before or after.

{{
  "sentiment": "bullish or bearish or neutral or mixed",
  "confidence": 65,
  "themes": ["theme1", "theme2", "theme3"],
  "notable_accounts": ["handle1", "handle2"],
  "summary": "2-3 sentence summary of what the social media consensus is and why, citing specific claims or observations from the posts",
  "noise_filtered": 3,
  "signal_posts": 8
}}"""


def build_multimodal_content(ticker, posts, screenshots):
    """
    Build a multimodal Bedrock messages content array with text + images.

    Args:
        ticker: Ticker symbol
        posts: List of post dicts
        screenshots: Dict mapping post index → base64 PNG bytes

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
            "text": f"\n--- Post {i+1} ---\n{header}\n{p['text']}",
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

    return f"""You are a financial text analyst. The following social media posts do NOT contain explicit $TICKER cashtag mentions, but many are about specific stocks, ETFs, or the broader market.

For each post, identify which ticker(s) it most likely refers to. Only assign tickers when you are confident — do not guess.

Common mappings:
- "the market", "SPX", "S&P", "indices" → SPY
- "tech", "nasdaq", "QQQ" → QQQ
- Company names → their ticker (e.g. "Apple" → AAPL, "Tesla" → TSLA, "Nvidia" → NVDA)
- Generic market commentary with no specific stock → MARKET (special tag for overall market sentiment)
- Posts with no financial relevance (memes, personal, spam) → SKIP

# POSTS
{post_block}

# OUTPUT FORMAT
Return ONLY valid JSON. No text before or after.
Array of objects, one per post (same order as input). Use the index from the post.

[
  {{"index": 0, "tickers": ["SPY"], "reason": "discusses S&P support levels"}},
  {{"index": 1, "tickers": ["NVDA", "AMD"], "reason": "compares Nvidia and AMD earnings"}},
  {{"index": 2, "tickers": [], "reason": "SKIP - personal post, not financial"}}
]"""


def build_research_query_prompt(query):
    """
    Convert a natural-language research query into tickers + keywords
    for the Chrome extension to scan on X.
    """
    return f"""You are a financial research assistant. Convert the user's natural-language query into specific stock tickers and search keywords for scanning social media (X/Twitter).

# EXAMPLES
- "robinhood stock" → {{"tickers": ["HOOD"], "keywords": ["robinhood", "HOOD"], "reasoning": "Robinhood Markets trades as HOOD"}}
- "is nvidia overvalued" → {{"tickers": ["NVDA"], "keywords": ["nvidia", "NVDA", "overvalued"], "reasoning": "Nvidia trades as NVDA"}}
- "bitcoin etf flows" → {{"tickers": ["IBIT", "GBTC", "BITO"], "keywords": ["bitcoin etf", "BTC etf", "inflows"], "reasoning": "Major bitcoin ETFs are IBIT, GBTC, BITO"}}
- "tech earnings this week" → {{"tickers": ["AAPL", "MSFT", "GOOGL", "META"], "keywords": ["tech earnings", "earnings week"], "reasoning": "Major tech companies reporting"}}
- "EV market sentiment" → {{"tickers": ["TSLA", "RIVN", "LCID", "NIO"], "keywords": ["EV", "electric vehicle"], "reasoning": "Major EV manufacturers"}}

# RULES
1. Return 1-5 tickers, most relevant first
2. Keywords should be good X/Twitter search terms (cashtags are added automatically)
3. If the query mentions a specific company, always include its ticker
4. For broad themes, pick the top 3-4 most representative tickers

# USER QUERY
"{query}"

# OUTPUT FORMAT
Return ONLY valid JSON. No text before or after.

{{"tickers": ["TICK1", "TICK2"], "keywords": ["keyword1", "keyword2"], "reasoning": "brief explanation"}}"""


def build_market_sentiment_prompt(posts):
    """
    Build a prompt for overall market sentiment across ALL posts.
    This captures the general mood regardless of specific tickers.
    """
    post_block = "\n\n".join(
        f"@{p['handle']} ({p.get('timestamp', '?')}):\n{p['text']}"
        for p in posts
    )

    return f"""You are a market sentiment analyst. Analyze ALL the following social media posts to determine the OVERALL market sentiment — the general mood of traders and investors right now.

This is NOT about any single ticker. This is about the market as a whole: risk appetite, fear vs greed, sector rotation themes, and macro sentiment.

# RULES
1. Filter noise — focus on posts with real market opinions
2. Weight credible accounts higher
3. Look for: risk-on vs risk-off mood, sector themes, macro concerns, event catalysts
4. Note the TIME RANGE of posts — sentiment at market open differs from close

# POSTS
{post_block}

# OUTPUT FORMAT
Return ONLY valid JSON. No text before or after.

{{
  "sentiment": "bullish or bearish or neutral or mixed",
  "confidence": 65,
  "themes": ["theme1", "theme2", "theme3"],
  "notable_accounts": ["handle1", "handle2"],
  "summary": "2-3 sentences on overall market mood, citing specific observations",
  "risk_appetite": "risk-on or risk-off or neutral",
  "sector_rotation": "any notable sector themes or empty string",
  "macro_concerns": "any macro themes mentioned or empty string",
  "noise_filtered": 3,
  "signal_posts": 8
}}"""


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

    return f"""You are a market sentiment analyst reviewing historical sentiment data for ${ticker}.

# SENTIMENT HISTORY (chronological)
{facts_block}

# TASK
{task}

# RULES
1. Focus on CHANGES over time — not just the latest reading
2. Identify phase transitions: bullish->bearish, low confidence->high confidence, etc.
3. Call out specific dates and catalysts when sentiment shifted
4. Note if themes are evolving, recurring, or contradicting each other
5. Be concise and actionable — a trader should read this in 30 seconds

# OUTPUT FORMAT
Return ONLY valid JSON. No text before or after.

{{
  "current_phase": "bullish or bearish or neutral or transitioning",
  "confidence_trend": "rising or falling or stable",
  "phase_changes": [
    {{"date": "YYYY-MM-DD", "from": "bearish", "to": "bullish", "catalyst": "brief reason"}}
  ],
  "key_insight": "1-2 sentence summary of the most important pattern",
  "outlook": "what the trajectory suggests going forward",
  "data_points": 5
}}"""
