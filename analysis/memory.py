"""
Bedrock AgentCore Memory for Market Sentiment
Ported from oi/memory.py — same patterns, different memory name/namespace.

API notes (critical):
- MemoryClient uses snake_case: memory_id, actor_id, session_id
- Messages are tuples: [(content, "ASSISTANT")] NOT dicts
- retrieve_memories() NOT retrieve_memory_records()
- list_memories() returns list of dicts with 'id' key, NO 'name' field
- create_memory_and_wait() returns result["id"] NOT result["memoryId"]
"""

import time
import logging
import boto3
from datetime import datetime
from bedrock_agentcore.memory import MemoryClient
from config.settings import AWS_REGION

logger = logging.getLogger(__name__)

MEMORY_NAME = "market_sentiment"
EVENT_EXPIRY_DAYS = 30

_memory_client = None
_control_client = None
_memory_id = None


def _get_memory_client():
    """MemoryClient — snake_case params, handles events + retrieval"""
    global _memory_client
    if _memory_client is None:
        _memory_client = MemoryClient(region_name=AWS_REGION)
    return _memory_client


def _get_control_client():
    """boto3 control plane — camelCase, for get_memory to resolve name->ID"""
    global _control_client
    if _control_client is None:
        _control_client = boto3.client(
            "bedrock-agentcore-control", region_name=AWS_REGION
        )
    return _control_client


def _get_memory_id():
    """
    Get or cache the memory ID.

    Strategy:
    1. list_memories via MemoryClient (returns dicts with 'id' key, NO 'name')
    2. For each, call get_memory via boto3 control plane to check the name
    3. If not found, create a new memory store
    4. If "already exists" error, retry listing
    """
    global _memory_id
    if _memory_id is not None:
        return _memory_id

    client = _get_memory_client()
    control = _get_control_client()

    # Step 1: List memories and resolve by name
    try:
        memories = client.list_memories()
        logger.info(f"list_memories returned {len(memories)} entries")

        for mem in memories:
            mid = mem.get("id", "")
            if not mid:
                continue

            if mid.startswith(MEMORY_NAME):
                _memory_id = mid
                logger.info(f"Found memory by ID prefix: {_memory_id}")
                return _memory_id

            try:
                detail = control.get_memory(memoryId=mid)
                mem_detail = detail.get("memory", detail)
                name = mem_detail.get("name", "")
                if name == MEMORY_NAME:
                    _memory_id = mid
                    logger.info(f"Found memory by name lookup: {_memory_id}")
                    return _memory_id
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"list_memories failed: {e}")

    # Step 2: Not found — create
    try:
        _memory_id = _create_memory()
        return _memory_id
    except Exception as e:
        if "already exists" in str(e).lower():
            logger.warning(
                f"Memory '{MEMORY_NAME}' already exists, retrying list..."
            )
            try:
                memories = client.list_memories()
                for mem in memories:
                    mid = mem.get("id", "")
                    if mid and mid.startswith(MEMORY_NAME):
                        _memory_id = mid
                        logger.info(f"Found memory on retry: {_memory_id}")
                        return _memory_id
            except Exception as e2:
                logger.error(f"Retry list failed: {e2}")
        else:
            logger.error(f"Failed to create memory: {e}")

    return _memory_id


def _create_memory():
    """One-time setup: create the memory store"""
    client = _get_memory_client()

    result = client.create_memory_and_wait(
        name=MEMORY_NAME,
        description="Market sentiment from social media posts — stores daily sentiment episodes and extracted facts",
        strategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "sentiment_facts",
                    "description": "Key sentiment facts: ticker bias, confidence, themes, notable accounts",
                    "namespaces": ["/facts/{actorId}/"],
                }
            },
            {
                "summaryMemoryStrategy": {
                    "name": "sentiment_summaries",
                    "description": "Rolling summaries of daily sentiment analysis episodes",
                    "namespaces": ["/summaries/{actorId}/{sessionId}/"],
                }
            },
        ],
        event_expiry_days=EVENT_EXPIRY_DAYS,
    )

    memory_id = result.get("id") or result.get("memoryId", "")
    logger.info(f"Created Bedrock Memory store: {memory_id}")
    return memory_id


def store_sentiment(ticker, sentiment_result):
    """
    Store compact sentiment snapshot in Bedrock AgentCore memory.
    Format designed for easy retrieval by trading agents.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    memory_id = _get_memory_id()
    if not memory_id:
        logger.warning(f"{ticker}: skipping store — no memory ID")
        return

    client = _get_memory_client()

    direction = sentiment_result.get("sentiment", "unknown")
    confidence = sentiment_result.get("confidence", 0)
    post_count = sentiment_result.get("post_count", 0)
    themes = ", ".join(sentiment_result.get("themes", []))
    notable = ", ".join(
        f"@{a}" for a in sentiment_result.get("notable_accounts", [])
    )
    summary = sentiment_result.get("summary", "")

    content = (
        f"{ticker} | {today} | sentiment: {direction} {confidence}% | {post_count} posts\n"
        f"themes: {themes or 'none'}\n"
        f"notable: {notable or 'none'}\n"
        f"summary: {summary}\n"
    )

    try:
        t0 = time.time()
        client.create_event(
            memory_id=memory_id,
            actor_id=f"sentiment/{ticker}",
            session_id=f"sentiment-{today}",
            messages=[(content, "ASSISTANT")],
        )
        logger.info(
            f"{ticker}: stored sentiment ({direction} {confidence}%) ({time.time()-t0:.1f}s)"
        )
    except Exception as e:
        logger.warning(f"{ticker}: store_sentiment failed - {e}")


def recall_sentiment(ticker, query=None):
    """Retrieve historical sentiment from Bedrock Memory semantic store."""
    if query is None:
        query = f"{ticker} sentiment direction, confidence, themes, notable accounts"

    memory_id = _get_memory_id()
    if not memory_id:
        logger.warning(f"{ticker}: skipping recall — no memory ID")
        return []

    client = _get_memory_client()
    facts = []

    try:
        t0 = time.time()
        records = client.retrieve_memories(
            memory_id=memory_id,
            namespace=f"/summaries/sentiment/{ticker}/",
            query=query,
            top_k=5,
        )

        for record in records:
            content = record.get("content", {})
            if isinstance(content, dict):
                text = content.get("text", "")
            elif isinstance(content, str):
                text = content
            else:
                text = str(content)
            if text:
                facts.append(text)

        dur = time.time() - t0
        if facts:
            logger.info(f"{ticker}: recalled {len(facts)} sentiment facts ({dur:.1f}s)")
        else:
            logger.info(f"{ticker}: no sentiment facts found ({dur:.1f}s)")

    except Exception as e:
        logger.warning(f"{ticker}: recall failed - {e}")

    return facts
