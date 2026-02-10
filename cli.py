#!/usr/bin/env python3
"""
Market Sentiment Plugin — Unified CLI

Usage:
    python cli.py serve                         # Start backend on :5050
    python cli.py analyze                       # Analyze all tickers
    python cli.py analyze --ticker SPY          # Single ticker
    python cli.py analyze --dry-run             # Preview only
    python cli.py analyze --clear               # Clear posts after analysis
    python cli.py status                        # Backend health + post count
    python cli.py posts                         # List saved posts
    python cli.py posts --ticker SPY            # Filter by ticker
    python cli.py clear                         # Clear all saved posts
    python cli.py recall SPY                    # Recall sentiment from memory
"""

import argparse
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("msp")


def cmd_serve(args):
    """Start the FastAPI backend server"""
    import uvicorn
    from config.settings import BACKEND_PORT

    port = args.port or BACKEND_PORT
    logger.info(f"Starting backend on :{port}")
    uvicorn.run(
        "backend.server:app",
        host="0.0.0.0",
        port=port,
        reload=args.reload,
    )


def cmd_analyze(args):
    """Run sentiment analysis on saved posts"""
    from analysis.analyze import (
        load_posts,
        group_by_ticker,
        analyze_ticker,
    )
    from analysis.memory import store_sentiment
    from config.settings import POSTS_FILE

    posts = load_posts()
    if not posts:
        print("No posts to analyze.")
        return

    groups = group_by_ticker(posts)
    if not groups:
        print("No posts with $TICKER mentions found.")
        return

    if args.ticker:
        ticker = args.ticker.upper()
        if ticker not in groups:
            print(f"No posts found for ${ticker}")
            return
        groups = {ticker: groups[ticker]}

    print(f"Posts: {len(posts)} total, {len(groups)} tickers")
    for t, p in groups.items():
        print(f"  ${t}: {len(p)} posts")
    print()

    results = []
    for ticker, ticker_posts in groups.items():
        result = analyze_ticker(ticker, ticker_posts, dry_run=args.dry_run)
        if result and result.get("status") != "error":
            results.append(result)
            if not args.dry_run:
                store_sentiment(ticker, result)

    if results:
        print("\n--- Results ---")
        for r in results:
            sentiment = r.get("sentiment", "?")
            confidence = r.get("confidence", "?")
            themes = ", ".join(r.get("themes", []))
            print(f"  ${r['ticker']}: {sentiment} {confidence}% | themes: {themes}")
            if r.get("summary"):
                print(f"    {r['summary']}")
        print()

    if args.clear and not args.dry_run:
        with open(POSTS_FILE, "w") as f:
            json.dump([], f)
        print("Cleared posts file.")


def cmd_status(args):
    """Check backend health and post count"""
    import urllib.request
    import urllib.error
    from config.settings import BACKEND_PORT

    port = args.port or BACKEND_PORT
    base = f"http://localhost:{port}"

    # Health
    try:
        resp = urllib.request.urlopen(f"{base}/health", timeout=3)
        data = json.loads(resp.read())
        print(f"Backend:  online ({base})")
    except (urllib.error.URLError, ConnectionRefusedError):
        print(f"Backend:  OFFLINE ({base})")
        print("          Run: python cli.py serve")
        return

    # Post count
    try:
        resp = urllib.request.urlopen(f"{base}/posts", timeout=3)
        data = json.loads(resp.read())
        count = data.get("count", 0)
        print(f"Posts:    {count} saved")

        if count > 0:
            # Show ticker breakdown
            from collections import Counter
            ticker_counts = Counter()
            for p in data.get("posts", []):
                for t in p.get("tickers", []):
                    ticker_counts[t] += 1
            if ticker_counts:
                breakdown = ", ".join(f"${t}({c})" for t, c in ticker_counts.most_common())
                print(f"Tickers:  {breakdown}")
    except Exception:
        print("Posts:    unknown")


def cmd_posts(args):
    """List saved posts"""
    from config.settings import POSTS_FILE

    if not os.path.exists(POSTS_FILE):
        print("No posts saved yet.")
        return

    with open(POSTS_FILE, "r") as f:
        posts = json.load(f)

    if not posts:
        print("No posts saved yet.")
        return

    if args.ticker:
        ticker = args.ticker.upper()
        posts = [p for p in posts if ticker in p.get("tickers", [])]
        if not posts:
            print(f"No posts for ${ticker}")
            return

    for i, p in enumerate(posts, 1):
        tickers = " ".join(f"${t}" for t in p.get("tickers", []))
        handle = p.get("handle", "?")
        text = p.get("text", "")
        # Truncate long text
        if len(text) > 120:
            text = text[:117] + "..."
        print(f"  {i}. @{handle} {tickers}")
        print(f"     {text}")
        print()

    print(f"Total: {len(posts)} posts")


def cmd_clear(args):
    """Clear all saved posts"""
    from config.settings import POSTS_FILE

    if not os.path.exists(POSTS_FILE):
        print("Nothing to clear.")
        return

    with open(POSTS_FILE, "r") as f:
        count = len(json.load(f))

    if count == 0:
        print("Nothing to clear.")
        return

    if not args.yes:
        confirm = input(f"Clear {count} posts? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    with open(POSTS_FILE, "w") as f:
        json.dump([], f)
    print(f"Cleared {count} posts.")


def cmd_recall(args):
    """Recall sentiment from Bedrock AgentCore memory"""
    from analysis.memory import recall_sentiment

    ticker = args.ticker.upper()
    print(f"Recalling sentiment for ${ticker}...")

    facts = recall_sentiment(ticker, query=args.query)
    if not facts:
        print("No sentiment history found.")
        return

    print(f"\n--- {len(facts)} memories ---")
    for i, fact in enumerate(facts, 1):
        print(f"\n[{i}]")
        print(fact)


def main():
    parser = argparse.ArgumentParser(
        prog="msp",
        description="Market Sentiment Plugin",
    )
    sub = parser.add_subparsers(dest="command")

    # serve
    p_serve = sub.add_parser("serve", help="Start backend server")
    p_serve.add_argument("-p", "--port", type=int, help="Port (default: 5050)")
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload on changes")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Run sentiment analysis")
    p_analyze.add_argument("--ticker", help="Single ticker to analyze")
    p_analyze.add_argument("--dry-run", action="store_true", help="Preview without calling Bedrock")
    p_analyze.add_argument("--clear", action="store_true", help="Clear posts after analysis")

    # status
    p_status = sub.add_parser("status", help="Backend health + post count")
    p_status.add_argument("-p", "--port", type=int, help="Port (default: 5050)")

    # posts
    p_posts = sub.add_parser("posts", help="List saved posts")
    p_posts.add_argument("--ticker", help="Filter by ticker")

    # clear
    p_clear = sub.add_parser("clear", help="Clear all saved posts")
    p_clear.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    # recall
    p_recall = sub.add_parser("recall", help="Recall sentiment from memory")
    p_recall.add_argument("ticker", help="Ticker symbol")
    p_recall.add_argument("--query", help="Custom search query")

    args = parser.parse_args()

    commands = {
        "serve": cmd_serve,
        "analyze": cmd_analyze,
        "status": cmd_status,
        "posts": cmd_posts,
        "clear": cmd_clear,
        "recall": cmd_recall,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
