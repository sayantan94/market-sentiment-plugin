"""
Sentiment Analysis CLI

Usage:
    python -m analysis.analyze                  # All tickers
    python -m analysis.analyze --ticker SPY     # Single ticker
    python -m analysis.analyze --dry-run        # Preview only
    python -m analysis.analyze --clear          # Clear posts after analysis
"""

import argparse
import base64
import json
import logging
import os
from collections import defaultdict
from datetime import datetime

import boto3

from analysis.prompts import (
    build_sentiment_prompt,
    build_multimodal_content,
    build_ticker_inference_prompt,
    build_market_sentiment_prompt,
    build_research_query_prompt,
    build_recall_insight_prompt,
)
from config.settings import AWS_REGION, BEDROCK_MODEL_ID, POSTS_FILE, SEEN_FILE, SCREENSHOTS_DIR, DATA_DIR, ANALYSIS_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_posts():
    """Load unprocessed posts from data/posts.json"""
    try:
        with open(POSTS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"No posts file at {POSTS_FILE}")
        return []
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in {POSTS_FILE}")
        return []


def archive_posts(posts):
    """Move processed posts to date-based archive"""
    if not posts:
        return
    
    today = datetime.now().strftime("%Y-%m-%d")
    archive_dir = os.path.join(os.path.dirname(POSTS_FILE), "archive")
    os.makedirs(archive_dir, exist_ok=True)
    
    archive_file = os.path.join(archive_dir, f"posts_{today}.json")
    
    # Append to existing archive or create new
    existing = []
    if os.path.exists(archive_file):
        with open(archive_file, "r") as f:
            existing = json.load(f)
    
    existing.extend(posts)
    with open(archive_file, "w") as f:
        json.dump(existing, f, indent=2)
    
    # Also archive screenshots
    screenshots_archive = os.path.join(archive_dir, "screenshots", today)
    os.makedirs(screenshots_archive, exist_ok=True)
    
    for post in posts:
        fname = post.get("screenshot_file")
        if fname:
            src = os.path.join(SCREENSHOTS_DIR, fname)
            dst = os.path.join(screenshots_archive, fname)
            if os.path.exists(src):
                os.rename(src, dst)
    
    # Mark all archived URLs as seen for dedup
    urls = [p.get("url") for p in posts if p.get("url")]
    if urls:
        seen = set()
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, "r") as f:
                seen = set(json.load(f))
        seen.update(urls)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen), f)

    logger.info(f"Archived {len(posts)} posts to {archive_file}")


def group_by_ticker(posts):
    """Group posts by ticker symbol. Posts with no tickers are skipped."""
    groups = defaultdict(list)
    for post in posts:
        for ticker in post.get("tickers", []):
            groups[ticker].append(post)
    return dict(groups)


def load_screenshot_b64(filename, date_archive=None):
    """Load a screenshot file and return base64 string."""
    if date_archive:
        path = os.path.join(os.path.dirname(POSTS_FILE), "archive", "screenshots", date_archive, filename)
    else:
        path = os.path.join(SCREENSHOTS_DIR, filename)
    
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def call_bedrock_structured(content, schema, max_tokens=4000):
    """
    Call Bedrock Claude with native structured output (json_schema).
    content: string or list of content blocks (multimodal).
    schema: JSON Schema dict defining the output structure.
    Returns the parsed dict matching the schema.
    """
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": schema,
                }
            },
        }),
    )

    body = json.loads(response["body"].read())
    tokens_in = body.get("usage", {}).get("input_tokens", "?")
    tokens_out = body.get("usage", {}).get("output_tokens", "?")
    logger.info(f"Bedrock [{BEDROCK_MODEL_ID}] structured  {tokens_in} in → {tokens_out} out")

    for block in body.get("content", []):
        if block.get("type") == "text":
            return json.loads(block["text"])

    raise ValueError("No text block in response")


SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {
            "type": "string",
            "enum": ["bullish", "bearish", "neutral", "mixed"],
            "description": "Overall sentiment direction based on the posts analyzed",
        },
        "confidence": {
            "type": "integer",
            "description": "0-100 confidence score. Higher when posts agree and come from credible sources. Lower when mixed or insufficient data.",
        },
        "reasoning": {
            "type": "string",
            "description": "Step-by-step explanation: walk through the key posts, what the bulls say, what the bears say, and what tipped the balance. Cite @handles, price levels, and specific data points.",
        },
        "themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key themes identified across the posts (e.g. 'earnings beat', 'short squeeze', 'FDA approval')",
        },
        "catalysts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Upcoming events or triggers that could move the stock (e.g. 'earnings on Feb 28', 'Fed meeting next week')",
        },
        "key_levels": {
            "type": "string",
            "description": "Important price levels, support/resistance, or targets mentioned (e.g. '$130 support, $180 target'). Empty string if none.",
        },
        "notable_accounts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Twitter handles of credible accounts whose opinions carried weight in the analysis",
        },
        "summary": {
            "type": "string",
            "description": "4-5 sentence summary: what is the social media consensus, what are the strongest bull and bear arguments, and what should a trader watch for next",
        },
        "visual_insights": {
            "type": "string",
            "description": "Insights extracted from charts, option flow tables, or other images in screenshots. Empty string if no screenshots.",
        },
        "noise_filtered": {
            "type": "integer",
            "description": "Number of posts filtered out as noise (ads, spam, jokes, no real opinion)",
        },
        "signal_posts": {
            "type": "integer",
            "description": "Number of posts with real market signal used in the analysis",
        },
    },
    "required": ["sentiment", "confidence", "reasoning", "themes", "summary", "noise_filtered", "signal_posts"],
    "additionalProperties": False,
}

MARKET_SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {
            "type": "string",
            "enum": ["bullish", "bearish", "neutral", "mixed"],
            "description": "Overall market sentiment direction",
        },
        "confidence": {
            "type": "integer",
            "description": "0-100 confidence score for the market sentiment reading",
        },
        "reasoning": {
            "type": "string",
            "description": "Step-by-step explanation: what is the overall mood, what data supports it, where are the disagreements. Cite specific @handles and claims.",
        },
        "themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key market themes (e.g. 'AI rally', 'rate cut hopes', 'earnings season')",
        },
        "notable_accounts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Credible accounts whose views shaped the analysis",
        },
        "summary": {
            "type": "string",
            "description": "4-5 sentences on overall market mood, risk appetite, and what traders should watch for next",
        },
        "risk_appetite": {
            "type": "string",
            "enum": ["risk-on", "risk-off", "neutral"],
            "description": "Overall risk appetite: risk-on (buying dips, chasing momentum), risk-off (hedging, rotating to safety), or neutral",
        },
        "sector_rotation": {
            "type": "string",
            "description": "Notable sector rotation themes (e.g. 'rotation from tech to energy'). Empty string if none observed.",
        },
        "macro_concerns": {
            "type": "string",
            "description": "Macro concerns mentioned: rates, inflation, geopolitics, employment data. Empty string if none.",
        },
        "noise_filtered": {
            "type": "integer",
            "description": "Number of noise posts filtered out",
        },
        "signal_posts": {
            "type": "integer",
            "description": "Number of signal posts used in analysis",
        },
    },
    "required": ["sentiment", "confidence", "reasoning", "themes", "summary", "risk_appetite", "noise_filtered", "signal_posts"],
    "additionalProperties": False,
}

TICKER_INFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "inferences": {
            "type": "array",
            "description": "One entry per input post, in the same order",
            "items": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "0-based index of the post from the input list",
                    },
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Identified ticker symbols (e.g. ['NVDA', 'AMD']). Empty array if the post should be skipped.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why these tickers were assigned, or why the post was skipped",
                    },
                },
                "required": ["index", "tickers", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["inferences"],
    "additionalProperties": False,
}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "tickers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "1-5 stock ticker symbols, most relevant first (e.g. ['HOOD'] for 'robinhood stock')",
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Search keywords for X/Twitter. Cashtags like $HOOD are added automatically, so use plain terms (e.g. ['robinhood', 'HOOD earnings'])",
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why these tickers and keywords were chosen",
        },
    },
    "required": ["tickers", "keywords", "reasoning"],
    "additionalProperties": False,
}

RECALL_INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "current_phase": {
            "type": "string",
            "enum": ["bullish", "bearish", "neutral", "transitioning"],
            "description": "Current sentiment phase based on the most recent data points",
        },
        "confidence_trend": {
            "type": "string",
            "enum": ["rising", "falling", "stable"],
            "description": "Direction of confidence scores over time",
        },
        "phase_changes": {
            "type": "array",
            "description": "List of sentiment phase transitions detected in the history",
            "items": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date of the phase change (YYYY-MM-DD)",
                    },
                    "from": {
                        "type": "string",
                        "description": "Previous sentiment phase (e.g. 'bearish')",
                    },
                    "to": {
                        "type": "string",
                        "description": "New sentiment phase (e.g. 'bullish')",
                    },
                    "catalyst": {
                        "type": "string",
                        "description": "What caused the shift (e.g. 'earnings beat expectations')",
                    },
                },
                "required": ["date", "from", "to", "catalyst"],
                "additionalProperties": False,
            },
        },
        "key_insight": {
            "type": "string",
            "description": "1-2 sentence summary of the single most important pattern in the data",
        },
        "outlook": {
            "type": "string",
            "description": "What the sentiment trajectory suggests going forward for traders",
        },
        "data_points": {
            "type": "integer",
            "description": "Number of historical sentiment data points analyzed",
        },
    },
    "required": ["current_phase", "confidence_trend", "phase_changes", "key_insight", "outlook", "data_points"],
    "additionalProperties": False,
}


