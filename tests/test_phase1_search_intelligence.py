"""Tests for Phase 1: Search Intelligence (Hybrid search, relevance, filters, highlights, answer, multi-query)"""

from __future__ import annotations

import pytest

from jiro.config import Settings
from jiro.search.relevance import RelevanceScorer, RelevanceBreakdown, RelevanceScore
from jiro.search.filters import SearchFilter, FilterConfig, get_engines_for_category
from jiro.search.highlights import HighlightExtractor, extract_highlights_from_content
from jiro.search.answer import AnswerSynthesizer, AnswerResult
from jiro.search.multiquery import MultiQuerySearcher, MultiQueryRequest, generate_sub_queries


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    return Settings.load()


@pytest.fixture
def sample_results():
    return [
        {
            "title": "Python Web Scraping Tutorial",
            "link": "https://example.com/python-scraping",
            "displayed_link": "example.com",
            "snippet": "Learn how to scrape websites with Python using BeautifulSoup and Scrapy.",
            "date": "2026-01-15",
        },
        {
            "title": "Best Python Scraping Libraries 2026",
            "link": "https://github.com/awesome-scraping",
            "displayed_link": "github.com",
            "snippet": "Comparison of BeautifulSoup, Scrapy, Playwright, and Selenium for web scraping.",
            "date": "2026-02-20",
        },
        {
            "title": "Web Scraping with Python - Real Python",
            "link": "https://realpython.com/python-web-scraping/",
            "displayed_link": "realpython.com",
            "snippet": "Complete guide to web scraping with Python including async techniques.",
            "date": "2026-03-10",
        },
        {
            "title": "Scraping Amazon Product Data",
            "link": "https://amazon.com/scraping-guide",
            "displayed_link": "amazon.com",
            "snippet": "How to extract product prices, reviews, and ratings from Amazon.",
            "date": "2025-12-01",
        },
    ]


# ── Relevance Scoring Tests ──────────────────────────────────────────────────

class TestRelevanceScorer:
    def test_keyword_match_score(self, settings):
        scorer = RelevanceScorer(settings)
        query = "python web scraping"
        result = {
            "title": "Python Web Scraping Tutorial",
            "snippet": "Learn how to scrape websites with Python using BeautifulSoup",
            "link": "https://example.com/python-scraping",
        }
        score = scorer._keyword_match_score(query, result)
        assert 0.0 <= score <= 1.0
        assert score > 0.2  # Should have some keyword overlap

    def test_keyword_match_no_overlap(self, settings):
        scorer = RelevanceScorer(settings)
        query = "python web scraping"
        result = {
            "title": "JavaScript Framework Comparison",
            "snippet": "Compare React, Vue, and Angular for frontend development",
            "link": "https://example.com/js-frameworks",
        }
        score = scorer._keyword_match_score(query, result)
        assert score < 0.3  # Low overlap

    def test_source_authority_known_domains(self, settings):
        scorer = RelevanceScorer(settings)
        # High authority
        assert scorer._source_authority_score("https://github.com/user/repo") >= 0.9
        assert scorer._source_authority_score("https://wikipedia.org/wiki/Python") >= 0.9
        assert scorer._source_authority_score("https://stackoverflow.com/questions/123") >= 0.9
        # Medium authority
        assert scorer._source_authority_score("https://realpython.com/article") >= 0.5
        # Unknown domain
        assert scorer._source_authority_score("https://unknown-site.xyz/page") == 0.5

    def test_source_authority_bias_domains(self, settings):
        settings.raw["scraping"]["bias_domains"] = {"example.com": 1.5}
        scorer = RelevanceScorer(settings)
        assert scorer._source_authority_score("https://example.com/page") == 1.0  # capped at 1.0

    def test_freshness_score(self, settings):
        scorer = RelevanceScorer(settings)
        # Recent (within 1 day) - need a date very close to now
        import time
        recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        assert scorer._freshness_score({"date": recent}) >= 0.9
        # Few days ago (within week)
        week_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 3*86400))
        assert scorer._freshness_score({"date": week_ago}) >= 0.7
        # Month ago
        month_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30*86400))
        assert scorer._freshness_score({"date": month_ago}) >= 0.5
        # Year ago
        year_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 400*86400))
        assert scorer._freshness_score({"date": year_ago}) <= 0.3
        # No date
        assert scorer._freshness_score({}) == 0.5

    def test_full_score_with_breakdown(self, settings):
        scorer = RelevanceScorer(settings)
        query = "python web scraping"
        result = {
            "title": "Python Web Scraping Tutorial",
            "snippet": "Learn how to scrape websites with Python using BeautifulSoup",
            "link": "https://github.com/user/scraping-tutorial",
            "date": "2026-08-15",
        }
        score_obj = scorer.score(query, result, semantic_similarity=0.8)
        
        assert isinstance(score_obj, RelevanceScore)
        assert 0.0 <= score_obj.score <= 1.0
        assert isinstance(score_obj.breakdown, RelevanceBreakdown)
        assert 0.0 <= score_obj.breakdown.keyword_match <= 1.0
        assert 0.0 <= score_obj.breakdown.semantic_similarity <= 1.0
        assert 0.0 <= score_obj.breakdown.source_authority <= 1.0
        assert 0.0 <= score_obj.breakdown.freshness <= 1.0

    def test_score_batch(self, settings):
        scorer = RelevanceScorer(settings)
        query = "python web scraping"
        results = [
            {"title": "Python Scraping", "snippet": "Python scraping guide", "link": "https://github.com/a", "date": "2026-08-15"},
            {"title": "JavaScript Guide", "snippet": "JS tutorial", "link": "https://example.com/b", "date": "2026-08-15"},
        ]
        scores = scorer.score_batch(query, results, semantic_scores=[0.8, 0.3])
        
        assert len(scores) == 2
        assert scores[0].score > scores[1].score  # First should be more relevant


