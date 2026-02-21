#!/usr/bin/env python3
"""msp-cli — Market Sentiment Plugin CLI"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("analysis").setLevel(logging.INFO)

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


# --- Daemon helpers ---

def _is_server_up(port):
    """Check if the backend is responding on the given port."""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
        return True
    except Exception:
        return False


def _read_pid():
    from config.settings import PID_FILE
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        # Check if process is alive
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        os.remove(PID_FILE)
        return None


def _start_daemon(port):
    """Launch the backend as a background daemon. Returns True if started."""
    from config.settings import PID_FILE, LOG_FILE, DATA_DIR
    os.makedirs(DATA_DIR, exist_ok=True)

    log_f = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.server:app",
         "--host", "0.0.0.0", "--port", str(port)],
        stdout=log_f,
        stderr=log_f,
        start_new_session=True,
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    # Wait for it to come up
    for _ in range(20):
        time.sleep(0.3)
        if _is_server_up(port):
            return True
    return False


def _stop_daemon():
    """Stop the daemon if running. Returns True if it was stopped."""
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    from config.settings import PID_FILE
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    return True


def _ensure_server(port):
    """Ensure backend is running — start daemon if needed. Returns True if up."""
    if _is_server_up(port):
        return True
    print(f"  {DIM}Starting backend on :{port}...{RESET}", end="", flush=True)
    if _start_daemon(port):
        print(f" {GREEN}ok{RESET}")
        return True
    else:
        print(f" {RED}failed{RESET}")
        from config.settings import LOG_FILE
        print(f"  {DIM}Check {LOG_FILE}{RESET}")
        return False


# --- Commands ---

def cmd_start(args):
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT
    if _is_server_up(port):
        print(f"  Backend already {GREEN}running{RESET} on :{port}")
        return
    print(f"  Starting backend on :{port}...", end="", flush=True)
    if _start_daemon(port):
        print(f" {GREEN}ok{RESET}")
    else:
        print(f" {RED}failed{RESET}")
        from config.settings import LOG_FILE
        print(f"  {DIM}Check {LOG_FILE}{RESET}")


def cmd_stop(args):
    if _stop_daemon():
        print(f"  Backend {GREEN}stopped{RESET}")
    else:
        print(f"  {DIM}Backend not running{RESET}")


def cmd_doctor(args):
    import urllib.request
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT

    print(BANNER)
    print(f"  {BOLD}Doctor{RESET}\n")

    # 1. Backend
    pid = _read_pid()
    if _is_server_up(port):
        pid_label = f" (pid {pid})" if pid else ""
        print(f"  Backend      {GREEN}online{RESET} :{port}{pid_label}")
    else:
        print(f"  Backend      {RED}offline{RESET} — run: msp-cli start")

    # 2. Posts file
    from config.settings import POSTS_FILE
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE) as f:
            count = len(json.load(f))
        print(f"  Posts         {count} saved")
    else:
        print(f"  Posts         {DIM}none{RESET}")

    # 3. AWS / Bedrock
    try:
        import boto3
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        acct = identity.get("Account", "?")
        print(f"  AWS           {GREEN}authenticated{RESET} (account {acct})")
    except Exception as e:
        print(f"  AWS           {RED}not configured{RESET}")
        print(f"                Fix: {CYAN}export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...{RESET}")
        print(f"                 or: {CYAN}aws configure{RESET}")

    # 4. Extension connectivity (try /scan endpoint)
    if _is_server_up(port):
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/scan", timeout=2)
            data = json.loads(resp.read())
            status = data.get("status", "none")
            if status != "none":
                print(f"  Last scan     {status} ({data.get('count', 0)} posts)")
            else:
                print(f"  Last scan     {DIM}none{RESET}")
        except Exception:
            pass

    print()


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

    if _is_server_up(port):
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/posts", timeout=3)
            data = json.loads(resp.read())
            count = data.get("count", 0)
            print(f"  Backend {GREEN}online{RESET} on :{port}")
            print(f"  {count} posts saved")
        except Exception:
            print(f"  Backend {GREEN}online{RESET}, posts unknown")
    else:
        print(f"  Backend {RED}offline{RESET} — run: msp-cli start")


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
    import re
    from analysis.memory import recall_sentiment
    ticker = args.ticker.upper()
    if not re.match(r'^[A-Z0-9]{1,10}$', ticker):
        print(f"  {RED}Invalid ticker: {ticker}{RESET}")
        print(f"  {DIM}Usage: msp-cli recall AAPL --ask \"any phase changes?\"{RESET}")
        return
    facts = recall_sentiment(ticker, query=args.query)
    if not facts:
        print(f"  {DIM}No history for ${ticker}{RESET}")
        return
    print(f"  ${ticker}: {len(facts)} memories\n")
    for i, fact in enumerate(facts, 1):
        print(f"  [{i}] {fact.strip()}\n")

    # --ask: run LLM insight over recalled memories
    if args.ask is not None:
        question = args.ask if args.ask else None  # empty string = default prompt
        print(f"  {DIM}Analyzing sentiment history...{RESET}")
        try:
            from analysis.analyze import recall_insight
            result = recall_insight(ticker, facts, question)
        except Exception as e:
            print(f"  {RED}Insight error: {e}{RESET}")
            return

        phase = result.get("current_phase", "?")
        sc = S_COLOR.get(phase, DIM)
        trend = result.get("confidence_trend", "?")
        insight = result.get("key_insight", "")
        outlook = result.get("outlook", "")

        print(f"  {BOLD}--- Insight ---{RESET}")
        print(f"  Phase: {sc}{phase}{RESET}  |  Confidence: {trend}")

        changes = result.get("phase_changes", [])
        if changes:
            print(f"  Phase changes:")
            for ch in changes:
                print(f"    {ch.get('date', '?')}: {ch.get('from', '?')} -> {ch.get('to', '?')} — {ch.get('catalyst', '')}")

        if insight:
            print(f"\n  {CYAN}{insight}{RESET}")
        if outlook:
            print(f"  {DIM}Outlook: {outlook}{RESET}")


def cmd_research(args):
    import urllib.request, urllib.error
    from config.settings import BACKEND_PORT

    port = args.port or BACKEND_PORT
    base = f"http://localhost:{port}"
    query = args.query

    print(BANNER)

    # 1. Auto-start backend if needed
    if not _ensure_server(port):
        return

    # 2. Call Bedrock to resolve query -> tickers + keywords
    print(f"  {DIM}Researching: \"{query}\"{RESET}")
    try:
        from analysis.analyze import research_query
        result = research_query(query)
    except Exception as e:
        err = str(e)
        print(f"  {RED}LLM error: {e}{RESET}")
        if "credentials" in err.lower() or "NoCredentialsError" in err:
            print(f"\n  {BOLD}AWS credentials not configured. Fix with either:{RESET}")
            print(f"  1. Set env vars: {CYAN}export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...{RESET}")
            print(f"  2. Run: {CYAN}aws configure{RESET}")
        return

    tickers = result.get("tickers", [])
    keywords = result.get("keywords", [])
    reasoning = result.get("reasoning", "")

    if not tickers:
        print(f"  {RED}No tickers identified for query.{RESET}")
        return

    print(f"  Tickers: {' '.join(f'{GREEN}${t}{RESET}' for t in tickers)}")
    print(f"  Keywords: {', '.join(keywords)}")
    if reasoning:
        print(f"  {DIM}{reasoning}{RESET}")
    print()

    # 3. POST scan to backend
    max_posts = args.max_posts or 50
    scan_body = json.dumps({
        "tickers": tickers,
        "keywords": keywords,
        "maxPosts": max_posts,
        "query": query,
    }).encode()

    req = urllib.request.Request(
        f"{base}/scan",
        data=scan_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"  {RED}Failed to queue scan: {e}{RESET}")
        return

    print(f"  Scan queued — waiting for extension to pick up...")
    print(f"  {DIM}(Ctrl+C to detach, scan continues in extension){RESET}\n")

    # 4. Connect to SSE stream for real-time progress
    try:
        sse_req = urllib.request.Request(f"{base}/scan/stream")
        sse_req.add_header("Accept", "text/event-stream")
        resp = urllib.request.urlopen(sse_req, timeout=300)

        last_status = ""
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            try:
                scan = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            status = scan.get("status", "none")
            count = scan.get("count", 0)
            current = scan.get("currentTicker", "")
            phase = scan.get("phase", "")

            if status == "pending" and last_status != "pending":
                print(f"  {YELLOW}Waiting for extension...{RESET}", end="\r")
            elif status == "running":
                phase_label = f" [{phase}]" if phase else ""
                print(f"  {CYAN}Scanning ${current}{phase_label} — {count} posts{RESET}   ", end="\r")
            elif status == "done":
                print(f"\n  {GREEN}Done! {count} posts captured.{RESET}")
                if tickers:
                    ticker_cmds = " ".join(f"msp-cli analyze -t {t}" for t in tickers[:2])
                    print(f"  Next: {DIM}{ticker_cmds}{RESET}")
                break
            elif status == "error":
                print(f"\n  {RED}Scan error.{RESET}")
                break
            elif status == "none":
                break

            last_status = status

    except KeyboardInterrupt:
        print(f"\n  {DIM}Detached — scan continues in extension.{RESET}")
    except Exception as e:
        print(f"  {RED}SSE stream error: {e}{RESET}")


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
    p = argparse.ArgumentParser(prog="msp-cli", description="Market Sentiment Plugin")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("start", help="Start backend daemon")
    s.add_argument("-p", "--port", type=int)

    sub.add_parser("stop", help="Stop backend daemon")

    s = sub.add_parser("doctor", help="Check setup health")
    s.add_argument("-p", "--port", type=int)

    s = sub.add_parser("serve", help="Start backend (foreground)")
    s.add_argument("-p", "--port", type=int)
    s.add_argument("--reload", action="store_true")

    s = sub.add_parser("analyze", help="Run sentiment analysis")
    s.add_argument("-t", "--ticker")
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("research", help="Research a topic via LLM + auto-scan")
    s.add_argument("query", help="Natural language query (e.g. 'robinhood stock')")
    s.add_argument("-n", "--max-posts", type=int, default=50)
    s.add_argument("-p", "--port", type=int)

    s = sub.add_parser("status", help="Backend health")
    s.add_argument("-p", "--port", type=int)

    s = sub.add_parser("posts", help="List saved posts")
    s.add_argument("-t", "--ticker")

    s = sub.add_parser("clear", help="Clear saved posts")
    s.add_argument("-y", "--yes", action="store_true")

    s = sub.add_parser("recall", help="Recall ticker sentiment")
    s.add_argument("ticker")
    s.add_argument("-q", "--query", help="Semantic search across memories")
    s.add_argument("--ask", nargs="?", const="", default=None,
                   help="Ask LLM to analyze sentiment history (optional custom question)")

    sub.add_parser("recall-market", help="Recall market sentiment")

    args = p.parse_args()
    cmds = {
        "start": cmd_start, "stop": cmd_stop, "doctor": cmd_doctor,
        "serve": cmd_serve, "analyze": cmd_analyze, "research": cmd_research,
        "status": cmd_status, "posts": cmd_posts, "clear": cmd_clear,
        "recall": cmd_recall, "recall-market": cmd_recall_market,
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