def research_query(query):
    """Use Bedrock structured output to convert a query into tickers + keywords."""
    prompt = build_research_query_prompt(query)
    return call_bedrock_structured(prompt, RESEARCH_SCHEMA, max_tokens=500)


def recall_insight(ticker, facts, question=None):
    """Run LLM analysis over recalled sentiment memories to identify phase changes."""
    prompt = build_recall_insight_prompt(ticker, facts, question)
    return call_bedrock_structured(prompt, RECALL_INSIGHT_SCHEMA, max_tokens=1000)


def infer_tickers(untagged_posts, dry_run=False):
    """
    Use LLM to identify tickers for posts without explicit $TICKER mentions.
    Returns list of (post, inferred_tickers) tuples.
    """
    if not untagged_posts:
        return []

    logger.info(f"Inferring tickers for {len(untagged_posts)} untagged posts")

    if dry_run:
        logger.info(f"[DRY RUN] would infer tickers for {len(untagged_posts)} posts")
        return []

    try:
        prompt = build_ticker_inference_prompt(untagged_posts)
        result = call_bedrock_structured(prompt, TICKER_INFERENCE_SCHEMA, max_tokens=2000)

        results = []
        for inf in result.get("inferences", []):
            idx = inf.get("index", -1)
            tickers = inf.get("tickers", [])
            if 0 <= idx < len(untagged_posts) and tickers:
                post = untagged_posts[idx]
                real_tickers = [t for t in tickers if t != "SKIP"]
                if real_tickers:
                    results.append((post, real_tickers))
                    logger.info(
                        f"  @{post['handle']}: inferred {', '.join(real_tickers)} "
                        f"— {inf.get('reason', '')[:60]}"
                    )

        logger.info(f"Inferred tickers for {len(results)}/{len(untagged_posts)} posts")
        return results

    except Exception as e:
        logger.error(f"Ticker inference failed: {e}")
        return []


def analyze_market(all_posts, dry_run=False):
    """Run overall market sentiment analysis across ALL posts."""
    if not all_posts:
        return None

    logger.info(f"MARKET: analyzing overall sentiment from {len(all_posts)} posts")

    if dry_run:
        logger.info("[DRY RUN] would analyze overall market sentiment")
        return None

    try:
        prompt = build_market_sentiment_prompt(all_posts)
        result = call_bedrock_structured(prompt, MARKET_SENTIMENT_SCHEMA, max_tokens=1500)
        result["post_count"] = len(all_posts)
        result["analyzed_at"] = datetime.now().isoformat()
        logger.info(
            f"MARKET: {result.get('sentiment', '?')} {result.get('confidence', '?')}% "
            f"risk={result.get('risk_appetite', '?')}"
        )
        return result
    except Exception as e:
        logger.error(f"MARKET: analysis failed - {e}")
        return None


def analyze_ticker(ticker, posts, dry_run=False):
    """Run sentiment analysis for a single ticker (with screenshots if available)"""
    screenshots = {}
    for i, p in enumerate(posts):
        fname = p.get("screenshot_file")
        if fname:
            b64 = load_screenshot_b64(fname)
            if b64:
                screenshots[i] = b64

    has_screenshots = len(screenshots) > 0
    logger.info(f"{ticker}: {len(posts)} posts, {len(screenshots)} screenshots")

    if dry_run:
        logger.info(f"{ticker}: [DRY RUN] would call Bedrock ({'multimodal' if has_screenshots else 'text-only'})")
        return None

    try:
        if has_screenshots:
            content = build_multimodal_content(ticker, posts, screenshots)
        else:
            content = build_sentiment_prompt(ticker, posts)

        result = call_bedrock_structured(content, SENTIMENT_SCHEMA)
        result["ticker"] = ticker
        result["post_count"] = len(posts)
        result["screenshots_analyzed"] = len(screenshots)
        result["analyzed_at"] = datetime.now().isoformat()

        logger.info(
            f"{ticker}: {result.get('sentiment', '?')} "
            f"{result.get('confidence', '?')}% "
            f"({result.get('signal_posts', '?')} signal / "
            f"{result.get('noise_filtered', '?')} noise)"
        )

        return result

    except Exception as e:
        logger.error(f"{ticker}: analysis failed - {e}")
        return {"ticker": ticker, "status": "error", "error": str(e)}


