"""Google Scholar search engine plugin."""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from jiro.config import Settings
from jiro.plugins import BaseEnginePlugin
from jiro.scraping.client import ScrapingClient

class GoogleScholarPlugin(BaseEnginePlugin):
    """Google Scholar search engine plugin."""
    name = 'google_scholar'
    type = 'engine'
    version = '1.0'
    author = 'Jiro Team'
    description = 'Google Scholar academic search - papers, citations, authors'
    homepage = 'https://scholar.google.com'
    min_jiro_version = '0.2.0'
    supported_types = ['web', 'scholar', 'citations']
    rate_limit_rpm = 10
    requires_proxy = True
    BASE_URL = 'https://scholar.google.com'
    SEARCH_PATH = '/scholar'

    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
        self.base_url = settings.get('plugins.engine.google_scholar.base_url', self.BASE_URL)

    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search Google Scholar."""
        params = {'q': query, 'hl': kwargs.get('hl', 'en'), 'as_sdt': kwargs.get('as_sdt', '0,5'), 'start': kwargs.get('start', 0)}
        if kwargs.get('year_from'):
            params['as_ylo'] = kwargs['year_from']
        if kwargs.get('year_to'):
            params['as_yhi'] = kwargs['year_to']
        url = f'{self.base_url}{self.SEARCH_PATH}'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        try:
            resp = await self.client.get(url, params=params, extra_headers=headers, engine=self.name)
            html = resp.text
            return self._parse_results(html, query)
        except Exception as e:
            raise RuntimeError(f'Google Scholar search failed: {e}')

    async def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape a Google Scholar page (paper profile, author profile, etc.)."""
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = await self.client.get(url, extra_headers=headers, engine=self.name)
        return self._parse_paper_page(resp.text, url)

    def _parse_results(self, html: str, query: str) -> List[Dict[str, Any]]:
        """Parse Scholar search results."""
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        results = []
        for item in tree.css('.gs_r.gs_or.gs_scl'):
            try:
                title_elem = item.css_first('.gs_rt a')
                if not title_elem:
                    title_elem = item.css_first('.gs_rt')
                title = title_elem.text(strip=True) if title_elem else ''
                link = title_elem.attributes.get('href', '') if title_elem else ''
                authors_elem = item.css_first('.gs_a')
                authors_text = authors_elem.text(strip=True) if authors_elem else ''
                snippet_elem = item.css_first('.gs_rs')
                snippet = snippet_elem.text(strip=True) if snippet_elem else ''
                cited_elem = item.css_first('.gs_fl a')
                cited_count = 0
                if cited_elem and 'Cited by' in cited_elem.text():
                    cited_match = re.search('Cited by (\\d+)', cited_elem.text())
                    if cited_match:
                        cited_count = int(cited_match.group(1))
                year_match = re.search('\\b(19|20)\\d{2}\\b', authors_text)
                year = year_match.group(0) if year_match else ''
                if title:
                    results.append({'title': title, 'link': link, 'snippet': snippet, 'authors': authors_text, 'year': year, 'cited_by': cited_count, 'source': 'google_scholar'})
            except Exception:
                continue
        return results

    def _parse_paper_page(self, html: str, url: str) -> Dict[str, Any]:
        """Parse a paper detail page."""
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        title_elem = tree.css_first('#gsc_oci_title, .gsc_oci_title')
        title = title_elem.text(strip=True) if title_elem else ''
        authors_elem = tree.css_first('.gsc_oci_authors')
        authors = authors_elem.text(strip=True) if authors_elem else ''
        pub_elem = tree.css_first('.gsc_oci_journal')
        publication = pub_elem.text(strip=True) if pub_elem else ''
        abstract_elem = tree.css_first('.gsc_oci_abstract')
        abstract = abstract_elem.text(strip=True) if abstract_elem else ''
        citations_elem = tree.css_first('.gsc_oci_citations')
        citations = citations_elem.text(strip=True) if citations_elem else ''
        return {'title': title, 'link': url, 'authors': authors, 'publication': publication, 'abstract': abstract, 'citations': citations, 'source': 'google_scholar'}
from jiro.plugins import engine_registry
engine_registry.register(GoogleScholarPlugin)