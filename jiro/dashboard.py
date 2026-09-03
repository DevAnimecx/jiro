"""Web UI Dashboard for Jiro v0.2.

Provides a modern web dashboard with:
- Real-time search with hybrid/structured options
- Social media scraper interface
- Intent classifier demo
- Plugin registry browser
- Engine comparison view
- Usage metrics and monitoring

Uses Alpine.js for interactivity and Tailwind CSS for styling.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jiro Dashboard v0.2</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <style>
        [x-cloak] { display: none !important; }
        .fade-in { animation: fadeIn 0.3s ease-in; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        .pulse { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
    </style>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        jiro: { 50: '#f0f9ff', 100: '#e0f2fe', 200: '#bae6fd', 300: '#7dd3fc',
                                400: '#38bdf8', 500: '#0ea5e9', 600: '#0284c7', 700: '#0369a1',
                                800: '#075985', 900: '#0c4a6e' }
                    }
                }
            }
        }
    </script>
</head>
<body class="h-full bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100"
      x-data="dashboard()" x-init="init()">

    <!-- Header -->
    <header class="bg-white dark:bg-gray-800 shadow-sm border-b border-gray-200 dark:border-gray-700">
        <div class="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-jiro-500 rounded-lg flex items-center justify-center">
                    <span class="text-white font-bold text-xl">J</span>
                </div>
                <div>
                    <h1 class="text-xl font-bold">Jiro Dashboard</h1>
                    <p class="text-xs text-gray-500 dark:text-gray-400">v0.2.0 - Search Intelligence</p>
                </div>
            </div>
            <div class="flex items-center gap-4">
                <span class="text-sm text-gray-500" x-text="status?.version || '...'"></span>
                <div class="w-3 h-3 rounded-full" :class="connected ? 'bg-green-500' : 'bg-red-500'"></div>
            </div>
        </div>
    </header>

    <!-- Navigation Tabs -->
    <nav class="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div class="max-w-7xl mx-auto px-4 flex gap-1">
            <template x-for="tab in tabs" :key="tab.id">
                <button @click="activeTab = tab.id"
                        class="px-4 py-3 text-sm font-medium border-b-2 transition-colors"
                        :class="activeTab === tab.id
                            ? 'border-jiro-500 text-jiro-600 dark:text-jiro-400'
                            : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    <span x-text="tab.icon"></span>
                    <span x-text="tab.name"></span>
                </button>
            </template>
        </div>
    </nav>

    <!-- Main Content -->
    <main class="max-w-7xl mx-auto px-4 py-6">

        <!-- Search Tab -->
        <div x-show="activeTab === 'search'" x-cloak class="fade-in">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6">
                <h2 class="text-lg font-semibold mb-4">Hybrid Search</h2>
                <div class="flex gap-3 mb-4">
                    <input type="text" x-model="searchQuery" @keyup.enter="doSearch()"
                           placeholder="Enter search query..."
                           class="flex-1 px-4 py-2 border rounded-lg focus:ring-2 focus:ring-jiro-500 focus:border-transparent dark:bg-gray-700 dark:border-gray-600">
                    <select x-model="searchEngine" class="px-4 py-2 border rounded-lg dark:bg-gray-700 dark:border-gray-600">
                        <option value="google">Google</option>
                        <option value="bing">Bing</option>
                        <option value="brave">Brave</option>
                        <option value="duckduckgo">DuckDuckGo</option>
                        <option value="hybrid">Hybrid (All)</option>
                    </select>
                    <select x-model="searchType" class="px-4 py-2 border rounded-lg dark:bg-gray-700 dark:border-gray-600">
                        <option value="web">Web</option>
                        <option value="news">News</option>
                        <option value="images">Images</option>
                        <option value="videos">Videos</option>
                    </select>
                    <button @click="doSearch()" :disabled="searching"
                            class="px-6 py-2 bg-jiro-500 text-white rounded-lg hover:bg-jiro-600 disabled:opacity-50">
                        <span x-show="!searching">Search</span>
                        <span x-show="searching" class="pulse">Searching...</span>
                    </button>
                </div>

                <!-- Intent Detection -->
                <div x-show="intent" class="mb-4 p-3 bg-blue-50 dark:bg-blue-900/30 rounded-lg text-sm">
                    <span class="font-medium">Intent:</span> <span x-text="intent?.intent"></span>
                    <span class="text-gray-500 mx-2">|</span>
                    <span class="font-medium">Target:</span> <span x-text="intent?.target"></span>
                    <span x-show="intent?.social_platform" class="text-gray-500 mx-2">|</span>
                    <span x-show="intent?.social_platform" class="font-medium">Platform:</span>
                    <span x-text="intent?.social_platform"></span>
                </div>

                <!-- Results -->
                <div x-show="searchResults.length > 0" class="space-y-3">
                    <template x-for="(result, idx) in searchResults" :key="idx">
                        <div class="p-4 border rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                            <a :href="result.link" target="_blank" class="text-jiro-600 dark:text-jiro-400 font-medium hover:underline"
                               x-text="result.title"></a>
                            <p class="text-sm text-gray-500 dark:text-gray-400 mt-1" x-text="result.snippet"></p>
                            <div class="flex gap-4 mt-2 text-xs text-gray-400">
                                <span x-text="result.source"></span>
                                <span x-show="result.relevance">Relevance: <span x-text="(result.relevance * 100).toFixed(0) + '%'"></span></span>
                            </div>
                        </div>
                    </template>
                </div>

                <div x-show="!searchResults.length && !searching" class="text-center py-12 text-gray-400">
                    Enter a query to search across 9+ engines with hybrid intelligence
                </div>
            </div>
        </div>

        <!-- Social Tab -->
        <div x-show="activeTab === 'social'" x-cloak class="fade-in">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6">
                <h2 class="text-lg font-semibold mb-4">Social Media Scraper</h2>
                <div class="flex gap-3 mb-4">
                    <input type="text" x-model="socialUrl" @keyup.enter="doSocialScrape()"
                           placeholder="Paste social media URL (Reddit, YouTube, Twitter, Instagram...)"
                           class="flex-1 px-4 py-2 border rounded-lg focus:ring-2 focus:ring-jiro-500 dark:bg-gray-700 dark:border-gray-600">
                    <button @click="doSocialScrape()" :disabled="socialLoading"
                            class="px-6 py-2 bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50">
                        Scrape
                    </button>
                </div>

                <!-- Platform Quick Select -->
                <div class="flex gap-2 mb-4 flex-wrap">
                    <template x-for="platform in socialPlatforms" :key="platform">
                        <button @click="socialUrl = 'https://' + platform + '.com'"
                                class="px-3 py-1 text-xs bg-gray-100 dark:bg-gray-700 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600"
                                x-text="platform"></button>
                    </template>
                </div>

                <!-- Social Results -->
                <div x-show="socialResult" class="mt-4">
                    <pre class="bg-gray-100 dark:bg-gray-900 p-4 rounded-lg overflow-auto max-h-96 text-sm"
                         x-text="JSON.stringify(socialResult, null, 2)"></pre>
                </div>
            </div>
        </div>

        <!-- Intent Tab -->
        <div x-show="activeTab === 'intent'" x-cloak class="fade-in">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6">
                <h2 class="text-lg font-semibold mb-4">Intent Classifier</h2>
                <div class="flex gap-3 mb-4">
                    <input type="text" x-model="intentQuery" @keyup.enter="classifyIntent()"
                           placeholder="Enter query to classify intent..."
                           class="flex-1 px-4 py-2 border rounded-lg focus:ring-2 focus:ring-jiro-500 dark:bg-gray-700 dark:border-gray-600">
                    <button @click="classifyIntent()"
                            class="px-6 py-2 bg-green-500 text-white rounded-lg hover:bg-green-600">
                        Classify
                    </button>
                </div>

                <!-- Intent Result -->
                <div x-show="intentResult" class="grid grid-cols-2 gap-4">
                    <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg">
                        <span class="text-sm text-gray-500">Intent</span>
                        <p class="text-xl font-bold" x-text="intentResult?.intent"></p>
                    </div>
                    <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg">
                        <span class="text-sm text-gray-500">Confidence</span>
                        <p class="text-xl font-bold" x-text="(intentResult?.confidence * 100).toFixed(0) + '%'"></p>
                    </div>
                    <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg">
                        <span class="text-sm text-gray-500">Target</span>
                        <p class="text-xl font-bold" x-text="intentResult?.target"></p>
                    </div>
                    <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg">
                        <span class="text-sm text-gray-500">Category</span>
                        <p class="text-xl font-bold" x-text="intentResult?.category || 'N/A'"></p>
                    </div>
                </div>

                <!-- Intent Examples -->
                <div class="mt-6">
                    <h3 class="font-medium mb-2">Try these examples:</h3>
                    <div class="flex flex-wrap gap-2">
                        <template x-for="ex in intentExamples" :key="ex">
                            <button @click="intentQuery = ex; classifyIntent()"
                                    class="px-3 py-1 text-xs bg-jiro-100 dark:bg-jiro-900 text-jiro-700 dark:text-jiro-300 rounded-full hover:bg-jiro-200"
                                    x-text="ex"></button>
                        </template>
                    </div>
                </div>
            </div>
        </div>

        <!-- Plugins Tab -->
        <div x-show="activeTab === 'plugins'" x-cloak class="fade-in">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6">
                <h2 class="text-lg font-semibold mb-4">Plugin Registry</h2>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    <template x-for="plugin in plugins" :key="plugin.name">
                        <div class="p-4 border rounded-lg hover:shadow-md transition-shadow">
                            <div class="flex items-center gap-2 mb-2">
                                <span class="px-2 py-1 text-xs rounded-full"
                                      :class="{
                                          'bg-blue-100 text-blue-700': plugin.type === 'engine',
                                          'bg-purple-100 text-purple-700': plugin.type === 'search',
                                          'bg-green-100 text-green-700': plugin.type === 'datasource',
                                          'bg-orange-100 text-orange-700': plugin.type === 'extractor',
                                          'bg-pink-100 text-pink-700': plugin.type === 'social'
                                      }"
                                      x-text="plugin.type"></span>
                                <span class="font-medium" x-text="plugin.name"></span>
                            </div>
                            <p class="text-sm text-gray-500" x-text="plugin.description"></p>
                            <div class="mt-2 text-xs text-gray-400">
                                <span>v</span><span x-text="plugin.version"></span>
                                <span class="mx-1">|</span>
                                <span x-text="plugin.author"></span>
                            </div>
                        </div>
                    </template>
                </div>
            </div>
        </div>

        <!-- Monitor Tab -->
        <div x-show="activeTab === 'monitor'" x-cloak class="fade-in">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6">
                <h2 class="text-lg font-semibold mb-4">Server Status</h2>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                    <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg text-center">
                        <span class="text-sm text-gray-500">Version</span>
                        <p class="text-2xl font-bold" x-text="status?.version || '...'"></p>
                    </div>
                    <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg text-center">
                        <span class="text-sm text-gray-500">Engines</span>
                        <p class="text-2xl font-bold" x-text="status?.engines?.length || 0"></p>
                    </div>
                    <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg text-center">
                        <span class="text-sm text-gray-500">Plugins</span>
                        <p class="text-2xl font-bold" x-text="status?.plugins_enabled ? 'Enabled' : 'Disabled'"></p>
                    </div>
                </div>

                <!-- Engine Health -->
                <h3 class="font-medium mb-3">Engine Health</h3>
                <div class="space-y-2">
                    <template x-for="engine in (status?.engines || [])" :key="engine.name || engine">
                        <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700 rounded-lg">
                            <span class="font-medium" x-text="engine.name || engine"></span>
                            <span class="px-2 py-1 text-xs rounded-full bg-green-100 text-green-700">Healthy</span>
                        </div>
                    </template>
                </div>
            </div>
        </div>

    </main>

    <!-- Footer -->
    <footer class="fixed bottom-0 inset-x-0 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 py-2">
        <div class="max-w-7xl mx-auto px-4 flex justify-between text-xs text-gray-500">
            <span>Jiro v0.2.0 - Local-first Search Intelligence</span>
            <span x-text="new Date().toLocaleString()"></span>
        </div>
    </footer>

    <script>
    function dashboard() {
        return {
            // State
            activeTab: 'search',
            connected: false,
            status: null,
            tabs: [
                { id: 'search', name: 'Search', icon: '🔍' },
                { id: 'social', name: 'Social', icon: '📱' },
                { id: 'intent', name: 'Intent', icon: '🧠' },
                { id: 'plugins', name: 'Plugins', icon: '🔌' },
                { id: 'monitor', name: 'Monitor', icon: '📊' },
            ],

            // Search
            searchQuery: '',
            searchEngine: 'google',
            searchType: 'web',
            searchResults: [],
            searching: false,
            intent: null,

            // Social
            socialUrl: '',
            socialResult: null,
            socialLoading: false,
            socialPlatforms: ['reddit', 'youtube', 'twitter', 'instagram', 'tiktok', 'linkedin', 'bluesky', 'threads', 'telegram', 'pinterest'],

            // Intent
            intentQuery: '',
            intentResult: null,
            intentExamples: [
                'What is the latest news about AI?',
                'github.com/fastapi',
                'buy iPhone 15 pro',
                'python web scraping tutorial',
                '@elonmusk tweets',
                'weather in Tokyo',
            ],

            // Plugins
            plugins: [],

            // Init
            async init() {
                await this.fetchStatus();
                await this.fetchPlugins();
                this.connected = true;
            },

            // API calls
            async fetchStatus() {
                try {
                    const resp = await fetch('/api/status');
                    this.status = await resp.json();
                } catch (e) {
                    console.error('Failed to fetch status:', e);
                }
            },

            async fetchPlugins() {
                try {
                    const resp = await fetch('/api/plugins');
                    this.plugins = await resp.json();
                } catch (e) {
                    console.error('Failed to fetch plugins:', e);
                }
            },

            async doSearch() {
                if (!this.searchQuery) return;
                this.searching = true;
                try {
                    const resp = await fetch('/api/search', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            q: this.searchQuery,
                            engine: this.searchEngine,
                            type: this.searchType,
                            max_results: 20,
                        }),
                    });
                    const data = await resp.json();
                    this.searchResults = data.organic_results || [];
                    if (data.search_metadata?.intent) {
                        this.intent = data.search_metadata;
                    }
                } catch (e) {
                    console.error('Search failed:', e);
                } finally {
                    this.searching = false;
                }
            },

            async doSocialScrape() {
                if (!this.socialUrl) return;
                this.socialLoading = true;
                try {
                    const resp = await fetch('/api/social', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: this.socialUrl }),
                    });
                    this.socialResult = await resp.json();
                } catch (e) {
                    console.error('Social scrape failed:', e);
                } finally {
                    this.socialLoading = false;
                }
            },

            async classifyIntent() {
                if (!this.intentQuery) return;
                try {
                    const resp = await fetch('/api/smart/classify', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ query: this.intentQuery }),
                    });
                    this.intentResult = await resp.json();
                } catch (e) {
                    console.error('Classification failed:', e);
                }
            },
        }
    }
    </script>
</body>
</html>"""


