# Jiro JavaScript/TypeScript SDK

[![npm version](https://img.shields.io/npm/v/jiro-sdk.svg)](https://www.npmjs.com/package/jiro-sdk)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)

Official JavaScript/TypeScript SDK for [Jiro Search API](https://github.com/DevAnimecx/jiro).

## Installation

```bash
npm install jiro-sdk
```

## Quick Start

```javascript
import { JiroClient } from 'jiro-sdk';

// Initialize client
const client = new JiroClient({
  apiKey: 'your-api-key',
  baseUrl: 'http://127.0.0.1:8000'
});

// Search the web
const results = await client.search('python web scraping');
console.log(results.organic_results);

// Scrape a URL
const content = await client.scrape('https://example.com');
console.log(content.content);

// AI-powered research with citations
const answer = await client.aiAsk('What is Python?');
console.log(answer.answer);

// Parallel search across multiple engines
const parallelResults = await client.searchParallel('AI news', { numEngines: 3 });
```

## TypeScript Support

Full TypeScript definitions included:

```typescript
import { JiroClient, SearchResult, ScrapeResult, AIResponse } from 'jiro-sdk';

const client = new JiroClient({ apiKey: 'your-key' });

const results: SearchResult = await client.search('query');
const content: ScrapeResult = await client.scrape('https://example.com');
const answer: AIResponse = await client.aiAsk('What is Python?');
```

## Browser & Node.js

Works in both browser and Node.js environments:

```javascript
// Browser
import { JiroClient } from 'jiro-sdk';
const client = new JiroClient({ apiKey: 'your-key' });

// Node.js (CommonJS)
const { JiroClient } = require('jiro-sdk');
const client = new JiroClient({ apiKey: 'your-key' });
```

## WebSocket Streaming

```javascript
import { JiroClient } from 'jiro-sdk';

const client = new JiroClient({ apiKey: 'your-key' });

// Create a search stream
const ws = client.createSearchStream('AI news');

ws.onopen = () => {
  console.log('Connected to search stream');
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Received:', data);
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};

ws.onclose = () => {
  console.log('Stream closed');
};
```

## Search Options

```javascript
// Basic search
const results = await client.search('query');

// Search with options
const results = await client.search('query', {
  engine: 'bing',
  num: 20,
  type: 'images',
  location: 'uk',
  language: 'en'
});

// Search images
const images = await client.searchImages('cats');

// Search news
const news = await client.searchNews('technology');

// Search videos
const videos = await client.searchVideos('python tutorial');
```

## Scrape Options

```javascript
// Basic scrape
const content = await client.scrape('https://example.com');

// Scrape with format
const content = await client.scrape('https://example.com', {
  format: 'text'
});
```

## AI Research

```javascript
// Basic AI question
const answer = await client.aiAsk('What is Python?');
console.log(answer.answer);
answer.citations.forEach(c => {
  console.log(`- ${c.title}: ${c.url}`);
});

// AI with more sources
const answer = await client.aiAsk('Compare React vs Vue', {
  maxSources: 10
});
```

## Batch Operations

```javascript
// Batch search
const job = await client.batchSearch({
  queries: ['python', 'javascript', 'go'],
  engine: 'google',
  numResults: 5
});
console.log(`Progress: ${job.progress}%`);

// Batch scrape
const job = await client.batchScrape({
  urls: ['https://example.com', 'https://httpbin.org/html'],
  format: 'markdown'
});

// Check job status
const status = await client.batchJobStatus(job.jobId);
```

## Social Media

```javascript
// Scrape social media
const result = await client.socialScrape('https://reddit.com/r/python', 'reddit');

// Search social platforms
const results = await client.socialSearch('python', 'reddit', 10);
```

## System

```javascript
// Get system status
const status = await client.status();
console.log(`Version: ${status.version}`);

// Get usage stats
const usage = await client.usage(30);

// List plugins
const plugins = await client.plugins();

// Health check
const health = await client.health();
```

## Error Handling

```javascript
import { JiroClient, JiroError, AuthenticationError, RateLimitError } from 'jiro-sdk';

const client = new JiroClient({ apiKey: 'your-key' });

try {
  const results = await client.search('test');
} catch (error) {
  if (error instanceof AuthenticationError) {
    console.error('Invalid API key');
  } else if (error instanceof RateLimitError) {
    console.error('Rate limit exceeded');
  } else if (error instanceof JiroError) {
    console.error('Jiro error:', error.message);
  } else {
    console.error('Unknown error:', error);
  }
}
```

## License

MIT License
