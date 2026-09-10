/**
 * Jiro JavaScript/TypeScript SDK - Official client library for Jiro Search API.
 *
 * Provides a simple interface to all Jiro features:
 * - Web search with multiple engines
 * - Web scraping with content extraction
 * - AI-powered research with citations
 * - Real-time WebSocket streaming
 * - Batch operations
 * - Social media scraping
 *
 * Usage:
 *   import { JiroClient } from 'jiro-sdk';
 *
 *   const client = new JiroClient({ apiKey: 'your-api-key' });
 *   const results = await client.search('python web scraping');
 *   const content = await client.scrape('https://example.com');
 *   const answer = await client.aiAsk('What is Python?');
 *
 *   // Browser/Node.js compatible
 */

/**
 * @typedef {Object} SearchResult
 * @property {string} title
 * @property {string} snippet
 * @property {string} link
 * @property {string} source
 * @property {string} [displayed_link]
 */

/**
 * @typedef {Object} ScrapeResult
 * @property {string} title
 * @property {string} url
 * @property {string} content
 * @property {string} [html]
 */

/**
 * @typedef {Object} AIResponse
 * @property {string} answer
 * @property {Array<{title: string, url: string}>} citations
 */

/**
 * @typedef {Object} BatchJob
 * @property {string} job_id
 * @property {string} operation
 * @property {string} status
 * @property {number} total_items
 * @property {number} completed_items
 * @property {number} failed_items
 */

/**
 * @typedef {Object} JiroClientOptions
 * @property {string} [apiKey] - API key for authentication
 * @property {string} [baseUrl] - Base URL of Jiro server
 * @property {number} [timeout] - Request timeout in milliseconds
 */

const VERSION = '0.2.15';

/**
 * Base error class for Jiro SDK errors.
 */
class JiroError extends Error {
  /**
   * @param {string} message
   * @param {number} [status=0]
   * @param {*} [data=null]
   */
  constructor(message, status = 0, data = null) {
    super(message);
    this.name = 'JiroError';
    this.status = status;
    this.data = data;
  }
}

/**
 * Authentication error.
 */
class AuthenticationError extends JiroError {
  constructor(message = 'Invalid API key') {
    super(message, 401);
    this.name = 'AuthenticationError';
  }
}

/**
 * Rate limit error.
 */
class RateLimitError extends JiroError {
  constructor(message = 'Rate limit exceeded') {
    super(message, 429);
    this.name = 'RateLimitError';
  }
}

/**
 * Not found error.
 */
class NotFoundError extends JiroError {
  constructor(message = 'Resource not found') {
    super(message, 404);
    this.name = 'NotFoundError';
  }
}

/**
 * Server error.
 */
class ServerError extends JiroError {
  constructor(message = 'Internal server error') {
    super(message, 500);
    this.name = 'ServerError';
  }
}

/**
 * Jiro client for browser and Node.js environments.
 *
 * @example
 * // Browser
 * const client = new JiroClient({ apiKey: 'your-key' });
 * const results = await client.search('test');
 *
 * @example
 * // Node.js
 * const { JiroClient } = require('jiro-sdk');
 * const client = new JiroClient({ apiKey: 'your-key' });
 * const results = await client.search('test');
 */
class JiroClient {
  /**
   * Create a new Jiro client.
   * @param {JiroClientOptions} [options={}]
   */
  constructor(options = {}) {
    /** @type {string|null} */
    this.apiKey = options.apiKey || null;

    /** @type {string} */
    this.baseUrl = (options.baseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '');

    /** @type {number} */
    this.timeout = options.timeout || 30000;
  }

