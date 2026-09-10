/**
 * Jiro JavaScript/TypeScript SDK - Official client library for Jiro Search API.
 *
 * Provides a simple interface to all Jiro features:
 * - Web search with multiple engines
 * - Web scraping with content extraction
 * - AI-powered research with citations
 * - Real-time WebSocket streaming
 *
 * Usage:
 *   import { JiroClient } from 'jiro-sdk';
 *
 *   const client = new JiroClient({ apiKey: 'your-api-key' });
 *   const results = await client.search('python web scraping');
 *   const content = await client.scrape('https://example.com');
 *   const answer = await client.aiAsk('What is Python?');
 */

class JiroError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = 'JiroError';
    this.status = status;
    this.data = data;
  }
}

class AuthenticationError extends JiroError {
  constructor(message = 'Invalid API key') {
    super(message, 401);
    this.name = 'AuthenticationError';
  }
}

class RateLimitError extends JiroError {
  constructor(message = 'Rate limit exceeded') {
    super(message, 429);
    this.name = 'RateLimitError';
  }
}

class JiroClient {
  /**
   * Create a new Jiro client.
   * @param {Object} options - Client options
   * @param {string} options.apiKey - API key for authentication
   * @param {string} options.baseUrl - Base URL of Jiro server
   * @param {number} options.timeout - Request timeout in milliseconds
   */
  constructor(options = {}) {
    this.apiKey = options.apiKey || null;
    this.baseUrl = (options.baseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '');
    this.timeout = options.timeout || 30000;
  }

  /**
   * Make an HTTP request.
   * @private
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
      if (!response.ok) {
        throw new JiroError(data.error || data.detail || 'Request failed', response.status, data);
      }

      return data;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  // ---- Search ----

  /**
   * Search the web.
   * @param {string} query - Search query
   * @param {Object} options - Search options
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
   * @param {Object} options - Search options
   * @returns {Promise<Object>} Search results
   */
  async searchParallel(query, options = {}) {
    return this._request('GET', '/search.json', null, {
      q: query,
      num: options.numResults || 10,
      parallel: true,
      num_engines: options.numEngines || 3,
    });
  }

  // ---- Scrape ----

  /**
   * Scrape a URL and extract content.
   * @param {string} url - URL to scrape
   * @param {Object} options - Scrape options
   * @returns {Promise<Object>} Scraped content
   */
  async scrape(url, options = {}) {
    return this._request('POST', '/scrape', {
      url,
      format: options.format || 'markdown',
    });
  }

  // ---- AI ----

  /**
   * Ask an AI research question with citations.
   * @param {string} query - Research question
   * @param {Object} options - AI options
   * @returns {Promise<Object>} AI answer with citations
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

  // ---- System ----

  /**
   * Get system status.
   * @returns {Promise<Object>} System status
   */
  async status() {
    return this._request('GET', '/status');
  }

  /**
   * Get usage statistics.
   * @param {number} days - Number of days to look back
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

  // ---- WebSocket ----

  /**
   * Create a WebSocket connection for real-time search streaming.
   * @param {string} query - Search query
   * @param {Object} options - WebSocket options
   * @returns {Promise<WebSocket>} WebSocket connection
   */
  async createSearchStream(query, options = {}) {
    const params = new URLSearchParams({
      query,
      engine: options.engine || 'google',
      num: options.num || 10,
    });

    const wsUrl = this.baseUrl.replace(/^http/, 'ws') + `/ws/search?${params}`;
    return new WebSocket(wsUrl);
  }
}

// Export for Node.js and browsers
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { JiroClient, JiroError, AuthenticationError, RateLimitError };
}
if (typeof window !== 'undefined') {
  window.JiroClient = JiroClient;
}
