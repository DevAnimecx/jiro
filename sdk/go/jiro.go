// Package jiro provides an official Go client for the Jiro Search API.
//
// Features:
//   - Web search with multiple engines
//   - Web scraping with content extraction
//   - AI-powered research with citations
//   - Batch operations
//   - Social media scraping
//   - System monitoring
//
// Usage:
//
//	client := jiro.NewClient(jiro.WithAPIKey("your-api-key"))
//
//	// Search
//	results, err := client.Search("python web scraping", nil)
//
//	// Scrape
//	content, err := client.Scrape("https://example.com", nil)
//
//	// AI Research
//	answer, err := client.AiAsk("What is Python?", nil)
package jiro

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

// Version is the SDK version.
const Version = "0.2.15"

// Client represents a Jiro API client.
type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

// ClientOption is a function that configures the client.
type ClientOption func(*Client)

// WithAPIKey sets the API key for authentication.
func WithAPIKey(apiKey string) ClientOption {
	return func(c *Client) {
		c.apiKey = apiKey
	}
}

// WithBaseURL sets the base URL of the Jiro server.
func WithBaseURL(baseURL string) ClientOption {
	return func(c *Client) {
		c.baseURL = baseURL
	}
}

// WithTimeout sets the HTTP client timeout.
func WithTimeout(timeout time.Duration) ClientOption {
	return func(c *Client) {
		c.httpClient.Timeout = timeout
	}
}