  /**
   * Make an HTTP request.
   * @private
   * @param {string} method
   * @param {string} path
   * @param {Object|null} [body=null]
   * @param {Object|null} [params=null]
   * @returns {Promise<any>}
   * @throws {JiroError}
   */
  async _request(method, path, body = null, params = null) {
    let url = `${this.baseUrl}${path}`;

    if (params) {
      const searchParams = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) {
          searchParams.set(key, String(value));
        }
      }
      const qs = searchParams.toString();
      if (qs) url += `?${qs}`;
    }

    const headers = {
      'Accept': 'application/json',
      'User-Agent': `jiro-sdk-js/${VERSION}`,
    };

    if (this.apiKey) {
      headers['X-API-Key'] = this.apiKey;
    }

    if (body) {
      headers['Content-Type'] = 'application/json';
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    try {
      const response = await fetch(url, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      const data = await response.json();

      if (response.status === 401) throw new AuthenticationError();
      if (response.status === 429) throw new RateLimitError();
      if (response.status === 404) throw new NotFoundError();
      if (response.status >= 500) throw new ServerError(data.error || 'Server error');

      if (!response.ok) {
        throw new JiroError(data.error || data.detail || 'Request failed', response.status, data);
      }

      return data;
    } catch (error) {
      if (error instanceof JiroError) throw error;
      if (error.name === 'AbortError') {
        throw new JiroError('Request timeout', 0);
      }
      throw new JiroError(error.message, 0);
    } finally {
      clearTimeout(timeoutId);
    }
  }

  // ── Search ──────────────────────────────────────────────────────────────

  /**
   * Search the web.
   * @param {string} query - Search query
   * @param {Object} [options={}] - Search options
   * @param {string} [options.engine='google'] - Search engine
   * @param {number} [options.numResults=10] - Number of results
   * @param {string} [options.type='web'] - Search type (web, images, news, videos)
   * @param {string} [options.location='us'] - Location
   * @param {string} [options.language='en'] - Language
   * @returns {Promise<Object>} Search results
   */
  async search(query, options = {}) {
    return this._request('GET', '/search.json', null, {
      q: query,
      engine: options.engine || 'google',
      num: options.numResults || 10,
      type: options.type || 'web',
      location: options.location || 'us',
      language: options.language || 'en',
    });
  }

  /**
   * Search multiple engines in parallel.
   * @param {string} query - Search query
   * @param {Object} [options={}] - Search options
   * @returns {Promise<Object>} Search results
   */
  async searchParallel(query, options = {}) {
    return this._request('GET', '/search.json', null, {
      q: query,
      num: options.numResults || 10,
      parallel: true,
      num_engines: Math.min(options.numEngines || 3, 5),
    });
  }

  /**
   * Search for images.
   * @param {string} query - Search query
   * @param {Object} [options={}] - Options
   * @returns {Promise<Object>} Image results
   */
  async searchImages(query, options = {}) {
    return this.search(query, { ...options, type: 'images' });
  }

  /**
   * Search for news.
   * @param {string} query - Search query
   * @param {Object} [options={}] - Options
   * @returns {Promise<Object>} News results
   */
  async searchNews(query, options = {}) {
    return this.search(query, { ...options, type: 'news' });
  }

  /**
   * Search for videos.
   * @param {string} query - Search query
   * @param {Object} [options={}] - Options
   * @returns {Promise<Object>} Video results
   */
  async searchVideos(query, options = {}) {
    return this.search(query, { ...options, type: 'videos' });
  }

  // ── Scrape ──────────────────────────────────────────────────────────────

  /**
   * Scrape a URL and extract content.
   * @param {string} url - URL to scrape
   * @param {Object} [options={}] - Scrape options
   * @param {string} [options.format='markdown'] - Output format
   * @returns {Promise<Object>} Scraped content
   */
  async scrape(url, options = {}) {
    return this._request('POST', '/scrape', {
      url,
      format: options.format || 'markdown',
    });
  }

  /**
   * Scrape multiple URLs in batch.
   * @param {string[]} urls - URLs to scrape
   * @param {Object} [options={}] - Options
   * @returns {Promise<Object>} Batch job
   */
  async scrapeBatch(urls, options = {}) {
    return this._request('POST', '/batch/scrape', {
      urls,
      format: options.format || 'markdown',
      max_concurrent: options.maxConcurrent || 5,
    });
  }

  // ── AI ──────────────────────────────────────────────────────────────────

  /**
   * Ask an AI research question with citations.
   * @param {string} query - Research question
   * @param {Object} [options={}] - AI options
   * @param {number} [options.maxSources=5] - Max sources to cite
   * @returns {Promise<Object>} AI response with answer and citations
   */
  async aiAsk(query, options = {}) {
    return this._request('POST', '/ai/search', {
      query,
      max_sources: options.maxSources || 5,
    });
  }

  /**
   * Get current AI configuration.
   * @returns {Promise<Object>} AI config
   */
  async aiConfig() {
    return this._request('GET', '/ai/config');
  }

  /**
   * Configure AI provider.
   * @param {Object} config - Provider configuration
   * @returns {Promise<Object>} Updated config
   */
  async aiSetup(config) {
    return this._request('POST', '/ai/config', config);
  }

  // ── Social ──────────────────────────────────────────────────────────────

  /**
   * Scrape social media content.
   * @param {string} url - Social media URL
   * @param {Object} [options={}] - Options
   * @returns {Promise<Object>} Scraped content
   */
  async socialScrape(url, options = {}) {
    return this._request('POST', '/social/scrape', {
      url,
      platform: options.platform,
    });
  }

  /**
   * Search on social platform.
   * @param {string} query - Search query
   * @param {string} [platform='reddit'] - Platform name
   * @param {number} [numResults=10] - Number of results
   * @returns {Promise<Object>} Search results
   */
  async socialSearch(query, platform = 'reddit', numResults = 10) {
    return this._request('GET', `/social/${platform}/search`, null, {
      q: query,
      num: numResults,
    });
  }

  // ── System ──────────────────────────────────────────────────────────────

  /**
   * Get system status.
   * @returns {Promise<Object>} System status
   */
  async status() {
    return this._request('GET', '/status');
  }

  /**
   * Get usage statistics.
   * @param {number} [days=7] - Number of days
   * @returns {Promise<Object>} Usage stats
   */
  async usage(days = 7) {
    return this._request('GET', '/usage', null, { days });
  }

  /**
   * List installed plugins.
   * @returns {Promise<Object>} Plugin list
   */
  async plugins() {
    return this._request('GET', '/plugins');
  }

  /**
   * Health check endpoint.
   * @returns {Promise<Object>} Health status
   */
  async health() {
    return this._request('GET', '/health');
  }

  /**
   * Get Prometheus metrics.
   * @returns {Promise<string>} Metrics text
   */
  async metrics() {
    const response = await fetch(`${this.baseUrl}/metrics`, {
      headers: { 'Accept': 'text/plain' },
    });
    return response.text();
  }

  // ── Batch ───────────────────────────────────────────────────────────────

  /**
   * Execute multiple searches in batch.
   * @param {string[]} queries - Search queries
   * @param {Object} [options={}] - Options
   * @returns {Promise<Object>} Batch job
   */
  async batchSearch(queries, options = {}) {
    return this._request('POST', '/batch/search', {
      queries,
      engine: options.engine || 'google',
      num_results: options.numResults || 10,
    });
  }

  /**
   * Get batch job status.
   * @param {string} jobId - Job ID
   * @returns {Promise<Object>} Job status
   */
  async batchJob(jobId) {
    return this._request('GET', `/batch/jobs/${jobId}`);
  }

  // ── WebSocket ───────────────────────────────────────────────────────────

  /**
   * Create WebSocket connection for real-time search streaming.
   * @param {string} query - Search query
   * @param {Object} [options={}] - Options
   * @returns {WebSocket} WebSocket connection
   */
  createSearchStream(query, options = {}) {
    if (typeof WebSocket === 'undefined') {
      throw new JiroError('WebSocket not supported in this environment');
    }

    const params = new URLSearchParams({
      query,
      engine: options.engine || 'google',
      num: options.num || 10,
    });

    const wsUrl = this.baseUrl.replace(/^http/, 'ws') + `/ws/search?${params}`;
    return new WebSocket(wsUrl);
  }
}

// Export for different environments
if (typeof module !== 'undefined' && module.exports) {
  // Node.js
  module.exports = {
    JiroClient,
    JiroError,
    AuthenticationError,
    RateLimitError,
    NotFoundError,
    ServerError,
    VERSION,
  };
  module.exports.default = JiroClient;
}

if (typeof window !== 'undefined') {
  // Browser
  window.JiroClient = JiroClient;
  window.JiroError = JiroError;
  window.AuthenticationError = AuthenticationError;
  window.RateLimitError = RateLimitError;
  window.NotFoundError = NotFoundError;
  window.ServerError = ServerError;
}

// ESM export
export {
  JiroClient,
  JiroError,
  AuthenticationError,
  RateLimitError,
  NotFoundError,
  ServerError,
  VERSION,
};
export default JiroClient;
