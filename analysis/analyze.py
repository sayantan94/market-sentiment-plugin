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
from config.settings import AWS_REGION, BEDROCK_MODEL_ID, POSTS_FILE, SEEN_FILE, SCREENSHOTS_DIR, DATA_DIR

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


def call_bedrock(messages_content, max_tokens=20000):
    """
    Call Bedrock Claude with content blocks (text or text+images).
    messages_content: either a string (text-only) or a list of content blocks (multimodal).
    """
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    if isinstance(messages_content, str):
        content = messages_content
    else:
        content = messages_content

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}],
            }
        ),
    )

    body = json.loads(response["body"].read())
    text = body["content"][0]["text"]
    tokens_in = body.get("usage", {}).get("input_tokens", "?")
    tokens_out = body.get("usage", {}).get("output_tokens", "?")
    logger.info(f"Bedrock [{BEDROCK_MODEL_ID}] {tokens_in} in → {tokens_out} out")
    return text


def parse_response(response_text):
    """Parse JSON response from Bedrock"""
    json_start = response_text.find("{")
    json_end = response_text.rfind("}") + 1
    if json_start == -1 or json_end <= json_start:
        raise ValueError("No valid JSON found in response")
    return json.loads(response_text[json_start:json_end])


def research_query(query):
    """Use Bedrock LLM with tool_use to convert a query into structured tickers + keywords."""
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    tool_schema = {
        "name": "research_result",
        "description": "Return the tickers, keywords, and reasoning for a research query",
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1-5 stock ticker symbols, most relevant first (e.g. HOOD, NVDA)",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Search keywords for X/Twitter (cashtags added automatically)",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation of why these tickers/keywords were chosen",
                },
            },
            "required": ["tickers", "keywords", "reasoning"],
        },
    }

    prompt = build_research_query_prompt(query)

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [tool_schema],
            "tool_choice": {"type": "tool", "name": "research_result"},
        }),
    )

    body = json.loads(response["body"].read())
    tokens_in = body.get("usage", {}).get("input_tokens", "?")
    tokens_out = body.get("usage", {}).get("output_tokens", "?")
    logger.info(f"Bedrock [{BEDROCK_MODEL_ID}] tool_use  {tokens_in} in → {tokens_out} out")

    # Extract tool_use block — guaranteed structured JSON
    for block in body.get("content", []):
        if block.get("type") == "tool_use":
            return block["input"]

    raise ValueError("No tool_use block in response")


def recall_insight(ticker, facts, question=None):
    """Run LLM analysis over recalled sentiment memories to identify phase changes."""
    prompt = build_recall_insight_prompt(ticker, facts, question)
    response = call_bedrock(prompt, max_tokens=1000)
    return parse_response(response)


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
        response_text = call_bedrock(prompt, max_tokens=2000)

        # Parse JSON array
        json_start = response_text.find("[")
        json_end = response_text.rfind("]") + 1
        if json_start == -1 or json_end <= json_start:
            raise ValueError("No valid JSON array found")

        inferences = json.loads(response_text[json_start:json_end])
        results = []

        for inf in inferences:
            idx = inf.get("index", -1)
            tickers = inf.get("tickers", [])
            if 0 <= idx < len(untagged_posts) and tickers:
                post = untagged_posts[idx]
                # Filter out SKIP entries
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
        response_text = call_bedrock(prompt, max_tokens=1500)
        result = parse_response(response_text)
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

        response_text = call_bedrock(content)
        result = parse_response(response_text)
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
