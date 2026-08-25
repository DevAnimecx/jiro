# Contributing to Jiro Search

Thank you for your interest in contributing to Jiro Search! This guide covers how to contribute engine plugins, report issues, and submit pull requests.

## Quick Start

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feat/my-engine`
3. **Make your changes**
4. **Run tests**: `pytest -m "not network"`
5. **Submit a PR**

## Engine Plugin Development

Jiro Search uses a plugin architecture for search engines. You can add new engines without modifying core code.

### Creating a New Engine Plugin

```bash
# Scaffold a new engine
jiro plugins create myengine --author "Your Name"
```

This creates `myengine.py` with a template. Edit it to implement your engine's parsing logic.

### Plugin Structure

```python
class MyEngine(BaseEngine):
    name = "myengine"
    types = ["web", "images"]  # Supported search types
    version = "1.0.0"
    author = "Your Name"
    description = "MyEngine search parser"
    homepage = "https://github.com/you/jiro-myengine"
    license = "MIT"
    min_jiro_version = "0.1.0"

    # JSON Schema for engine-specific config
    config_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string"},
            "region": {"type": "string", "enum": ["us", "eu", "asia"]}
        }
    }

    async def search(self, req: SearchRequest) -> SearchResponse:
        # 1. Build request params
        params = {"q": req.q, "num": str(min(req.num, 100))}

        # 2. Make HTTP request
        html, _ = await self.client.get(
            "https://myengine.com/search",
            engine=self.name,
            params=params,
        )

        # 3. Parse with selectolax
        tree = parse_html(html)
        blocks = self._first_many(tree, RESULT_BLOCKS)

        # 4. Extract results
        organic = []
        for block in blocks:
            result = self._parse_result(block, position)
            if result:
                organic.append(result)

        # 5. Return structured response
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q, "organic_results_count": len(organic)},
            organic_results=organic,
        )
```

### Self-Healing Selectors

Use multiple selector candidates for resilience:

```python
RESULT_BLOCKS = [
    "div.result",      # Primary
    "div.item",        # Fallback
    "article.post",    # Fallback 2
]

TITLE_SELECTORS = ["h3 a", "h2.title a", ".title"]
SNIPPET_SELECTORS = [".snippet", ".summary", "p.description"]
```

The `_first_many()` helper tries each selector until one returns results.

### Testing Your Plugin

```bash
# Test with a live query
jiro search web "test query" --engine myengine

# Validate config
jiro plugins validate myengine --config myengine-config.json
```

### Distributing Plugins

1. **Publish to PyPI**: `pip install jiro-myengine`
2. **GitHub Release**: Users can download and place in `~/.jiro/plugins/`
3. **Jiro Plugin Registry** (future): Submit to the community marketplace

## Code Style

- **Python**: 3.11+ with type hints
- **Formatter**: `ruff format` (line length 100)
- **Linter**: `ruff check`
- **Type Check**: `pyright` or `mypy`

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run checks
ruff check .
ruff format .
pytest -m "not network"
```

## Pull Request Guidelines

### Before Submitting

- [ ] Tests pass (`pytest -m "not network"`)
- [ ] Code formatted (`ruff format .`)
- [ ] No lint errors (`ruff check .`)
- [ ] Type hints added for new functions
- [ ] Docstrings for public APIs
- [ ] CHANGELOG.md updated (if applicable)

### PR Title Format

```
feat(engine): add support for Brave News search
fix(scraper): handle 429 rate limit on Google
docs: update plugin development guide
refactor(registry): add plugin discovery
```

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): subject

body (optional)

footer (optional)
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`

## Engine Plugin Checklist

When submitting a new engine plugin:

- [ ] Implements `BaseEngine` interface
- [ ] Supports at least `web` search type
- [ ] Uses self-healing selectors
- [ ] Handles pagination (`start`, `num`)
- [ ] Handles `time_range`, `safe`, `language`, `location` params
- [ ] Includes `parser_version` for cache invalidation
- [ ] Has `config_schema` for engine-specific settings
- [ ] Tests with `pytest -m "network"` (if network access available)
- [ ] Documents any special requirements (proxies, API keys, etc.)

## Reporting Issues

### Bug Reports

Include:
- Jiro version (`jiro --version`)
- Python version
- Engine(s) affected
- Minimal reproduction steps
- Expected vs actual behavior
- Error logs (with `--log-level debug`)

### Feature Requests

- Clear use case description
- Why this benefits users
- Implementation approach (if known)

### Security Issues

Report privately via GitHub Security Advisories or email security@jiro-search.dev

## Community

- **GitHub Discussions**: [DevAnimecx/jiro/discussions](https://github.com/DevAnimecx/jiro/discussions) — questions & ideas
- **Issues**: [DevAnimecx/jiro/issues](https://github.com/DevAnimecx/jiro/issues)
- **Security**: GitHub Security Advisories or `security@blackvault.dev`

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

**Maintainers**: See [MAINTAINERS.md](MAINTAINERS.md) for release process and governance.