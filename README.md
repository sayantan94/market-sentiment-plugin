# Market Sentiment Plugin

A Chrome extension + local backend that captures posts from X (Twitter) while you scroll, then runs AI-powered sentiment analysis using Claude on AWS Bedrock. Results are stored in Bedrock AgentCore long-term memory for downstream trading agents to consume.

## How It Works

```
X (Twitter)                   Local Machine                    AWS
+-----------+    POST     +-------------+    invoke_model   +---------+
| Chrome    | ---------> | FastAPI     | ----------------> | Bedrock |
| Extension |  text +    | :5050       |   text + images   | Claude  |
| ($ btn)   |  screenshot| posts.json  | <---------------- | Sonnet  |
+-----------+            +------+------+   JSON sentiment  +----+----+
                                |                               |
                                |  store_sentiment()            |
                                +------------------------------>+
                                         AgentCore Memory
```

1. **Browse X** -- the extension injects a `$` button on every post
2. **Click `$`** -- extracts text, handle, timestamp, `$TICKER` mentions, and a screenshot of the post
3. **Posts are saved** locally via a FastAPI backend (`data/posts.json` + `data/screenshots/`)
4. **Run analysis** -- groups posts by ticker, sends text + screenshots to Claude Sonnet 4.5 on Bedrock
5. **Results stored** in Bedrock AgentCore memory for trading agents to retrieve later

## Prerequisites

- **Python 3.11+**
- **Google Chrome** (or Chromium-based browser)
- **AWS credentials** configured (`~/.aws/credentials` or environment variables) with access to:
  - Bedrock Runtime (`bedrock-runtime`) -- for Claude model invocation
  - Bedrock AgentCore (`bedrock-agentcore-control`) -- for long-term memory

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url> market-sentiment-plugin
cd market-sentiment-plugin
pip install -r requirements.txt
```

### 2. Load the Chrome extension

1. Open Chrome and navigate to `chrome://extensions`
2. Enable **Developer mode** (toggle in top-right)
3. Click **Load unpacked**
4. Select the `extension/` folder from this repo
5. The extension icon appears in your toolbar

### 3. Start the backend

```bash
python cli.py serve
```

This starts the collector API on `http://localhost:5050`. Keep it running while browsing.

## Usage

### Saving posts

1. Navigate to [x.com](https://x.com)
2. Scroll through your feed or search for tickers
3. Click the **`$`** button on any post to save it
   - Button turns **green** on success
   - A screenshot of the post is captured automatically
4. Posts with `$TICKER` mentions (e.g., `$SPY`, `$NVDA`) are tagged for analysis

### CLI commands

```bash
# Check backend status and saved post count
python cli.py status

# List all saved posts
python cli.py posts

# List posts for a specific ticker
python cli.py posts --ticker SPY

# Run sentiment analysis on all tickers
python cli.py analyze

# Analyze a single ticker
python cli.py analyze --ticker SPY

# Preview analysis without calling Bedrock (no cost)
python cli.py analyze --dry-run

# Analyze and clear posts after
python cli.py analyze --clear

# Clear all saved posts (with confirmation)
python cli.py clear

# Clear without confirmation
python cli.py clear -y

# Recall past sentiment from Bedrock AgentCore memory
python cli.py recall SPY
```

### Typical workflow

```bash
# Terminal 1: start backend
python cli.py serve

# Browse X, click $ on posts you find insightful...

# Terminal 2: check what you've saved
python cli.py status
python cli.py posts

# Run analysis
python cli.py analyze

# Output:
#   $SPY: bullish 72% | themes: gamma squeeze, call wall accumulation, earnings rotation
#   $NVDA: bearish 58% | themes: profit taking, resistance at $950

# Recall from memory later
python cli.py recall SPY
```

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Bedrock model for analysis |
| `BACKEND_PORT` | `5050` | Local backend port |

## Project Structure

```
market-sentiment-plugin/
├── cli.py                    # Unified CLI entry point
├── extension/                # Chrome Extension (Manifest V3)
│   ├── manifest.json         # Extension config + permissions
│   ├── content.js            # Injected into x.com -- $ button + screenshot capture
│   ├── content.css           # Button styles (matches X dark theme)
│   ├── popup.html/js         # Extension popup (status + controls)
│   └── background.js         # Service worker (badge count + tab capture)
├── backend/
│   ├── server.py             # FastAPI on :5050 (save/list/clear posts)
│   └── models.py             # Pydantic models
├── analysis/
│   ├── analyze.py            # Reads posts, calls Bedrock, parses results
│   ├── prompts.py            # Sentiment prompt templates (text + multimodal)
│   └── memory.py             # Bedrock AgentCore memory (store + recall)
├── config/
│   └── settings.py           # Configuration constants
├── data/
│   ├── posts.json            # Saved posts (gitignored)
│   └── screenshots/          # Post screenshots as PNGs (gitignored)
└── requirements.txt
```

## How Analysis Works

When you run `python cli.py analyze`:

1. Posts are grouped by `$TICKER` mention
2. For each ticker, a prompt is built with all post text + any available screenshots
3. **Multimodal analysis**: Claude sees both the text AND the screenshot images, extracting insights from charts, option flow tables, and other visual data that text extraction misses
4. Claude returns structured JSON: sentiment direction, confidence %, themes, notable accounts, and a summary
5. Results are stored in Bedrock AgentCore memory with:
   - **Actor ID**: `sentiment/{TICKER}` (e.g., `sentiment/SPY`)
   - **Session ID**: `sentiment-{date}` (e.g., `sentiment-2026-02-10`)
   - **Strategies**: semantic memory (for fact retrieval) + summary memory (rolling summaries)

## Data Format

### Saved post (`data/posts.json`)

```json
{
  "text": "Massive call flow on $SPY $580 strike, gamma squeeze setup",
  "handle": "spotgamma",
  "timestamp": "2026-02-10T14:32:00.000Z",
  "tickers": ["SPY"],
  "url": "https://x.com/spotgamma/status/1234567890",
  "screenshot_file": "a1b2c3d4e5f6g7h8.png",
  "saved_at": "2026-02-10T09:32:15.123456"
}
```

### Analysis result

```json
{
  "sentiment": "bullish",
  "confidence": 72,
  "themes": ["gamma squeeze", "call wall accumulation", "earnings rotation"],
  "notable_accounts": ["spotgamma", "unusual_whales"],
  "summary": "Strong bullish consensus driven by institutional call accumulation...",
  "visual_insights": "Chart shows clear breakout above $580 resistance with volume confirmation",
  "post_count": 15,
  "screenshots_analyzed": 8
}
```

## License

MIT
