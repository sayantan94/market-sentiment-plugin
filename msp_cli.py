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
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

# Rich logging for analysis modules — shows Bedrock/Memory calls cleanly
logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    handlers=[RichHandler(console=console, show_path=False, show_time=False, markup=True)],
)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("analysis").setLevel(logging.INFO)

SENTIMENT_STYLE = {
    "bullish": "bold green",
    "bearish": "bold red",
    "neutral": "bold yellow",
    "mixed": "bold yellow",
    "transitioning": "bold yellow",
}


def _header(subtitle=""):
    """Print a compact CLI header."""
    title = Text("$ Market Sentiment", style="bold cyan")
    if subtitle:
        title.append(f" · {subtitle}", style="dim")
    console.print(title)
    console.print()


# --- Daemon helpers ---

def _is_server_up(port):
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
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        os.remove(PID_FILE)
        return None


def _start_daemon(port):
    from config.settings import PID_FILE, LOG_FILE, DATA_DIR
    os.makedirs(DATA_DIR, exist_ok=True)
    log_f = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.server:app",
         "--host", "0.0.0.0", "--port", str(port)],
        stdout=log_f, stderr=log_f, start_new_session=True,
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    for _ in range(20):
        time.sleep(0.3)
        if _is_server_up(port):
            return True
    return False


def _stop_daemon():
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
    if _is_server_up(port):
        return True
    with console.status("Starting backend...", spinner="dots"):
        if _start_daemon(port):
            console.print(f"  Backend started on :{port}", style="green")
            return True
    console.print(f"  Backend failed to start", style="red")
    from config.settings import LOG_FILE
    console.print(f"  Check {LOG_FILE}", style="dim")
    return False


# --- Commands ---

def cmd_start(args):
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT
    if _is_server_up(port):
        console.print(f"  Backend already running on :{port}", style="green")
        return
    with console.status("Starting backend...", spinner="dots"):
        ok = _start_daemon(port)
    if ok:
        console.print(f"  Backend started on :{port}", style="green")
    else:
        console.print(f"  Backend failed to start", style="red")
        from config.settings import LOG_FILE
        console.print(f"  Check {LOG_FILE}", style="dim")


def cmd_stop(args):
    if _stop_daemon():
        console.print("  Backend stopped", style="green")
    else:
        console.print("  Backend not running", style="dim")


def cmd_doctor(args):
    import urllib.request
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT

    _header("Doctor")

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column("label", style="bold", width=14)
    tbl.add_column("value")

    # Backend
    pid = _read_pid()
    if _is_server_up(port):
        pid_label = f" (pid {pid})" if pid else ""
        tbl.add_row("Backend", Text(f"online :{port}{pid_label}", style="green"))
    else:
        tbl.add_row("Backend", Text("offline — run: msp-cli start", style="red"))

    # Posts
    from config.settings import POSTS_FILE
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE) as f:
            count = len(json.load(f))
        tbl.add_row("Posts", f"{count} saved")
    else:
        tbl.add_row("Posts", Text("none", style="dim"))

    # AWS
    try:
        import boto3
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        acct = identity.get("Account", "?")
        tbl.add_row("AWS", Text(f"authenticated (account {acct})", style="green"))
    except Exception:
        val = Text("not configured\n", style="red")
        val.append("  export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...\n", style="cyan")
        val.append("  or: aws configure", style="cyan")
        tbl.add_row("AWS", val)

    # Scan
    if _is_server_up(port):
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/scan", timeout=2)
            data = json.loads(resp.read())
            status = data.get("status", "none")
            if status != "none":
                tbl.add_row("Last scan", f"{status} ({data.get('count', 0)} posts)")
            else:
                tbl.add_row("Last scan", Text("none", style="dim"))
        except Exception:
            pass

    console.print(tbl)
    console.print()


def cmd_serve(args):
    import uvicorn
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT
    _header("Serve")
    console.print(f"  Collector on :{port}")
    uvicorn.run("backend.server:app", host="0.0.0.0", port=port, reload=args.reload)


