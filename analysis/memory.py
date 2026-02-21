"""
Bedrock AgentCore Memory for Market Sentiment
"""

import time
import logging
import boto3
from datetime import datetime, timedelta

from config.settings import AWS_REGION, MEMORY_NAME, EVENT_EXPIRY_DAYS

logger = logging.getLogger(__name__)


class MemoryClient:
    def __init__(self):
        self.control = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)
        self.runtime = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
        self._memory_id = None

    @property
    def memory_id(self):
        if self._memory_id is None:
            self._memory_id = self._find_or_create_memory()
        return self._memory_id

    def _find_or_create_memory(self):
        """Find existing memory or create new one"""
        try:
            response = self.control.list_memories()
            for mem in response.get("memories", []):
                if MEMORY_NAME in mem.get("arn", ""):
                    memory_id = mem.get("id", "")
                    logger.info(f"Found memory: {memory_id}")
                    return memory_id
        except Exception as e:
            logger.warning(f"list_memories failed: {e}")

        return self._create_memory()

    def _create_memory(self):
        """Create new memory store"""
        try:
            response = self.control.create_memory(
                name=MEMORY_NAME,
                description="Market sentiment from social media posts",
                memoryStrategies=[
                    {
                        "semanticMemoryStrategy": {
                            "name": "sentiment_facts",
                            "description": "Key sentiment facts",
                            "namespaces": ["/facts/{actorId}/"],
                        }
                    },
                    {
                        "summaryMemoryStrategy": {
                            "name": "sentiment_summaries",
                            "description": "Rolling summaries of daily sentiment",
                            "namespaces": ["/summaries/{actorId}/{sessionId}/"],
                        }
                    },
                ],
                eventExpiryDuration=EVENT_EXPIRY_DAYS,
            )

            memory_id = response.get("memory", {}).get("id") or response.get("id", "")
            logger.info(f"Created memory: {memory_id}")

            # Wait for memory to be ready
            for _ in range(30):
                try:
                    mem = self.control.get_memory(memoryId=memory_id)
                    if mem.get("memory", {}).get("status") == "ACTIVE":
                        break
                except Exception:
                    pass
                time.sleep(1)

            return memory_id
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.warning(f"Memory exists, retrying list...")
                response = self.control.list_memories()
                for mem in response.get("memories", []):
                    if MEMORY_NAME in mem.get("arn", ""):
                        return mem.get("id", "")
            raise


_client = MemoryClient()


