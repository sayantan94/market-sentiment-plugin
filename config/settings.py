"""
Configuration settings for Market Sentiment Plugin
"""

import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)

# Paths
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")

# Backend
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "5050"))

# Bedrock AgentCore Memory
MEMORY_NAME = "market_sentiment"
EVENT_EXPIRY_DAYS = 30
