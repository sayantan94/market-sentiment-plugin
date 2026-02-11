#!/usr/bin/env python3
"""msp — Market Sentiment Plugin CLI"""

import argparse
import json
import os
import sys
import time

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"

BANNER = f"""{CYAN}{BOLD}
  ███╗   ███╗  █████╗  ██████╗  ██╗  ██╗ ███████╗ ████████╗
  ████╗ ████║ ██╔══██╗ ██╔══██╗ ██║ ██╔╝ ██╔════╝ ╚══██╔══╝
  ██╔████╔██║ ███████║ ██████╔╝ █████╔╝  █████╗      ██║
  ██║╚██╔╝██║ ██╔══██║ ██╔══██╗ ██╔═██╗  ██╔══╝      ██║
  ██║ ╚═╝ ██║ ██║  ██║ ██║  ██║ ██║  ██╗ ███████╗    ██║
  ╚═╝     ╚═╝ ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚══════╝    ╚═╝

  ███████╗ ███████╗ ███╗   ██╗ ████████╗ ██╗ ███╗   ███╗ ███████╗ ███╗   ██╗ ████████╗
  ██╔════╝ ██╔════╝ ████╗  ██║ ╚══██╔══╝ ██║ ████╗ ████║ ██╔════╝ ████╗  ██║ ╚══██╔══╝
  ███████╗ █████╗   ██╔██╗ ██║    ██║    ██║ ██╔████╔██║ █████╗   ██╔██╗ ██║    ██║
  ╚════██║ ██╔══╝   ██║╚██╗██║    ██║    ██║ ██║╚██╔╝██║ ██╔══╝   ██║╚██╗██║    ██║
  ███████║ ███████╗ ██║ ╚████║    ██║    ██║ ██║ ╚═╝ ██║ ███████╗ ██║ ╚████║    ██║
  ╚══════╝ ╚══════╝ ╚═╝  ╚═══╝    ╚═╝    ╚═╝ ╚═╝     ╚═╝ ╚══════╝ ╚═╝  ╚═══╝    ╚═╝{RESET}
"""

S_COLOR = {"bullish": GREEN, "bearish": RED, "neutral": YELLOW, "mixed": YELLOW}


def cmd_serve(args):
    import uvicorn
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT
    print(BANNER)
    print(f"  Collector on :{port}")
    uvicorn.run("backend.server:app", host="0.0.0.0", port=port, reload=args.reload)


def cmd_analyze(args):
    from analysis.analyze import (
        load_posts, group_by_ticker, analyze_ticker,
        infer_tickers, analyze_market, archive_posts,
    )
    from analysis.memory import store_sentiment, store_market_sentiment
    from config.settings import POSTS_FILE

    print(BANNER)
    posts = load_posts()
    if not posts:
        print(f"  {DIM}No posts. Browse X and click $ to save.{RESET}")
        return

    tagged = [p for p in posts if p.get("tickers")]
    untagged = [p for p in posts if not p.get("tickers")]
    print(f"  {len(posts)} posts ({len(tagged)} tagged, {len(untagged)} untagged)")

    # Infer tickers for untagged
    if untagged and not args.dry_run:
        t0 = time.time()
        for post, tickers in infer_tickers(untagged):
            post["tickers"] = tickers
            post["tickers_inferred"] = True
            tagged.append(post)
        print(f"  Inferred tickers ({time.time()-t0:.1f}s)")

    groups = group_by_ticker(tagged)
    if args.ticker:
        t = args.ticker.upper()
        groups = {t: groups[t]} if t in groups else {}
    if not groups:
        print(f"  {DIM}No tickers to analyze.{RESET}")
        return

    print(f"  {' '.join(f'${t}({len(p)})' for t,p in groups.items())}\n")

    # Per-ticker
    results = []
    for ticker, ticker_posts in groups.items():
        if ticker == "MARKET":
            continue
        t0 = time.time()
        if args.dry_run:
            print(f"  ${ticker}: {YELLOW}dry run{RESET}")
            continue
        result = analyze_ticker(ticker, ticker_posts)
        dur = time.time() - t0
        if result and result.get("status") != "error":
            results.append(result)
            post_times = [p.get("timestamp") or p.get("saved_at") for p in ticker_posts]
            store_sentiment(ticker, result, post_times=post_times)
            s = result.get("sentiment", "?")
            c = result.get("confidence", 0)
            sc = S_COLOR.get(s, DIM)
            print(f"  ${ticker}: {sc}{s} {c}%{RESET} ({dur:.1f}s)")
        else:
            print(f"  ${ticker}: {RED}failed{RESET} ({dur:.1f}s)")

    # Market
    if not args.ticker and not args.dry_run:
        t0 = time.time()
        mr = analyze_market(posts)
        dur = time.time() - t0
        if mr and mr.get("status") != "error":
            all_times = [p.get("timestamp") or p.get("saved_at") for p in posts]
            store_market_sentiment(mr, post_times=all_times)
            s = mr.get("sentiment", "?")
            c = mr.get("confidence", 0)
            sc = S_COLOR.get(s, DIM)
            print(f"  MARKET: {sc}{s} {c}%{RESET} risk={mr.get('risk_appetite','?')} ({dur:.1f}s)")
            results.append({**mr, "ticker": "MARKET"})

    # Archive
    if results:
        archive_posts(posts)
        with open(POSTS_FILE, "w") as f:
            json.dump([], f)
        print(f"\n  {GREEN}{len(results)} analyzed, stored in memory{RESET}")


