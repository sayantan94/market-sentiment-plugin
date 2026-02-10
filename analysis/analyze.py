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

from analysis.memory import store_sentiment, recall_sentiment
from analysis.prompts import build_sentiment_prompt, build_multimodal_content
from config.settings import AWS_REGION, BEDROCK_MODEL_ID, POSTS_FILE, SCREENSHOTS_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_posts():
    """Load posts from data/posts.json"""
    try:
        with open(POSTS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"No posts file at {POSTS_FILE}")
        return []
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in {POSTS_FILE}")
        return []


def group_by_ticker(posts):
    """Group posts by ticker symbol. Posts with no tickers are skipped."""
    groups = defaultdict(list)
    for post in posts:
        for ticker in post.get("tickers", []):
            groups[ticker].append(post)
    return dict(groups)


def load_screenshot_b64(filename):
    """Load a screenshot file and return base64 string."""
    path = os.path.join(SCREENSHOTS_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def call_bedrock(messages_content, max_tokens=2000):
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
    return body["content"][0]["text"]


def parse_response(response_text):
    """Parse JSON response from Bedrock"""
    json_start = response_text.find("{")
    json_end = response_text.rfind("}") + 1
    if json_start == -1 or json_end <= json_start:
        raise ValueError("No valid JSON found in response")
    return json.loads(response_text[json_start:json_end])


def analyze_ticker(ticker, posts, dry_run=False):
    """Run sentiment analysis for a single ticker (with screenshots if available)"""
    # Load screenshots for posts that have them
    screenshots = {}
    for i, p in enumerate(posts):
        fname = p.get("screenshot_file")
        if fname:
            b64 = load_screenshot_b64(fname)
            if b64:
                screenshots[i] = b64

    has_screenshots = len(screenshots) > 0
    logger.info(
        f"{ticker}: {len(posts)} posts, {len(screenshots)} screenshots"
    )

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
    parser = argparse.ArgumentParser(description="Market sentiment analysis")
    parser.add_argument("--ticker", help="Analyze a single ticker")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without calling Bedrock"
    )
    parser.add_argument(
        "--clear", action="store_true", help="Clear posts after analysis"
    )
    args = parser.parse_args()

    posts = load_posts()
    if not posts:
        logger.info("No posts to analyze")
        return

    logger.info(f"Loaded {len(posts)} posts")
    groups = group_by_ticker(posts)

    if not groups:
        logger.info("No posts with $TICKER mentions found")
        return

    if args.ticker:
        ticker = args.ticker.upper()
        if ticker not in groups:
            logger.info(f"No posts found for ${ticker}")
            return
        groups = {ticker: groups[ticker]}

    logger.info(f"Tickers: {', '.join(f'${t}({len(p)})' for t, p in groups.items())}")

    results = []
    for ticker, ticker_posts in groups.items():
        result = analyze_ticker(ticker, ticker_posts, dry_run=args.dry_run)
        if result and result.get("status") != "error":
            results.append(result)
            if not args.dry_run:
                store_sentiment(ticker, result)

    # Summary
    if results:
        print("\n--- Results ---")
        for r in results:
            ss = r.get("screenshots_analyzed", 0)
            ss_label = f" + {ss} screenshots" if ss else ""
            print(
                f"  ${r['ticker']}: {r.get('sentiment', '?')} "
                f"{r.get('confidence', '?')}% "
                f"| {r.get('post_count', 0)} posts{ss_label} "
                f"| themes: {', '.join(r.get('themes', []))}"
            )
            if r.get("visual_insights"):
                print(f"    visual: {r['visual_insights']}")

    if args.clear and not args.dry_run:
        with open(POSTS_FILE, "w") as f:
            json.dump([], f)
        logger.info("Cleared posts file")


if __name__ == "__main__":
    main()
