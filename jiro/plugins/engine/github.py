"""GitHub search engine plugin."""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from jiro.config import Settings
from jiro.plugins import BaseEnginePlugin
from jiro.scraping.client import ScrapingClient

class GitHubPlugin(BaseEnginePlugin):
    """GitHub code and repository search engine plugin."""
    name = 'github'
    type = 'engine'
    version = '1.0'
    author = 'Jiro Team'
    description = 'GitHub code search - repositories, code, issues, users'
    homepage = 'https://github.com'
    min_jiro_version = '0.2.0'
    supported_types = ['web', 'code', 'repository', 'issues', 'users']
    rate_limit_rpm = 30
    requires_proxy = False
    BASE_URL = 'https://api.github.com/search'

    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
        self.token = settings.get('plugins.engine.github.token', '')
        self.base_url = settings.get('plugins.engine.github.base_url', self.BASE_URL)

    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search GitHub using the official API."""
        search_type = kwargs.get('type', 'code')
        search_query = self._build_query(query, kwargs)
        params = {'q': search_query, 'per_page': min(kwargs.get('max_results', 25), 100), 'page': kwargs.get('start', 0) // 100 + 1, 'sort': kwargs.get('sort', 'best-match'), 'order': kwargs.get('order', 'desc')}
        headers = {'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Jiro/0.2'}
        if self.token:
            headers['Authorization'] = f'token {self.token}'
        url = f'{self.base_url}/{search_type}'
        try:
            resp = await self.client.get(url, params=params, extra_headers=headers, engine=self.name)
            data = resp.json()
            return self._parse_results(data, search_type)
        except Exception as e:
            raise RuntimeError(f'GitHub search failed: {e}')

    async def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape a GitHub page (repo, issue, PR, file)."""
        match = re.search('github\\.com/([^/]+)/([^/]+)', url)
        if not match:
            raise ValueError('Invalid GitHub URL')
        owner, repo = match.groups()
        repo = repo.replace('.git', '')
        headers = {'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'Jiro/0.2'}
        if self.token:
            headers['Authorization'] = f'token {self.token}'
        repo_url = f'https://api.github.com/repos/{owner}/{repo}'
        resp = await self.client.get(repo_url, extra_headers=headers, engine=self.name)
        repo_data = resp.json()
        return self._parse_repo(repo_data, url)

    def _build_query(self, query: str, kwargs: Dict[str, Any]) -> str:
        """Build GitHub search query with qualifiers."""
        parts = [query]
        if kwargs.get('language'):
            parts.append(f"language:{kwargs['language']}")
        if kwargs.get('repo'):
            parts.append(f"repo:{kwargs['repo']}")
        if kwargs.get('user'):
            parts.append(f"user:{kwargs['user']}")
        if kwargs.get('org'):
            parts.append(f"org:{kwargs['org']}")
        if kwargs.get('path'):
            parts.append(f"path:{kwargs['path']}")
        if kwargs.get('extension'):
            parts.append(f"extension:{kwargs['extension']}")
        if kwargs.get('min_stars'):
            parts.append(f"stars:>={kwargs['min_stars']}")
        if kwargs.get('min_forks'):
            parts.append(f"forks:>={kwargs['min_forks']}")
        if kwargs.get('created_after'):
            parts.append(f"created:>{kwargs['created_after']}")
        if kwargs.get('pushed_after'):
            parts.append(f"pushed:>{kwargs['pushed_after']}")
        if kwargs.get('state'):
            parts.append(f"state:{kwargs['state']}")
        if kwargs.get('label'):
            parts.append(f"label:{kwargs['label']}")
        return ' '.join(parts)

    def _parse_results(self, data: Dict[str, Any], search_type: str) -> List[Dict[str, Any]]:
        """Parse GitHub API response."""
        items = data.get('items', [])
        results = []
        for item in items:
            if search_type == 'repositories':
                results.append(self._parse_repo(item, item.get('html_url', '')))
            elif search_type == 'code':
                results.append(self._parse_code(item))
            elif search_type == 'issues':
                results.append(self._parse_issue(item))
            elif search_type == 'users':
                results.append(self._parse_user(item))
            else:
                results.append(item)
        return results

    def _parse_repo(self, repo: Dict[str, Any], url: str) -> Dict[str, Any]:
        return {'title': repo.get('full_name', ''), 'link': repo.get('html_url', url), 'snippet': repo.get('description', '')[:300] if repo.get('description') else '', 'stars': repo.get('stargazers_count', 0), 'forks': repo.get('forks_count', 0), 'language': repo.get('language', ''), 'topics': repo.get('topics', []), 'license': repo.get('license', {}).get('name', '') if repo.get('license') else '', 'updated': repo.get('updated_at', ''), 'source': 'github', 'type': 'repository'}

    def _parse_code(self, item: Dict[str, Any]) -> Dict[str, Any]:
        repo = item.get('repository', {})
        return {'title': f"{repo.get('full_name', '')} - {item.get('name', '')}", 'link': item.get('html_url', ''), 'snippet': item.get('text_matches', [{}])[0].get('fragment', '')[:300], 'repository': repo.get('full_name', ''), 'path': item.get('path', ''), 'language': self._guess_language(item.get('path', '')), 'source': 'github', 'type': 'code'}

    def _parse_issue(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {'title': item.get('title', ''), 'link': item.get('html_url', ''), 'snippet': item.get('body', '')[:300] if item.get('body') else '', 'state': item.get('state', ''), 'labels': [l.get('name', '') for l in item.get('labels', [])], 'assignees': [a.get('login', '') for a in item.get('assignees', [])], 'created': item.get('created_at', ''), 'updated': item.get('updated_at', ''), 'source': 'github', 'type': 'issue'}

    def _parse_user(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {'title': item.get('login', ''), 'link': item.get('html_url', ''), 'snippet': item.get('bio', '')[:300] if item.get('bio') else '', 'type': 'user' if item.get('type') == 'User' else 'organization', 'followers': item.get('followers', 0), 'public_repos': item.get('public_repos', 0), 'source': 'github'}

    def _guess_language(self, path: str) -> str:
        ext_map = {'.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript', '.java': 'Java', '.cpp': 'C++', '.c': 'C', '.go': 'Go', '.rs': 'Rust', '.rb': 'Ruby', '.php': 'PHP', '.cs': 'C#', '.html': 'HTML', '.css': 'CSS', '.json': 'JSON', '.md': 'Markdown', '.yml': 'YAML', '.yaml': 'YAML', '.xml': 'XML', '.sh': 'Shell'}
        for ext, lang in ext_map.items():
            if path.endswith(ext):
                return lang
        return ''
from jiro.plugins import engine_registry
engine_registry.register(GitHubPlugin)