def cmd_status(args):
    import urllib.request, urllib.error
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT
    base = f"http://localhost:{port}"

    try:
        urllib.request.urlopen(f"{base}/health", timeout=3)
    except Exception:
        print(f"  Backend {RED}offline{RESET} — run: msp serve")
        return

    try:
        resp = urllib.request.urlopen(f"{base}/posts", timeout=3)
        data = json.loads(resp.read())
        count = data.get("count", 0)
        print(f"  Backend {GREEN}online{RESET} on :{port}")
        print(f"  {count} posts saved")
    except Exception:
        print(f"  Backend {GREEN}online{RESET}, posts unknown")


def cmd_posts(args):
    from config.settings import POSTS_FILE
    if not os.path.exists(POSTS_FILE):
        print(f"  {DIM}No posts.{RESET}")
        return
    with open(POSTS_FILE) as f:
        posts = json.load(f)
    if args.ticker:
        t = args.ticker.upper()
        posts = [p for p in posts if t in p.get("tickers", [])]
    if not posts:
        print(f"  {DIM}No posts.{RESET}")
        return
    for i, p in enumerate(posts, 1):
        tickers = " ".join(f"${t}" for t in p.get("tickers", []))
        text = p.get("text", "")[:80]
        print(f"  {i}. @{p.get('handle','?')} {tickers}  {DIM}{text}{RESET}")
    print(f"  {len(posts)} total")


def cmd_clear(args):
    from config.settings import POSTS_FILE
    if not os.path.exists(POSTS_FILE):
        print(f"  {DIM}Nothing to clear.{RESET}")
        return
    with open(POSTS_FILE) as f:
        count = len(json.load(f))
    if count == 0:
        print(f"  {DIM}Nothing to clear.{RESET}")
        return
    if not args.yes:
        if input(f"  Clear {count} posts? [y/N] ").strip().lower() != "y":
            return
    with open(POSTS_FILE, "w") as f:
        json.dump([], f)
    print(f"  Cleared {count} posts.")


def cmd_recall(args):
    from analysis.memory import recall_sentiment
    ticker = args.ticker.upper()
    facts = recall_sentiment(ticker, query=args.query)
    if not facts:
        print(f"  {DIM}No history for ${ticker}{RESET}")
        return
    print(f"  ${ticker}: {len(facts)} memories\n")
    for i, fact in enumerate(facts, 1):
        print(f"  [{i}] {fact.strip()}\n")


def cmd_recall_market(args):
    from analysis.memory import recall_sentiment
    facts = recall_sentiment("MARKET")
    if not facts:
        print(f"  {DIM}No market sentiment history{RESET}")
        return
    print(f"  MARKET: {len(facts)} memories\n")
    for i, fact in enumerate(facts, 1):
        print(f"  [{i}] {fact.strip()}\n")


def main():
    p = argparse.ArgumentParser(prog="msp", description="Market Sentiment Plugin")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="Start collector backend")
    s.add_argument("-p", "--port", type=int)
    s.add_argument("--reload", action="store_true")

    s = sub.add_parser("analyze", help="Run sentiment analysis")
    s.add_argument("-t", "--ticker")
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("status", help="Backend health")
    s.add_argument("-p", "--port", type=int)

    s = sub.add_parser("posts", help="List saved posts")
    s.add_argument("-t", "--ticker")

    s = sub.add_parser("clear", help="Clear saved posts")
    s.add_argument("-y", "--yes", action="store_true")

    s = sub.add_parser("recall", help="Recall ticker sentiment")
    s.add_argument("ticker")
    s.add_argument("-q", "--query")

    sub.add_parser("recall-market", help="Recall market sentiment")

    args = p.parse_args()
    cmds = {
        "serve": cmd_serve, "analyze": cmd_analyze, "status": cmd_status,
        "posts": cmd_posts, "clear": cmd_clear, "recall": cmd_recall,
        "recall-market": cmd_recall_market,
    }
    if args.cmd in cmds:
        try:
            cmds[args.cmd](args)
        except KeyboardInterrupt:
            sys.exit(0)
    else:
        print(BANNER)
        p.print_help()


if __name__ == "__main__":
    main()