def cmd_analyze(args):
    from analysis.analyze import (
        load_posts, group_by_ticker, analyze_ticker,
        infer_tickers, analyze_market, archive_posts,
    )
    from analysis.memory import store_sentiment, store_market_sentiment
    from config.settings import POSTS_FILE

    _header("Analyze")

    posts = load_posts()
    if not posts:
        console.print("  No posts. Browse X and click $ to save.", style="dim")
        return

    tagged = [p for p in posts if p.get("tickers")]
    untagged = [p for p in posts if not p.get("tickers")]
    console.print(f"  {len(posts)} posts ({len(tagged)} tagged, {len(untagged)} untagged)")

    # Infer tickers
    if untagged and not args.dry_run:
        with console.status(f"Inferring tickers for {len(untagged)} untagged posts...", spinner="dots"):
            t0 = time.time()
            inferred = infer_tickers(untagged)
            dur = time.time() - t0
        for post, tickers in inferred:
            post["tickers"] = tickers
            post["tickers_inferred"] = True
            tagged.append(post)
        console.print(f"  Inferred {len(inferred)} tickers ({dur:.1f}s)", style="dim")

    groups = group_by_ticker(tagged)
    if args.ticker:
        t = args.ticker.upper()
        groups = {t: groups[t]} if t in groups else {}
    if not groups:
        console.print("  No tickers to analyze.", style="dim")
        return

    ticker_summary = " ".join(f"[bold cyan]${t}[/]({len(p)})" for t, p in groups.items())
    console.print(f"  Tickers: {ticker_summary}")
    console.print()

    # Per-ticker analysis
    results = []
    for ticker, ticker_posts in groups.items():
        if ticker == "MARKET":
            continue
        if args.dry_run:
            console.print(f"  ${ticker}: [yellow]dry run[/]")
            continue

        n_ss = sum(1 for p in ticker_posts if p.get("screenshot_file"))
        label = f"Analyzing ${ticker} — {len(ticker_posts)} posts"
        if n_ss:
            label += f", {n_ss} screenshots"

        with console.status(label, spinner="dots"):
            t0 = time.time()
            result = analyze_ticker(ticker, ticker_posts)
            dur = time.time() - t0

        if result and result.get("status") != "error":
            results.append(result)
            post_times = [p.get("timestamp") or p.get("saved_at") for p in ticker_posts]
            store_sentiment(ticker, result, post_times=post_times)
            s = result.get("sentiment", "?")
            c = result.get("confidence", 0)
            style = SENTIMENT_STYLE.get(s, "dim")
            ss_label = f" + {n_ss} screenshots" if n_ss else ""
            console.print(f"  [green]✓[/] ${ticker}  [{style}]{s} {c}%[/]  {len(ticker_posts)} posts{ss_label}  [dim]({dur:.1f}s)[/]")
        else:
            console.print(f"  [red]✗[/] ${ticker}  [red]failed[/]  [dim]({dur:.1f}s)[/]")

    # Market
    if not args.ticker and not args.dry_run:
        with console.status("Analyzing overall market...", spinner="dots"):
            t0 = time.time()
            mr = analyze_market(posts)
            dur = time.time() - t0
        if mr and mr.get("status") != "error":
            all_times = [p.get("timestamp") or p.get("saved_at") for p in posts]
            store_market_sentiment(mr, post_times=all_times)
            s = mr.get("sentiment", "?")
            c = mr.get("confidence", 0)
            style = SENTIMENT_STYLE.get(s, "dim")
            console.print(f"  [green]✓[/] MARKET  [{style}]{s} {c}%[/]  risk={mr.get('risk_appetite', '?')}  [dim]({dur:.1f}s)[/]")
            results.append({**mr, "ticker": "MARKET"})

    # Results table
    if results:
        console.print()
        tbl = Table(box=box.ROUNDED, title="Results", title_style="bold")
        tbl.add_column("Ticker", style="cyan bold")
        tbl.add_column("Sentiment")
        tbl.add_column("Conf", justify="right")
        tbl.add_column("Posts", justify="right")
        tbl.add_column("Themes")
        tbl.add_column("Summary", max_width=40)

        for r in results:
            s = r.get("sentiment", "?")
            style = SENTIMENT_STYLE.get(s, "")
            themes = ", ".join(r.get("themes", [])[:3])
            summary = r.get("summary", "")[:80]
            ss = r.get("screenshots_analyzed", 0)
            posts_label = str(r.get("post_count", 0))
            if ss:
                posts_label += f"+{ss}ss"
            tbl.add_row(
                f"${r.get('ticker', '?')}",
                Text(s, style=style),
                f"{r.get('confidence', '?')}%",
                posts_label,
                themes,
                Text(summary, style="dim"),
            )

        console.print(tbl)

        # Archive
        archive_posts(posts)
        with open(POSTS_FILE, "w") as f:
            json.dump([], f)
        console.print(f"\n  [green]{len(results)} analyzed[/], archived, stored in memory")


