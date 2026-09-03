package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"

	"github.com/spf13/cobra"
)

var version = "0.2.1"
var baseURL = "http://localhost:8000"

func main() {
	var rootCmd = &cobra.Command{
		Use:   "jiro",
		Short: "Jiro Search CLI - Local-first, AI-native web search & scraping",
		Long: `A Go CLI wrapper for Jiro Search, a drop-in, self-hosted SerpAPI alternative
with MCP server, agentic research, and built-in legal compliance.`,
	}

	// Serve command
	var serveCmd = &cobra.Command{
		Use:   "serve",
		Short: "Start the Jiro API server",
		Run: func(cmd *cobra.Command, args []string) {
			host, _ := cmd.Flags().GetString("host")
			port, _ := cmd.Flags().GetString("port")

			fmt.Println("Starting Jiro Search API...")
			fmt.Printf("Host: %s\n", host)
			fmt.Printf("Port: %s\n", port)
			fmt.Println("")
			fmt.Printf("API: http://localhost:%s\n", port)
			fmt.Printf("Docs: http://localhost:%s/docs\n", port)

			python := getPythonCmd()
			c := exec.Command(python, "-m", "uvicorn", "jiro.server:create_app",
				"--host", host, "--port", port)
			c.Stdin = os.Stdin
			c.Stdout = os.Stdout
			c.Stderr = os.Stderr
			c.Run()
		},
	}
	serveCmd.Flags().StringP("host", "h", "127.0.0.1", "Host to bind to")
	serveCmd.Flags().StringP("port", "p", "8000", "Port to listen on")
	rootCmd.AddCommand(serveCmd)

	// Search command
	var searchCmd = &cobra.Command{
		Use:   "search [query]",
		Short: "Search the web",
		Args:  cobra.ExactArgs(1),
		Run: func(cmd *cobra.Command, args []string) {
			query := args[0]
			engine, _ := cmd.Flags().GetString("engine")
			searchType, _ := cmd.Flags().GetString("type")
			num, _ := cmd.Flags().GetInt("num")

			url := fmt.Sprintf("%s/v1/search", baseURL)
			payload := map[string]interface{}{
				"q":    query,
				"engine": engine,
				"type":  searchType,
				"num":   num,
			}

			jsonData, _ := json.Marshal(payload)
			resp, err := http.Post(url, "application/json", bytes.NewBuffer(jsonData))
			if err != nil {
				fmt.Printf("Error: %v\n", err)
				return
			}
			defer resp.Body.Close()

			body, _ := io.ReadAll(resp.Body)
			var result map[string]interface{}
			json.Unmarshal(body, &result)

			// Pretty print
			prettyJSON, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(prettyJSON))
		},
	}
	searchCmd.Flags().StringP("engine", "e", "google", "Search engine")
	searchCmd.Flags().StringP("type", "t", "web", "Search type")
	searchCmd.Flags().IntP("num", "n", 10, "Number of results")
	rootCmd.AddCommand(searchCmd)

	// Scrape command
	var scrapeCmd = &cobra.Command{
		Use:   "scrape [url]",
		Short: "Scrape a URL",
		Args:  cobra.ExactArgs(1),
		Run: func(cmd *cobra.Command, args []string) {
			url := args[0]
			format, _ := cmd.Flags().GetString("format")

			apiURL := fmt.Sprintf("%s/v1/scrape", baseURL)
			payload := map[string]interface{}{
				"url":    url,
				"format": format,
			}

			jsonData, _ := json.Marshal(payload)
			resp, err := http.Post(apiURL, "application/json", bytes.NewBuffer(jsonData))
			if err != nil {
				fmt.Printf("Error: %v\n", err)
				return
			}
			defer resp.Body.Close()

			body, _ := io.ReadAll(resp.Body)
			var result map[string]interface{}
			json.Unmarshal(body, &result)

			prettyJSON, _ := json.MarshalIndent(result, "", "  ")
			fmt.Println(string(prettyJSON))
		},
	}
	scrapeCmd.Flags().StringP("format", "f", "markdown", "Output format (markdown/text/html/json)")
	rootCmd.AddCommand(scrapeCmd)

	// Status command
	var statusCmd = &cobra.Command{
		Use:   "status",
		Short: "Show server status",
		Run: func(cmd *cobra.Command, args []string) {
			url := fmt.Sprintf("%s/v1/monitor/health", baseURL)

			resp, err := http.Get(url)
			if err != nil {
				fmt.Println("Jiro is not running")
				fmt.Println("Start with: jiro serve")
				return
			}
			defer resp.Body.Close()

			body, _ := io.ReadAll(resp.Body)
			var result map[string]interface{}
			json.Unmarshal(body, &result)

			fmt.Println("Jiro is running")
			fmt.Printf("Version: %v\n", result["version"])
			fmt.Printf("Status: %v\n", result["status"])
		},
	}
	rootCmd.AddCommand(statusCmd)

	// Version command
	var versionCmd = &cobra.Command{
		Use:   "version",
		Short: "Show version",
		Run: func(cmd *cobra.Command, args []string) {
			fmt.Printf("jiro-cli v%s\n", version)
		},
	}
	rootCmd.AddCommand(versionCmd)

	rootCmd.Execute()
}

func getPythonCmd() string {
	if _, err := exec.LookPath("python"); err == nil {
		return "python"
	}
	return "python3"
}

func checkJiroInstalled() bool {
	python := getPythonCmd()
	cmd := exec.Command(python, "-c", "import jiro; print(jiro.__version__)")
	output, err := cmd.CombinedOutput()
	return err == nil && strings.Contains(string(output), "0.2.")
}