# ── Filter Tests ──────────────────────────────────────────────────────────────

class TestSearchFilter:
    def test_include_domains(self, settings):
        config = FilterConfig(include_domains=["github.com", "gitlab.com"])
        filter_obj = SearchFilter(config, settings)
        
        results = [
            {"link": "https://github.com/user/repo", "title": "GitHub"},
            {"link": "https://example.com/page", "title": "Example"},
        ]
        filtered = filter_obj.filter(results)
        
        assert len(filtered) == 1
        assert "github.com" in filtered[0]["link"]

    def test_exclude_domains(self, settings):
        config = FilterConfig(exclude_domains=["facebook.com", "twitter.com"])
        filter_obj = SearchFilter(config, settings)
        
        results = [
            {"link": "https://github.com/user/repo", "title": "GitHub"},
            {"link": "https://facebook.com/page", "title": "Facebook"},
        ]
        filtered = filter_obj.filter(results)
        
        assert len(filtered) == 1
        assert "github.com" in filtered[0]["link"]

    def test_bias_domains(self, settings):
        config = FilterConfig(bias_domains={"github.com": 1.5})
        filter_obj = SearchFilter(config, settings)
        
        results = [
            {"link": "https://github.com/user/repo", "title": "GitHub", "relevance": {"relevance_score": 0.6}},
            {"link": "https://example.com/page", "title": "Example", "relevance": {"relevance_score": 0.6}},
        ]
        filtered = filter_obj.filter(results)
        
        # GitHub should be boosted
        github_result = next(r for r in filtered if "github.com" in r["link"])
        assert github_result["relevance"]["relevance_score"] > 0.6

    def test_time_range_filter(self, settings):
        import time
        config = FilterConfig(time_range="week")
        filter_obj = SearchFilter(config, settings)
        
        recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - 3*86400))
        old = time.strftime("%Y-%m-%d", time.localtime(time.time() - 60*86400))
        
        results = [
            {"link": "https://example.com/new", "title": "New", "date": recent},
            {"link": "https://example.com/old", "title": "Old", "date": old},
        ]
        filtered = filter_obj.filter(results)
        
        assert len(filtered) == 1
        assert "new" in filtered[0]["title"].lower()

    def test_absolute_date_range(self, settings):
        config = FilterConfig(start_date="2026-08-01", end_date="2026-08-20")
        filter_obj = SearchFilter(config, settings)
        
        results = [
            {"link": "https://example.com/in-range", "title": "In Range", "date": "2026-08-15"},
            {"link": "https://example.com/before", "title": "Before", "date": "2026-07-15"},
            {"link": "https://example.com/after", "title": "After", "date": "2026-08-25"},
        ]
        filtered = filter_obj.filter(results)
        
        assert len(filtered) == 1
        assert "in-range" in filtered[0]["link"]

    def test_category_filter_publication(self, settings):
        config = FilterConfig(category="publication")
        filter_obj = SearchFilter(config, settings)
        
        results = [
            {"link": "https://arxiv.org/abs/1234", "title": "ArXiv Paper", "snippet": "New research paper on transformers"},
            {"link": "https://example.com/blog", "title": "Blog Post", "snippet": "My thoughts on AI"},
        ]
        filtered = filter_obj.filter(results)
        
        assert len(filtered) == 1
        assert "arxiv.org" in filtered[0]["link"]

    def test_category_filter_shopping(self, settings):
        config = FilterConfig(category="shopping")
        filter_obj = SearchFilter(config, settings)
        
        results = [
            {"link": "https://amazon.com/product/123", "title": "Product", "snippet": "Price $29.99 buy now"},
            {"link": "https://example.com/article", "title": "Article", "snippet": "How to select items for shopping"},
        ]
        filtered = filter_obj.filter(results)
        
        # Should find amazon.com by domain category, may also include example.com by keyword
        assert len(filtered) >= 1
        amazon_results = [r for r in filtered if "amazon.com" in r["link"]]
        assert len(amazon_results) == 1


# ── Highlights Tests ──────────────────────────────────────────────────────────

