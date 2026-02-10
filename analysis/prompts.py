"""Sentiment analysis prompt template for Bedrock Claude"""


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