def store_sentiment(ticker, sentiment_result, post_times=None):
    """
    Store sentiment in Bedrock AgentCore memory.

    Args:
        ticker: Ticker symbol
        sentiment_result: Analysis result dict
        post_times: Optional list of ISO timestamps from the analyzed posts
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    analysis_time = now.strftime("%H:%M:%S")

    direction = sentiment_result.get("sentiment", "unknown")
    confidence = sentiment_result.get("confidence", 0)
    post_count = sentiment_result.get("post_count", 0)
    themes = ", ".join(sentiment_result.get("themes", []))
    notable = ", ".join(f"@{a}" for a in sentiment_result.get("notable_accounts", []))
    summary = sentiment_result.get("summary", "")

    # Build time range from post timestamps
    time_range = ""
    if post_times:
        valid = sorted([t for t in post_times if t])
        if valid:
            earliest = valid[0][:16].replace("T", " ")
            latest = valid[-1][:16].replace("T", " ")
            time_range = f" | posts: {earliest} → {latest}"

    content = (
        f"{ticker} | {today} {analysis_time}{time_range} | sentiment: {direction} {confidence}% | {post_count} posts\n"
        f"themes: {themes or 'none'}\n"
        f"notable: {notable or 'none'}\n"
        f"summary: {summary}\n"
    )

    try:
        _client.runtime.create_event(
            memoryId=_client.memory_id,
            actorId=f"sentiment/{ticker}",
            sessionId=f"sentiment-{today}",
            eventTimestamp=now.isoformat(),
            payload=[{
                "conversational": {
                    "content": {"text": content},
                    "role": "ASSISTANT"
                }
            }],
        )
        logger.info(f"{ticker}: stored sentiment ({direction} {confidence}%) at {analysis_time}")
    except Exception as e:
        logger.warning(f"{ticker}: store_sentiment failed - {e}")


def store_market_sentiment(sentiment_result, post_times=None):
    """Store overall market sentiment under actor 'sentiment/MARKET'."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    analysis_time = now.strftime("%H:%M:%S")

    direction = sentiment_result.get("sentiment", "unknown")
    confidence = sentiment_result.get("confidence", 0)
    post_count = sentiment_result.get("post_count", 0)
    themes = ", ".join(sentiment_result.get("themes", []))
    risk = sentiment_result.get("risk_appetite", "unknown")
    sector = sentiment_result.get("sector_rotation", "")
    macro = sentiment_result.get("macro_concerns", "")
    summary = sentiment_result.get("summary", "")

    time_range = ""
    if post_times:
        valid = sorted([t for t in post_times if t])
        if valid:
            earliest = valid[0][:16].replace("T", " ")
            latest = valid[-1][:16].replace("T", " ")
            time_range = f" | posts: {earliest} → {latest}"

    content = (
        f"MARKET | {today} {analysis_time}{time_range} | sentiment: {direction} {confidence}% | {post_count} posts\n"
        f"risk_appetite: {risk} | themes: {themes or 'none'}\n"
        f"sector_rotation: {sector or 'none'}\n"
        f"macro_concerns: {macro or 'none'}\n"
        f"summary: {summary}\n"
    )

    try:
        _client.runtime.create_event(
            memoryId=_client.memory_id,
            actorId="sentiment/MARKET",
            sessionId=f"sentiment-{today}",
            eventTimestamp=now.isoformat(),
            payload=[{
                "conversational": {
                    "content": {"text": content},
                    "role": "ASSISTANT"
                }
            }],
        )
        logger.info(f"MARKET: stored overall sentiment ({direction} {confidence}%) at {analysis_time}")
    except Exception as e:
        logger.warning(f"MARKET: store_market_sentiment failed - {e}")


def recall_sentiment(ticker, query=None):
    """Retrieve historical sentiment from Bedrock Memory.

    Without query: lists recent events for the ticker.
    With query: semantic search across the ticker's memories.
    """
    if query:
        return _recall_semantic(ticker, query)
    return _recall_events(ticker)


def _recall_events(ticker):
    """List events for a ticker across the last 30 days."""
    today = datetime.now()
    facts = []

    for i in range(30):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        session_id = f"sentiment-{day}"
        try:
            events_response = _client.runtime.list_events(
                memoryId=_client.memory_id,
                actorId=f"sentiment/{ticker}",
                sessionId=session_id,
                maxResults=10,
            )
            for event in events_response.get("events", []):
                for item in event.get("payload", []):
                    text = item.get("conversational", {}).get("content", {}).get("text", "")
                    if text:
                        facts.append(text)
        except Exception as e:
            logger.debug(f"{ticker}: no events for {day} - {e}")

    # Sort chronologically (each fact starts with "TICKER | YYYY-MM-DD HH:MM:SS")
    facts.sort()

    if facts:
        logger.info(f"{ticker}: recalled {len(facts)} events across 30 days")
    else:
        logger.info(f"{ticker}: no events found in last 30 days")

    return facts


def _recall_semantic(ticker, query):
    """Semantic search across a ticker's memories."""
    try:
        response = _client.runtime.retrieve_memory_records(
            memoryId=_client.memory_id,
            namespace=f"/facts/sentiment/{ticker}/",
            searchCriteria={
                "searchQuery": query,
                "topK": 10,
            },
        )

        facts = []
        for record in response.get("memoryRecords", []):
            content = record.get("content", {})
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            if text:
                facts.append(text)

        if facts:
            logger.info(f"{ticker}: semantic search found {len(facts)} results for '{query}'")
        else:
            logger.info(f"{ticker}: no results for '{query}'")

        return facts

    except Exception as e:
        logger.warning(f"{ticker}: semantic recall failed - {e}")
        return []