def cmd_status(args):
    import urllib.request
    from config.settings import BACKEND_PORT
    port = args.port or BACKEND_PORT

    if _is_server_up(port):
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/posts", timeout=3)
            data = json.loads(resp.read())
            count = data.get("count", 0)
            console.print(f"  Backend [green]online[/] on :{port}")
            console.print(f"  {count} posts saved")
        except Exception:
            console.print(f"  Backend [green]online[/], posts unknown")
    else:
        console.print(f"  Backend [red]offline[/] — run: msp-cli start")


def cmd_posts(args):
    from config.settings import POSTS_FILE
    if not os.path.exists(POSTS_FILE):
        console.print("  No posts.", style="dim")
        return
    with open(POSTS_FILE) as f:
        posts = json.load(f)
    if args.ticker:
        t = args.ticker.upper()
        posts = [p for p in posts if t in p.get("tickers", [])]
    if not posts:
        console.print("  No posts.", style="dim")
        return

    tbl = Table(box=box.SIMPLE, padding=(0, 1))
    tbl.add_column("#", style="dim", width=4, justify="right")
    tbl.add_column("Handle", style="cyan")
    tbl.add_column("Tickers", style="bold")
    tbl.add_column("Text", max_width=50, no_wrap=True)

    for i, p in enumerate(posts, 1):
        tickers = " ".join(f"${t}" for t in p.get("tickers", []))
        text = p.get("text", "")[:80].replace("\n", " ")
        tbl.add_row(str(i), f"@{p.get('handle', '?')}", tickers, Text(text, style="dim"))

    console.print(tbl)
    console.print(f"  {len(posts)} total", style="dim")


def cmd_clear(args):
    from config.settings import POSTS_FILE
    if not os.path.exists(POSTS_FILE):
        console.print("  Nothing to clear.", style="dim")
        return
    with open(POSTS_FILE) as f:
        count = len(json.load(f))
    if count == 0:
        console.print("  Nothing to clear.", style="dim")
        return
    if not args.yes:
        if input(f"  Clear {count} posts? [y/N] ").strip().lower() != "y":
            return
    with open(POSTS_FILE, "w") as f:
        json.dump([], f)
    console.print(f"  Cleared {count} posts.", style="green")


def cmd_recall(args):
    import re
    from analysis.memory import recall_sentiment
    ticker = args.ticker.upper()
    if not re.match(r'^[A-Z0-9]{1,10}$', ticker):
        console.print(f"  Invalid ticker: {ticker}", style="red")
        console.print(f"  Usage: msp-cli recall AAPL --ask \"any phase changes?\"", style="dim")
        return

    with console.status(f"Recalling ${ticker} sentiment...", spinner="dots"):
        facts = recall_sentiment(ticker, query=args.query)

    if not facts:
        console.print(f"  No history for ${ticker}", style="dim")
        return

    console.print(f"  [bold cyan]${ticker}[/]: {len(facts)} memories\n")

    for i, fact in enumerate(facts, 1):
        lines = fact.strip().split("\n")
        # First line is the header (TICKER | date | sentiment)
        header = lines[0] if lines else ""
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

        panel_content = Text()
        panel_content.append(header + "\n", style="bold")
        if body:
            panel_content.append(body, style="dim")

        console.print(Panel(panel_content, title=f"[dim]#{i}[/]", border_style="dim", width=80, padding=(0, 1)))

    # --ask: LLM insight
    if args.ask is not None:
        question = args.ask if args.ask else None
        with console.status("Analyzing sentiment history...", spinner="dots"):
            try:
                from analysis.analyze import recall_insight
                result = recall_insight(ticker, facts, question)
            except Exception as e:
                console.print(f"  Insight error: {e}", style="red")
                return

        console.print()

        phase = result.get("current_phase", "?")
        style = SENTIMENT_STYLE.get(phase, "dim")
        trend = result.get("confidence_trend", "?")
        insight = result.get("key_insight", "")
        outlook = result.get("outlook", "")

        # Insight panel
        insight_text = Text()
        insight_text.append(f"Phase: ", style="bold")
        insight_text.append(f"{phase}", style=style)
        insight_text.append(f"  ·  Confidence: ", style="bold")
        insight_text.append(f"{trend}\n", style="dim")

        changes = result.get("phase_changes", [])
        if changes:
            insight_text.append("\nPhase changes:\n", style="bold")
            for ch in changes:
                fr = ch.get("from", "?")
                to = ch.get("to", "?")
                fr_style = SENTIMENT_STYLE.get(fr, "")
                to_style = SENTIMENT_STYLE.get(to, "")
                date = ch.get("date", "?")
                catalyst = ch.get("catalyst", "")
                insight_text.append(f"  {date}: ")
                insight_text.append(fr, style=fr_style)
                insight_text.append(" → ")
                insight_text.append(to, style=to_style)
                if catalyst:
                    insight_text.append(f" — {catalyst}", style="dim")
                insight_text.append("\n")

        if insight:
            insight_text.append(f"\n{insight}\n", style="cyan")
        if outlook:
            insight_text.append(f"Outlook: {outlook}", style="dim")

        console.print(Panel(insight_text, title="[bold]Insight[/]", border_style="cyan", width=80, padding=(0, 1)))