class TestHighlights:
    def test_extract_highlights(self, settings, sample_results):
        extractor = HighlightExtractor(settings)
        query = "python web scraping"
        
        results = extractor.extract(query, sample_results)
        
        for r in results:
            assert "highlights" in r
            assert isinstance(r["highlights"], list)
            if r["highlights"]:
                for h in r["highlights"]:
                    assert len(h) > 0
                    # Should contain query terms
                    assert any(term in h.lower() for term in ["python", "scraping", "web"])

    def test_extract_highlights_empty_query(self, settings, sample_results):
        extractor = HighlightExtractor(settings)
        query = ""
        
        results = extractor.extract(query, sample_results)
        
        for r in results:
            assert r["highlights"] == []

    def test_extract_highlights_max_chars(self, settings, sample_results):
        settings.raw["scraping"]["highlights"]["max_characters"] = 150
        extractor = HighlightExtractor(settings)
        query = "python web scraping"
        
        results = extractor.extract(query, sample_results)
        
        for r in results:
            total_chars = sum(len(h) for h in r["highlights"])
            assert total_chars <= 150

    def test_extract_highlights_from_content(self):
        query = "python web scraping tutorial"
        content = """
        Python is a great language for web scraping. 
        BeautifulSoup makes it easy to parse HTML.
        Scrapy is a powerful framework for large-scale scraping.
        Selenium can handle JavaScript-heavy sites.
        """
        
        highlights = extract_highlights_from_content(query, content, max_chars=250)
        
        assert len(highlights) > 0
        total = sum(len(h) for h in highlights)
        assert total <= 250


# ── Answer Synthesis Tests ────────────────────────────────────────────────────

class TestAnswerSynthesizer:
    def test_extractive_synthesis(self, settings, sample_results):
        synthesizer = AnswerSynthesizer(settings)
        query = "What are the best Python web scraping libraries?"
        
        result = synthesizer._synthesize_extractive(query, [
            {"title": r["title"], "url": r["link"], "snippet": r["snippet"], "content": r["snippet"]}
            for r in sample_results[:3]
        ])
        
        assert isinstance(result, AnswerResult)
        assert result.mode == "extractive"
        assert result.provider == "extractive-fallback"
        assert len(result.answer) > 0
        assert len(result.citations) > 0
        assert result.confidence > 0.0

    def test_extractive_synthesis_empty(self, settings):
        synthesizer = AnswerSynthesizer(settings)
        query = "What is the meaning of life?"
        
        result = synthesizer._synthesize_extractive(query, [])
        
        assert "No relevant sources" in result.answer or "Could not extract" in result.answer
        assert result.confidence <= 0.3  # Low confidence for empty sources


# ── Multi-Query Tests ─────────────────────────────────────────────────────────

class TestMultiQuery:
    def test_generate_sub_queries(self):
        query = "compare python vs javascript for web scraping"
        sub_queries = generate_sub_queries(query, max_queries=3)
        
        assert len(sub_queries) >= 1
        assert len(sub_queries) <= 3
        assert query in sub_queries

    def test_generate_sub_queries_best(self):
        query = "best python web scraping library"
        sub_queries = generate_sub_queries(query, max_queries=3)
        
        assert len(sub_queries) >= 1
        # Should add review/comparison variants

    def test_multi_query_request(self):
        req = MultiQueryRequest(
            queries=["python scraping", "best scraping library", "scrapy vs playwright"],
            merge=True,
            deduplicate=True,
            rerank=True,
            max_results=20,
            depth="basic",
            engine="auto"
        )
        
        assert len(req.queries) == 3
        assert req.merge is True
        assert req.deduplicate is True
        assert req.rerank is True


# ── Engine Category Tests ────────────────────────────────────────────────────

class TestCategoryEngines:
    def test_get_engines_for_category(self, settings):
        settings.raw["scraping"]["engines"] = ["google", "bing", "github", "arxiv"]
        
        engines = get_engines_for_category("publication", settings)
        assert "arxiv" in engines
        assert "google" in engines
        
        engines = get_engines_for_category("github", settings)
        assert "github" in engines
        
        engines = get_engines_for_category("shopping", settings)
        assert "google" in engines


# ── Integration Tests (require orchestrator) ──────────────────────────────────

@pytest.mark.asyncio
async def test_search_request_v02_params(settings):
    """Test that SearchRequest accepts all v0.2 parameters."""
    from jiro.models import SearchRequest
    
    req = SearchRequest(
        q="test query",
        mode="hybrid",
        depth="advanced",
        include_domains=["github.com"],
        exclude_domains=["facebook.com"],
        bias_domains={"github.com": 1.5},
        start_date="2026-01-01",
        end_date="2026-12-31",
        category="publication",
        highlights=True,
        include_answer="extractive",
        output_schema={"type": "object", "properties": {"name": {"type": "string"}}}
    )
    
    assert req.mode == "hybrid"
    assert req.depth == "advanced"
    assert req.include_domains == ["github.com"]
    assert req.exclude_domains == ["facebook.com"]
    assert req.bias_domains == {"github.com": 1.5}
    assert req.start_date == "2026-01-01"
    assert req.end_date == "2026-12-31"
    assert req.category == "publication"
    assert req.highlights is True
    assert req.include_answer == "extractive"
    assert req.output_schema is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])