# Jiro Go SDK

Official Go SDK for [Jiro Search API](https://github.com/DevAnimecx/jiro).

## Installation

```bash
go get github.com/DevAnimecx/jiro/sdk/go
```

## Quick Start

```go
package main

import (
    "fmt"
    "github.com/DevAnimecx/jiro/sdk/go"
)

func main() {
    // Initialize client
    client := jiro.NewClient(
        jiro.WithAPIKey("your-api-key"),
        jiro.WithBaseURL("http://127.0.0.1:8000"),
    )

    // Search the web
    results, err := client.Search("python web scraping", nil)
    if err != nil {
        panic(err)
    }
    fmt.Println(results.OrganicResults)

    // Scrape a URL
    content, err := client.Scrape("https://example.com", nil)
    if err != nil {
        panic(err)
    }
    fmt.Println(content.Content)

    // AI-powered research
    answer, err := client.AiAsk("What is Python?", nil)
    if err != nil {
        panic(err)
    }
    fmt.Println(answer.Answer)
}
```

## Search Options

```go
// Basic search
results, _ := client.Search("query", nil)

// Search with options
results, _ := client.Search("query", &jiro.SearchOptions{
    Engine:   "bing",
    Num:      20,
    Type:     "images",
    Location: "uk",
    Language: "en",
})

// Parallel search
results, _ := client.SearchParallel("query", 10, 3)

// Search images
results, _ := client.SearchImages("cats", nil)

// Search news
results, _ := client.SearchNews("technology", nil)

// Search videos
results, _ := client.SearchVideos("python tutorial", nil)
```

## Scrape Options

```go
// Basic scrape
content, _ := client.Scrape("https://example.com", nil)

// Scrape with format
content, _ := client.Scrape("https://example.com", &jiro.ScrapeOptions{
    Format: "text",
})
```

## AI Research

```go
// Basic AI question
answer, _ := client.AiAsk("What is Python?", nil)
fmt.Println(answer.Answer)
for _, c := range answer.Citations {
    fmt.Printf("- %s: %s\n", c.Title, c.URL)
}

// AI with more sources
answer, _ := client.AiAsk("Compare React vs Vue", &jiro.AiAskOptions{
    MaxSources: 10,
})
```

## Batch Operations

```go
// Batch search
job, _ := client.BatchSearch(
    []string{"python", "javascript", "go"},
    "google",
    5,
)
fmt.Printf("Progress: %.1f%%\n", job.Progress())

// Batch scrape
job, _ := client.BatchScrape(
    []string{"https://example.com", "https://httpbin.org/html"},
    "markdown",
)

// Check job status
status, _ := client.BatchJobStatus(job.JobID)
```

## Social Media

```go
// Scrape social media
result, _ := client.SocialScrape("https://reddit.com/r/python", "reddit")

// Search social platforms
results, _ := client.SocialSearch("python", "reddit", 10)
```

## System

```go
// Get system status
status, _ := client.Status()
fmt.Printf("Version: %s\n", status.Version)

// Get usage stats
usage, _ := client.Usage(30)

// List plugins
plugins, _ := client.Plugins()

// Health check
health, _ := client.Health()
```

## Error Handling

```go
results, err := client.Search("test", nil)
if err != nil {
    if errResp, ok := err.(*jiro.ErrorResponse); ok {
        switch errResp.Error {
        case "authentication failed":
            fmt.Println("Invalid API key")
        case "rate limit exceeded":
            fmt.Println("Rate limit exceeded")
        default:
            fmt.Println("Error:", errResp.Error)
        }
    }
}
```

## License

MIT License