def save_analysis(results):
    """Persist analysis results to data/analysis.json keyed by ticker.
    Merges with existing results so previous tickers are preserved
    until overwritten by a new analysis run."""
    existing = {}
    if os.path.exists(ANALYSIS_FILE):
        try:
            with open(ANALYSIS_FILE, "r") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = {}

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()

    for r in results:
        ticker = r.get("ticker", "UNKNOWN")
        r["analyzed_at"] = ts
        existing[ticker] = r

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ANALYSIS_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    logger.info(f"Saved analysis for {len(results)} ticker(s) to {ANALYSIS_FILE}")


def main():
    from analysis.memory import store_sentiment, store_market_sentiment

    parser = argparse.ArgumentParser(description="Market sentiment analysis")
    parser.add_argument("--ticker", help="Analyze a single ticker")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without calling Bedrock"
    )
    args = parser.parse_args()

    posts = load_posts()
    if not posts:
        logger.info("No posts to analyze")
        return

    logger.info(f"Loaded {len(posts)} posts")

    # Step 1: Separate tagged vs untagged posts
    tagged = [p for p in posts if p.get("tickers")]
    untagged = [p for p in posts if not p.get("tickers")]
    logger.info(f"Tagged: {len(tagged)}, Untagged: {len(untagged)}")

    # Step 2: Infer tickers for untagged posts via LLM
    if untagged:
        inferred = infer_tickers(untagged, dry_run=args.dry_run)
        for post, tickers in inferred:
            post["tickers"] = tickers
            post["tickers_inferred"] = True
            tagged.append(post)

    # Step 3: Group by ticker
    groups = group_by_ticker(tagged)

    if not groups and not posts:
        logger.info("No posts to analyze")
        return

    if args.ticker:
        ticker = args.ticker.upper()
        if ticker not in groups:
            logger.info(f"No posts found for ${ticker}")
            return
        groups = {ticker: groups[ticker]}

    if groups:
        logger.info(f"Tickers: {', '.join(f'${t}({len(p)})' for t, p in groups.items())}")

    # Step 4: Per-ticker analysis
    results = []
    for ticker, ticker_posts in groups.items():
        if ticker == "MARKET":
            continue  # handled separately below
        result = analyze_ticker(ticker, ticker_posts, dry_run=args.dry_run)
        if result and result.get("status") != "error":
            results.append(result)
            if not args.dry_run:
                # Collect post timestamps for shift tracking
                post_times = [p.get("timestamp") or p.get("saved_at") for p in ticker_posts]
                store_sentiment(ticker, result, post_times=post_times)

    # Step 5: Overall market sentiment (all posts)
    if not args.ticker:
        market_result = analyze_market(posts, dry_run=args.dry_run)
        if market_result and market_result.get("status") != "error":
            if not args.dry_run:
                all_times = [p.get("timestamp") or p.get("saved_at") for p in posts]
                store_market_sentiment(market_result, post_times=all_times)
            results.append({**market_result, "ticker": "MARKET"})

    # Save analysis results locally for the extension to display
    if results and not args.dry_run:
        save_analysis(results)

    # Archive processed posts and clear active file
    if results and not args.dry_run:
        archive_posts(posts)
        with open(POSTS_FILE, "w") as f:
            json.dump([], f)
        logger.info("Cleared active posts file")

    # Summary
    if results:
        print("\n--- Results ---")
        for r in results:
            ss = r.get("screenshots_analyzed", 0)
            ss_label = f" + {ss} screenshots" if ss else ""
            ticker_label = r.get("ticker", "?")
            print(
                f"  ${ticker_label}: {r.get('sentiment', '?')} "
                f"{r.get('confidence', '?')}% "
                f"| {r.get('post_count', 0)} posts{ss_label} "
                f"| themes: {', '.join(r.get('themes', []))}"
            )
            if r.get("visual_insights"):
                print(f"    visual: {r['visual_insights']}")
            if r.get("risk_appetite"):
                print(f"    risk: {r['risk_appetite']} | macro: {r.get('macro_concerns', '-')}")


if __name__ == "__main__":
    main()
