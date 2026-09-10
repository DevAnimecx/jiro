/**
 * Jiro JavaScript/TypeScript SDK - Type Definitions
 *
 * @module jiro-sdk
 */

export declare const VERSION: string;

export declare class JiroError extends Error {
  status: number;
  data: any;
  constructor(message: string, status?: number, data?: any);
}

export declare class AuthenticationError extends JiroError {
  constructor(message?: string);
}

export declare class RateLimitError extends JiroError {
  constructor(message?: string);
}

export declare class NotFoundError extends JiroError {
  constructor(message?: string);
}

export declare class ServerError extends JiroError {
  constructor(message?: string);
}

export interface JiroClientOptions {
  apiKey?: string;
  baseUrl?: string;
  timeout?: number;
}

export interface SearchOptions {
  engine?: string;
  numResults?: number;
  type?: string;
  location?: string;
  language?: string;
}

export interface ScrapeOptions {
  format?: string;
}

export interface AIAskOptions {
  maxSources?: number;
}

export interface SearchResult {
  title: string;
  snippet: string;
  link: string;
  source: string;
  displayed_link?: string;
  position?: number;
}

export interface ScrapeResult {
  title: string;
  url: string;
  content: string;
  html?: string;
  markdown?: string;
}

export interface Citation {
  title: string;
  url: string;
}

export interface AIResponse {
  answer: string;
  citations: Citation[];
}

export interface BatchJob {
  job_id: string;
  operation: string;
  status: string;
  total_items: number;
  completed_items: number;
  failed_items: number;
  items?: any[];
}

export interface PluginInfo {
  name: string;
  version: string;
  author: string;
  description: string;
  types: string[];
}

export interface StatusResponse {
  version: string;
  status: string;
  engines: string[];
  auth_enabled: boolean;
}

export interface UsageResponse {
  requests: number;
  cached: number;
  tokens_in: number;
  tokens_out: number;
  by_endpoint: Array<{ endpoint: string; n: number }>;
}

export declare class JiroClient {
  apiKey: string | null;
  baseUrl: string;
  timeout: number;

  constructor(options?: JiroClientOptions);

  search(query: string, options?: SearchOptions): Promise<any>;
  searchParallel(query: string, options?: SearchOptions & { numEngines?: number }): Promise<any>;
  searchImages(query: string, options?: SearchOptions): Promise<any>;
  searchNews(query: string, options?: SearchOptions): Promise<any>;
  searchVideos(query: string, options?: SearchOptions): Promise<any>;

  scrape(url: string, options?: ScrapeOptions): Promise<ScrapeResult>;
  scrapeBatch(urls: string[], options?: ScrapeOptions & { maxConcurrent?: number }): Promise<BatchJob>;

  aiAsk(query: string, options?: AIAskOptions): Promise<AIResponse>;
  aiConfig(): Promise<any>;
  aiSetup(config: { provider: string; api_key: string; model?: string; base_url?: string }): Promise<any>;

  socialScrape(url: string, options?: { platform?: string }): Promise<any>;
  socialSearch(query: string, platform?: string, numResults?: number): Promise<any>;

  status(): Promise<StatusResponse>;
  usage(days?: number): Promise<UsageResponse>;
  plugins(): Promise<{ plugins: PluginInfo[] }>;
  health(): Promise<any>;
  metrics(): Promise<string>;

  batchSearch(queries: string[], options?: { engine?: string; numResults?: number }): Promise<BatchJob>;
  batchJob(jobId: string): Promise<BatchJob>;

  createSearchStream(query: string, options?: { engine?: string; num?: number }): WebSocket;
}

export default JiroClient;