// NewClient creates a new Jiro client.
func NewClient(opts ...ClientOption) *Client {
	c := &Client{
		baseURL: "http://127.0.0.1:8000",
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
	for _, opt := range opts {
		opt(c)
	}
	return c
}

// ErrorResponse represents an API error response.
type ErrorResponse struct {
	Error  string `json:"error"`
	Detail string `json:"detail,omitempty"`
}

// Error implements the error interface.
func (e *ErrorResponse) Error() string {
	if e.Detail != "" {
		return fmt.Sprintf("%s: %s", e.Error, e.Detail)
	}
	return e.Error
}

// ── Search Types ──────────────────────────────────────────────────────────

// SearchOptions contains optional parameters for search.
type SearchOptions struct {
	Engine     string
	Num        int
	Type       string
	Location   string
	Language   string
	Parallel   bool
	NumEngines int
}

// SearchResult represents a single search result.
type SearchResult struct {
	Title       string `json:"title"`
	Snippet     string `json:"snippet"`
	Link        string `json:"link"`
	Source      string `json:"source"`
	DisplayLink string `json:"displayed_link,omitempty"`
	Position    int    `json:"position,omitempty"`
}

// SearchResponse represents the search API response.
type SearchResponse struct {
	SearchMetadata struct {
		Engine         string  `json:"engine"`
		Cached         bool    `json:"cached"`
		TotalTimeTaken float64 `json:"total_time_taken"`
	} `json:"search_metadata"`
	OrganicResults []SearchResult `json:"organic_results"`
}

// Search executes a web search.
func (c *Client) Search(query string, opts *SearchOptions) (*SearchResponse, error) {
	params := url.Values{
		"q": {query},
	}
	if opts != nil {
		if opts.Engine != "" {
			params.Set("engine", opts.Engine)
		}
		if opts.Num > 0 {
			params.Set("num", fmt.Sprintf("%d", opts.Num))
		}
		if opts.Type != "" {
			params.Set("type", opts.Type)
		}
		if opts.Location != "" {
			params.Set("location", opts.Location)
		}
		if opts.Language != "" {
			params.Set("language", opts.Language)
		}
		if opts.Parallel {
			params.Set("parallel", "true")
			if opts.NumEngines > 0 {
				params.Set("num_engines", fmt.Sprintf("%d", opts.NumEngines))
			}
		}
	}

	var resp SearchResponse
	if err := c.get("/search.json", params, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// SearchImages searches for images.
func (c *Client) SearchImages(query string, opts *SearchOptions) (*SearchResponse, error) {
	if opts == nil {
		opts = &SearchOptions{}
	}
	opts.Type = "images"
	return c.Search(query, opts)
}

// SearchNews searches for news.
func (c *Client) SearchNews(query string, opts *SearchOptions) (*SearchResponse, error) {
	if opts == nil {
		opts = &SearchOptions{}
	}
	opts.Type = "news"
	return c.Search(query, opts)
}

// SearchVideos searches for videos.
func (c *Client) SearchVideos(query string, opts *SearchOptions) (*SearchResponse, error) {
	if opts == nil {
		opts = &SearchOptions{}
	}
	opts.Type = "videos"
	return c.Search(query, opts)
}

// SearchParallel searches multiple engines in parallel.
func (c *Client) SearchParallel(query string, numResults, numEngines int) (*SearchResponse, error) {
	return c.Search(query, &SearchOptions{
		Parallel:   true,
		Num:        numResults,
		NumEngines: numEngines,
	})
}

// ── Scrape Types ─────────────────────────────────────────────────────────

// ScrapeOptions contains optional parameters for scraping.
type ScrapeOptions struct {
	Format string
}

// ScrapeResponse represents the scrape API response.
type ScrapeResponse struct {
	Title   string `json:"title"`
	URL     string `json:"url"`
	Content string `json:"content"`
	HTML    string `json:"html,omitempty"`
}

// Scrape scrapes a URL and extracts content.
func (c *Client) Scrape(rawURL string, opts *ScrapeOptions) (*ScrapeResponse, error) {
	body := map[string]string{
		"url": rawURL,
	}
	if opts != nil && opts.Format != "" {
		body["format"] = opts.Format
	}

	var resp ScrapeResponse
	if err := c.post("/scrape", body, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// ── AI Types ─────────────────────────────────────────────────────────────

// AiAskOptions contains optional parameters for AI queries.
type AiAskOptions struct {
	MaxSources int
}

// Citation represents a source citation.
type Citation struct {
	Title string `json:"title"`
	URL   string `json:"url"`
}

// AiAskResponse represents the AI search API response.
type AiAskResponse struct {
	Answer    string     `json:"answer"`
	Citations []Citation `json:"citations"`
}

// AiAsk asks an AI research question with citations.
func (c *Client) AiAsk(query string, opts *AiAskOptions) (*AiAskResponse, error) {
	body := map[string]interface{}{
		"query": query,
	}
	if opts != nil && opts.MaxSources > 0 {
		body["max_sources"] = opts.MaxSources
	}

	var resp AiAskResponse
	if err := c.post("/ai/search", body, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// AiConfig gets current AI configuration.
func (c *Client) AiConfig() (map[string]interface{}, error) {
	var resp map[string]interface{}
	if err := c.get("/ai/config", nil, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// AiSetup configures AI provider.
func (c *Client) AiSetup(provider, apiKey string, model, baseURL string) (map[string]interface{}, error) {
	body := map[string]interface{}{
		"provider": provider,
		"api_key":  apiKey,
	}
	if model != "" {
		body["model"] = model
	}
	if baseURL != "" {
		body["base_url"] = baseURL
	}

	var resp map[string]interface{}
	if err := c.post("/ai/config", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ── Social Types ─────────────────────────────────────────────────────────

// SocialScrape scrapes social media content.
func (c *Client) SocialScrape(rawURL string, platform string) (map[string]interface{}, error) {
	body := map[string]interface{}{
		"url": rawURL,
	}
	if platform != "" {
		body["platform"] = platform
	}

	var resp map[string]interface{}
	if err := c.post("/social/scrape", body, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// SocialSearch searches on a social platform.
func (c *Client) SocialSearch(query, platform string, numResults int) (map[string]interface{}, error) {
	params := url.Values{
		"q": {query},
	}
	if numResults > 0 {
		params.Set("num", fmt.Sprintf("%d", numResults))
	}

	var resp map[string]interface{}
	path := fmt.Sprintf("/social/%s/search", platform)
	if err := c.get(path, params, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ── Batch Types ──────────────────────────────────────────────────────────

// BatchJob represents a batch operation job.
type BatchJob struct {
	JobID          string        `json:"job_id"`
	Operation      string        `json:"operation"`
	Status         string        `json:"status"`
	TotalItems     int           `json:"total_items"`
	CompletedItems int           `json:"completed_items"`
	FailedItems    int           `json:"failed_items"`
	Items          []interface{} `json:"items,omitempty"`
}

// Progress returns the job progress as a percentage.
func (j *BatchJob) Progress() float64 {
	if j.TotalItems == 0 {
		return 0
	}
	return float64(j.CompletedItems) / float64(j.TotalItems) * 100
}

// BatchSearch executes multiple searches in batch.
func (c *Client) BatchSearch(queries []string, engine string, numResults int) (*BatchJob, error) {
	body := map[string]interface{}{
		"queries":     queries,
		"engine":      engine,
		"num_results": numResults,
	}

	var resp BatchJob
	if err := c.post("/batch/search", body, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// BatchScrape scrapes multiple URLs in batch.
func (c *Client) BatchScrape(urls []string, format string) (*BatchJob, error) {
	body := map[string]interface{}{
		"urls":  urls,
		"format": format,
	}

	var resp BatchJob
	if err := c.post("/batch/scrape", body, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// BatchJobStatus gets batch job status.
func (c *Client) BatchJobStatus(jobID string) (*BatchJob, error) {
	var resp BatchJob
	path := fmt.Sprintf("/batch/jobs/%s", jobID)
	if err := c.get(path, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// ── System Types ─────────────────────────────────────────────────────────

// StatusResponse represents the system status response.
type StatusResponse struct {
	Version    string   `json:"version"`
	Status     string   `json:"status"`
	Engines    []string `json:"engines"`
	AuthEnabled bool   `json:"auth_enabled"`
}

// Status gets the system status.
func (c *Client) Status() (*StatusResponse, error) {
	var resp StatusResponse
	if err := c.get("/status", nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// Usage gets usage statistics.
func (c *Client) Usage(days int) (map[string]interface{}, error) {
	params := url.Values{}
	if days > 0 {
		params.Set("days", fmt.Sprintf("%d", days))
	}

	var resp map[string]interface{}
	if err := c.get("/usage", params, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// Plugins lists installed plugins.
func (c *Client) Plugins() (map[string]interface{}, error) {
	var resp map[string]interface{}
	if err := c.get("/plugins", nil, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// Health performs a health check.
func (c *Client) Health() (map[string]interface{}, error) {
	var resp map[string]interface{}
	if err := c.get("/health", nil, &resp); err != nil {
		return nil, err
	}
	return resp, nil
}

// ── Internal HTTP Methods ────────────────────────────────────────────────

// get performs an HTTP GET request.
func (c *Client) get(path string, params url.Values, result interface{}) error {
	u := c.baseURL + path
	if params != nil && len(params) > 0 {
		u += "?" + params.Encode()
	}

	req, err := http.NewRequest("GET", u, nil)
	if err != nil {
		return err
	}
	return c.do(req, result)
}

// post performs an HTTP POST request.
func (c *Client) post(path string, body interface{}, result interface{}) error {
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}

	req, err := http.NewRequest("POST", c.baseURL+path, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	return c.do(req, result)
}

// do executes an HTTP request and decodes the response.
func (c *Client) do(req *http.Request, result interface{}) error {
	req.Header.Set("User-Agent", fmt.Sprintf("jiro-sdk-go/%s", Version))
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}
	req.Header.Set("Accept", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}

	if resp.StatusCode == 401 {
		return &ErrorResponse{Error: "authentication failed"}
	}
	if resp.StatusCode == 429 {
		return &ErrorResponse{Error: "rate limit exceeded"}
	}
	if resp.StatusCode == 404 {
		return &ErrorResponse{Error: "resource not found"}
	}
	if resp.StatusCode >= 500 {
		return &ErrorResponse{Error: fmt.Sprintf("server error: %d", resp.StatusCode)}
	}
	if resp.StatusCode >= 400 {
		var errResp ErrorResponse
		if json.Unmarshal(body, &errResp) == nil {
			return &errResp
		}
		return &ErrorResponse{Error: fmt.Sprintf("HTTP %d", resp.StatusCode)}
	}

	if result != nil {
		return json.Unmarshal(body, result)
	}
	return nil
}
