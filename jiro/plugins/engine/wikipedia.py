"""Wikipedia search engine plugin."""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from jiro.config import Settings
from jiro.plugins import BaseEnginePlugin
from jiro.scraping.client import ScrapingClient

class WikipediaPlugin(BaseEnginePlugin):
    """Wikipedia search engine plugin."""
    name = 'wikipedia'
    type = 'engine'
    version = '1.0'
    author = 'Jiro Team'
    description = 'Wikipedia encyclopedia search'
    homepage = 'https://wikipedia.org'
    min_jiro_version = '0.2.0'
    supported_types = ['web', 'encyclopedia', 'reference']
    rate_limit_rpm = 100
    requires_proxy = False
    BASE_URL = 'https://en.wikipedia.org/w/api.php'

    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
        self.lang = settings.get('plugins.engine.wikipedia.lang', 'en')
        self.base_url = f'https://{self.lang}.wikipedia.org/w/api.php'

    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search Wikipedia using the MediaWiki API."""
        params = {'action': 'query', 'list': 'search', 'srsearch': query, 'srlimit': min(kwargs.get('max_results', 25), 50), 'srprop': 'snippet|title|timestamp|wordcount|size', 'format': 'json', 'srinfo': 'totalhits'}
        if kwargs.get('namespace'):
            params['srnamespace'] = kwargs['namespace']
        try:
            resp = await self.client.get(self.base_url, params=params, engine=self.name)
            data = resp.json()
            return self._parse_results(data.get('query', {}).get('search', []))
        except Exception as e:
            raise RuntimeError(f'Wikipedia search failed: {e}')

    async def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape a Wikipedia page."""
        title = self._extract_title(url)
        if not title:
            raise ValueError('Invalid Wikipedia URL')
        params = {'action': 'query', 'titles': title, 'prop': 'extracts|info|pageimages|categories|links|references', 'exintro': True, 'explaintext': True, 'inprop': 'url|displaytitle', 'format': 'json'}
        try:
            resp = await self.client.get(self.base_url, params=params, engine=self.name)
            data = resp.json()
            pages = data.get('query', {}).get('pages', {})
            for page in pages.values():
                if 'missing' not in page:
                    return self._parse_page(page)
            return {'title': '', 'link': url, 'error': 'Page not found'}
        except Exception as e:
            raise RuntimeError(f'Wikipedia scrape failed: {e}')

    def _parse_results(self, search_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse search results."""
        results = []
        for item in search_results:
            results.append({'title': item.get('title', ''), 'link': f"https://{self.lang}.wikipedia.org/wiki/{item.get('title', '').replace(' ', '_')}", 'snippet': re.sub('<[^>]+>', '', item.get('snippet', '')), 'word_count': item.get('wordcount', 0), 'size': item.get('size', 0), 'timestamp': item.get('timestamp', ''), 'source': 'wikipedia'})
        return results

    def _parse_page(self, page: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a full Wikipedia page."""
        return {'title': page.get('title', ''), 'link': page.get('fullurl', ''), 'snippet': page.get('extract', '')[:500], 'content': page.get('extract', ''), 'categories': [c.get('title', '') for c in page.get('categories', [])], 'links': [l.get('title', '') for l in page.get('links', [])[:50]], 'references': page.get('references', [])[:20], 'image': page.get('pageimage', ''), 'source': 'wikipedia'}

    def _extract_title(self, url: str) -> Optional[str]:
        """Extract page title from Wikipedia URL."""
        patterns = ['wikipedia\\.org/wiki/([^#?]+)', 'wikipedia\\.org/wiki/([^#?]+)#']
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1).replace('_', ' ')
        return None
from jiro.plugins import engine_registry
engine_registry.register(WikipediaPlugin)