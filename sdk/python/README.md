# Jiro Python SDK

[![PyPI version](https://img.shields.io/pypi/v/jiro-sdk.svg)](https://pypi.org/project/jiro-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/jiro-sdk.svg)](https://pypi.org/project/jiro-sdk/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)

Official Python SDK for [Jiro Search API](https://github.com/DevAnimecx/jiro).

## Installation

```bash
pip install jiro-sdk
```

With WebSocket support:
```bash
pip install jiro-sdk[websocket]
```

## Quick Start

```python
from jiro_sdk import JiroClient

# Initialize client
client = JiroClient(
    api_key="your-api-key",
    base_url="http://127.0.0.1:8000"
)

# Search the web
results = client.search("python web scraping")
print(results["organic_results"])

# Scrape a URL
content = client.scrape("https://example.com")
print(content["content"])

# AI-powered research with citations
answer = client.ai_ask("What is Python?")
print(answer["answer"])

# Parallel search across multiple engines
results = client.search_parallel("AI news", num_engines=3)

# Search for images
images = client.search_images("cats")

# Search for news
news = client.search_news("technology")
```

## Async Usage

```python
import asyncio
from jiro_sdk import AsyncJiroClient

async def main():
    async with AsyncJiroClient(api_key="your-key") as client:
        results = await client.search("python web scraping")
        content = await client.scrape("https://example.com")
        answer = await client.ai_ask("What is Python?")

asyncio.run(main())
```

## Social Media Scraping

```python
# Scrape social media
result = client.social_scrape("https://reddit.com/r/python/comments/123")

# Search social platforms
results = client.social_search("python", platform="reddit")
```

## Batch Operations

```python
# Batch search
job = client.batch_search(
    queries=["python", "javascript", "go"],
    engine="google",
    num_results=5
)
print(f"Progress: {job.progress}%")

# Batch scrape
job = client.scrape_batch([
    "https://example.com",
    "https://httpbin.org/html"
])
```

## Error Handling

```python
from jiro_sdk import JiroClient, JiroError, AuthenticationError, RateLimitError

client = JiroClient(api_key="your-key")

try:
    results = client.search("test")
except AuthenticationError:
    print("Invalid API key")
except RateLimitError:
    print("Rate limit exceeded, please wait")
except JiroError as e:
    print(f"Error: {e}")
```

## Configuration

```python
client = JiroClient(
    api_key="your-api-key",           # Optional: API key
    base_url="http://localhost:8000",  # Server URL
    timeout=30.0                       # Request timeout
)

# Get system status
status = client.status()

# Get usage statistics
usage = client.usage(days=30)

# List plugins
plugins = client.plugins()
```

## Context Manager

```python
with JiroClient(api_key="your-key") as client:
    results = client.search("test")
# Client is automatically closed
```

## License

MIT License