def cmd_research(args):
    import urllib.request, urllib.error
    from config.settings import BACKEND_PORT

    port = args.port or BACKEND_PORT
    base = f"http://localhost:{port}"
    query = args.query

    _header("Research")

    # 1. Auto-start backend
    if not _ensure_server(port):
        return

    # 2. Bedrock resolve query
    with console.status(f'Resolving "{query}"...', spinner="dots"):
        try:
            from analysis.analyze import research_query
            result = research_query(query)
        except Exception as e:
            console.print()
            err = str(e)
            console.print(f"  LLM error: {e}", style="red")
            if "credentials" in err.lower() or "NoCredentialsError" in err:
                console.print()
                console.print("  AWS credentials not configured:", style="bold")
                console.print("  export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...", style="cyan")
                console.print("  or: aws configure", style="cyan")
            return

    tickers = result.get("tickers", [])
    keywords = result.get("keywords", [])
    reasoning = result.get("reasoning", "")

    if not tickers:
        console.print("  No tickers identified for query.", style="red")
        return

    ticker_str = " ".join(f"[bold green]${t}[/]" for t in tickers)
    console.print(f"  Tickers:   {ticker_str}")
    console.print(f"  Keywords:  {', '.join(keywords)}", style="dim")
    if reasoning:
        console.print(f"  Reason:    {reasoning}", style="dim")
    console.print()

    # 3. POST scan
    max_posts = args.max_posts or 50
    scan_body = json.dumps({
        "tickers": tickers,
        "keywords": keywords,
        "maxPosts": max_posts,
        "query": query,
    }).encode()

    req = urllib.request.Request(
        f"{base}/scan", data=scan_body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        console.print(f"  Failed to queue scan: {e}", style="red")
        return

    console.print("  Scan queued — waiting for extension...")
    console.print("  [dim](Ctrl+C to detach, scan continues in extension)[/]\n")

    # 4. SSE stream
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
                console.print("  [yellow]Waiting for extension...[/]", end="\r")
            elif status == "running":
                phase_label = f" [{phase}]" if phase else ""
                console.print(f"  [cyan]Scanning ${current}{phase_label} — {count} posts[/]   ", end="\r")
            elif status == "done":
                console.print(f"\n  [green]Done! {count} posts captured.[/]")
                if tickers:
                    cmds = " ".join(f"msp-cli analyze -t {t}" for t in tickers[:2])
                    console.print(f"  Next: [dim]{cmds}[/]")
                break
            elif status in ("error", "none"):
                if status == "error":
                    console.print(f"\n  [red]Scan error.[/]")
                break

            last_status = status

    except KeyboardInterrupt:
        console.print(f"\n  [dim]Detached — scan continues in extension.[/]")
    except Exception as e:
        console.print(f"  SSE stream error: {e}", style="red")


def cmd_recall_market(args):
    from analysis.memory import recall_sentiment

    with console.status("Recalling market sentiment...", spinner="dots"):
        facts = recall_sentiment("MARKET")

    if not facts:
        console.print("  No market sentiment history", style="dim")
        return

    console.print(f"  [bold cyan]MARKET[/]: {len(facts)} memories\n")

    for i, fact in enumerate(facts, 1):
        lines = fact.strip().split("\n")
        header = lines[0] if lines else ""
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

        panel_content = Text()
        panel_content.append(header + "\n", style="bold")
        if body:
            panel_content.append(body, style="dim")

        console.print(Panel(panel_content, title=f"[dim]#{i}[/]", border_style="dim", width=80, padding=(0, 1)))


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
        _header()
        p.print_help()


if __name__ == "__main__":
    main()
