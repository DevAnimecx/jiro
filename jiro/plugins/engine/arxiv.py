"""arXiv search engine plugin."""
from __future__ import annotations
import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from jiro.config import Settings
from jiro.plugins import BaseEnginePlugin
from jiro.scraping.client import ScrapingClient

class ArxivPlugin(BaseEnginePlugin):
    """arXiv search engine plugin using the official API."""
    name = 'arxiv'
    type = 'engine'
    version = '1.0'
    author = 'Jiro Team'
    description = 'arXiv preprint search - physics, math, CS, etc.'
    homepage = 'https://arxiv.org'
    min_jiro_version = '0.2.0'
    supported_types = ['web', 'scholar', 'preprint']
    rate_limit_rpm = 60
    requires_proxy = False
    BASE_URL = 'http://export.arxiv.org/api/query'

    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
        self.base_url = settings.get('plugins.engine.arxiv.base_url', self.BASE_URL)

    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search arXiv using the official API."""
        search_query = self._build_query(query, kwargs)
        params = {'search_query': search_query, 'start': kwargs.get('start', 0), 'max_results': kwargs.get('max_results', 25), 'sortBy': kwargs.get('sort_by', 'relevance'), 'sortOrder': kwargs.get('sort_order', 'descending')}
        headers = {'User-Agent': 'Jiro/0.2 (+https://github.com/DevAnimecx/jiro)'}
        try:
            resp = await self.client.get(self.base_url, params=params, extra_headers=headers, engine=self.name)
            return self._parse_atom_response(resp.text)
        except Exception as e:
            raise RuntimeError(f'arXiv search failed: {e}')

    async def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape an arXiv paper page."""
        arxiv_id = self._extract_arxiv_id(url)
        if not arxiv_id:
            raise ValueError('Invalid arXiv URL')
        params = {'id_list': arxiv_id, 'max_results': 1}
        resp = await self.client.get(self.base_url, params=params, engine=self.name)
        results = self._parse_atom_response(resp.text)
        if results:
            return results[0]
        return {'title': '', 'link': url, 'error': 'Paper not found'}

    def _build_query(self, query: str, kwargs: Dict[str, Any]) -> str:
        """Build arXiv search query with field prefixes."""
        if kwargs.get('title'):
            return f'''ti:"{kwargs['title']}"'''
        if kwargs.get('author'):
            return f'''au:"{kwargs['author']}"'''
        if kwargs.get('abstract'):
            return f'''abs:"{kwargs['abstract']}"'''
        if kwargs.get('category'):
            return f"cat:{kwargs['category']}"
        return f'all:"{query}"'

    def _parse_atom_response(self, xml_text: str) -> List[Dict[str, Any]]:
        """Parse arXiv Atom XML response."""
        results = []
        try:
            root = ET.fromstring(xml_text)
            ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
            for entry in root.findall('.//atom:entry', ns):
                try:
                    id_elem = entry.find('atom:id', {'atom': 'http://www.w3.org/2005/Atom'})
                    arxiv_url = id_elem.text if id_elem is not None else ''
                    arxiv_id = arxiv_url.split('/')[-1] if arxiv_url else ''
                    title_elem = entry.find('atom:title', {'atom': 'http://www.w3.org/2005/Atom'})
                    title = title_elem.text.strip() if title_elem is not None else ''
                    summary_elem = entry.find('atom:summary', {'atom': 'http://www.w3.org/2005/Atom'})
                    summary = summary_elem.text.strip() if summary_elem is not None else ''
                    authors = []
                    for author in entry.findall('atom:author', {'atom': 'http://www.w3.org/2005/Atom'}):
                        name_elem = author.find('atom:name', {'atom': 'http://www.w3.org/2005/Atom'})
                        if name_elem is not None:
                            authors.append(name_elem.text.strip())
                    published_elem = entry.find('atom:published', {'atom': 'http://www.w3.org/2005/Atom'})
                    published = published_elem.text if published_elem is not None else ''
                    updated_elem = entry.find('atom:updated', {'atom': 'http://www.w3.org/2005/Atom'})
                    updated = updated_elem.text if updated_elem is not None else ''
                    categories = []
                    for cat in entry.findall('atom:category', {'atom': 'http://www.w3.org/2005/Atom'}):
                        term = cat.get('term')
                        if term:
                            categories.append(term)
                    doi = ''
                    for link in entry.findall('atom:link', {'atom': 'http://www.w3.org/2005/Atom'}):
                        if link.get('title') == 'doi':
                            doi = link.get('href', '')
                    pdf_url = ''
                    for link in entry.findall('atom:link', {'atom': 'http://www.w3.org/2005/Atom'}):
                        if link.get('type') == 'application/pdf':
                            pdf_url = link.get('href', '')
                    results.append({'title': title, 'link': f'https://arxiv.org/abs/{arxiv_id}' if arxiv_id else '', 'snippet': summary[:300] + '...' if len(summary) > 300 else summary, 'authors': authors, 'published': published, 'updated': updated, 'categories': categories, 'doi': doi, 'pdf_url': pdf_url, 'arxiv_id': arxiv_id, 'source': 'arxiv'})
                except Exception:
                    continue
        except ET.ParseError:
            pass
        return results

    def _extract_arxiv_id(self, url: str) -> Optional[str]:
        """Extract arXiv ID from various URL formats."""
        patterns = ['arxiv\\.org/abs/([^/]+)', 'arxiv\\.org/pdf/([^/]+)', 'arxiv\\.org/abs/([^/]+)v\\d+', 'arxiv:([0-9]{4}\\.[0-9]{4,5})']
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
from jiro.plugins import engine_registry
engine_registry.register(ArxivPlugin)