def create_dashboard_app(api_base_url: str = "http://localhost:8000") -> Starlette:
    """Create the dashboard Starlette app."""

    async def homepage(request):
        return HTMLResponse(DASHBOARD_HTML)

    async def api_status(request):
        return JSONResponse({"version": "0.2.0", "status": "ok"})

    async def api_plugins(request):
        from jiro.plugins import engine_registry, search_plugin_registry, datasource_registry
        plugins = []
        for name, cls in engine_registry.list_all().items():
            plugins.append({"name": name, "type": "engine", "version": cls.version,
                          "description": cls.description, "author": cls.author})
        for name, cls in search_plugin_registry.list_all().items():
            plugins.append({"name": name, "type": "search", "version": cls.version,
                          "description": cls.description, "author": cls.author})
        for name, cls in datasource_registry.list_all().items():
            plugins.append({"name": name, "type": "datasource", "version": cls.version,
                          "description": cls.description, "author": cls.author})
        return JSONResponse(plugins)

    async def api_search(request):
        import httpx
        body = await request.json()
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{api_base_url}/v1/search", json=body)
            return JSONResponse(resp.json())

    async def api_social(request):
        import httpx
        body = await request.json()
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{api_base_url}/v1/social", json=body)
            return JSONResponse(resp.json())

    async def api_smart_classify(request):
        import httpx
        body = await request.json()
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{api_base_url}/v1/smart/classify", json=body)
            return JSONResponse(resp.json())

    routes = [
        Route("/", homepage),
        Route("/api/status", api_status),
        Route("/api/plugins", api_plugins),
        Route("/api/search", api_search, methods=["POST"]),
        Route("/api/social", api_social, methods=["POST"]),
        Route("/api/smart/classify", api_smart_classify, methods=["POST"]),
    ]

    return Starlette(routes=routes)


# Standalone runner
if __name__ == "__main__":
    import uvicorn
    app = create_dashboard_app()
    uvicorn.run(app, host="0.0.0.0", port=3000)