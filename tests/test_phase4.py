"""Phase 4: Smart Extraction & Structured Data — tests for Schema.org extraction,
content classification, answer extraction, and image alt-text extraction.
"""

from __future__ import annotations



from jiro.structured import (
    StructuredResult,
    classify_content,
    extract_answers,
    extract_images_with_alt,
    extract_schema_org,
    extract_structured,
    parse_jsonld,
    parse_microdata,
    parse_rdfa,
)


# ── Fixtures ────────────────────────────────────────────────────────────

PRODUCT_HTML = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product",
 "name": "Wireless Mouse", "description": "Ergonomic wireless mouse with USB-C receiver",
 "brand": {"@type": "Brand", "name": "Logitech"},
 "image": "https://example.com/mouse.jpg",
 "offers": {"@type": "Offer", "price": "29.99", "priceCurrency": "USD",
            "availability": "https://schema.org/InStock"},
 "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.5", "reviewCount": "1234"}}
</script>
<meta property="og:type" content="product" />
<meta property="product:price:amount" content="29.99" />
</head><body>
<div class="product-detail">
<h1>Wireless Mouse</h1>
<p>Ergonomic wireless mouse with USB-C receiver</p>
<div class="price">$29.99</div>
<div class="rating">4.5 stars (1,234 reviews)</div>
</div>
</body></html>"""

ARTICLE_HTML = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "NewsArticle",
 "headline": "AI Advances in 2026",
 "author": {"@type": "Person", "name": "Jane Smith"},
 "publisher": {"@type": "Organization", "name": "Tech News"},
 "datePublished": "2026-01-15",
 "dateModified": "2026-01-16",
 "image": "https://example.com/ai-news.jpg",
 "description": "Latest advances in artificial intelligence technology"}
</script>
<meta property="article:published_time" content="2026-01-15" />
<meta property="article:author" content="Jane Smith" />
</head><body>
<article class="article-body">
<h1>AI Advances in 2026</h1>
<p class="byline">By Jane Smith</p>
<p>Artificial intelligence continues to advance rapidly in 2026...</p>
<p>New breakthroughs in language models have enabled...</p>
</article>
</body></html>"""

RECIPE_HTML = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Recipe",
 "name": "Classic Chocolate Chip Cookies",
 "recipeIngredient": ["2 cups flour", "1 cup butter", "1 cup chocolate chips"],
 "recipeInstructions": ["Preheat oven to 375F", "Mix dry ingredients", "Bake for 10 minutes"],
 "recipeYield": "24 cookies", "cookTime": "PT10M", "prepTime": "PT15M"}
</script>
</head><body>
<div class="recipe">
<h1>Classic Chocolate Chip Cookies</h1>
<div class="recipe-ingredients">
<h3>Ingredients</h3>
<ul><li>2 cups flour</li><li>1 cup butter</li><li>1 cup chocolate chips</li></ul>
</div>
<div class="recipe-instructions">
<h3>Instructions</h3>
<ol><li>Preheat oven to 375F</li><li>Mix dry ingredients</li><li>Bake for 10 minutes</li></ol>
</div>
</div>
</body></html>"""

FAQ_HTML = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "FAQPage",
 "mainEntity": [
   {"@type": "Question", "name": "What is Python?",
    "acceptedAnswer": {"@type": "Answer", "text": "Python is a programming language."}},
   {"@type": "Question", "name": "Is Python free?",
    "acceptedAnswer": {"@type": "Answer", "text": "Yes, Python is open source and free."}}
 ]}
</script>
</head><body>
<div class="faq">
<h1>Frequently Asked Questions</h1>
<details><summary>What is Python?</summary><p>Python is a programming language.</p></details>
<details><summary>Is Python free?</summary><p>Yes, Python is open source and free.</p></details>
</div>
</body></html>"""

VIDEO_HTML = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "VideoObject",
 "name": "Introduction to Machine Learning",
 "description": "A beginner's guide to ML",
 "duration": "PT15M30S",
 "uploadDate": "2026-01-10",
 "embedUrl": "https://youtube.com/embed/abc123",
 "thumbnailUrl": "https://img.youtube.com/vi/abc123/0.jpg"}
</script>
<meta property="og:video" content="https://youtube.com/embed/abc123" />
</head><body>
<div class="video-container">
<iframe src="https://youtube.com/embed/abc123" width="560" height="315"></iframe>
<h1>Introduction to Machine Learning</h1>
</div>
</body></html>"""

EVENT_HTML = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Event",
 "name": "Python Conference 2026",
 "startDate": "2026-06-15T09:00",
 "endDate": "2026-06-17T17:00",
 "location": {"@type": "Place", "name": "Convention Center, San Francisco"}}
</script>
</head><body>
<div class="event">
<h1>Python Conference 2026</h1>
<p>June 15-17, 2026 at Convention Center, San Francisco</p>
</div>
</body></html>"""

JOB_HTML = """<!DOCTYPE html><html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "JobPosting",
 "title": "Senior Python Developer",
 "hiringOrganization": {"@type": "Organization", "name": "TechCorp"},
 "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "San Francisco", "addressRegion": "CA"}},
 "datePosted": "2026-01-10",
 "estimatedSalary": {"@type": "MonetaryAmount", "value": "150000", "currency": "USD"}}
</script>
</head><body>
<div class="job-listing">
<h1>Senior Python Developer</h1>
<p>Company: TechCorp</p>
<p>Location: San Francisco, CA</p>
</div>
</body></html>"""

MICRODATA_HTML = """<!DOCTYPE html><html><body>
<div itemscope itemtype="https://schema.org/Product">
  <h1 itemprop="name">Wireless Keyboard</h1>
  <p itemprop="description">Bluetooth mechanical keyboard</p>
  <span itemprop="price" content="79.99">$79.99</span>
</div>
</body></html>"""

IMAGES_HTML = """<!DOCTYPE html><html><body>
<img src="/images/photo1.jpg" alt="Mountain landscape" title="Mountains" width="800" height="600">
<img src="/images/photo2.jpg" alt="" title="" width="100" height="100">
<img src="/images/pixel.gif" alt="tracker" width="1" height="1">
<img src="/images/product.jpg" alt="Product photo" width="400" height="300">
<figure><img src="/images/chart.jpg" alt="Sales chart"><figcaption>Q4 2025 Sales</figcaption></figure>
</body></html>"""


# ── JSON-LD Parsing ─────────────────────────────────────────────────────

class TestJsonLdParsing:
    def test_parse_jsonld_returns_blocks(self):
        blocks = parse_jsonld(PRODUCT_HTML)
        assert len(blocks) >= 1

    def test_parse_jsonld_extracts_product(self):
        blocks = parse_jsonld(PRODUCT_HTML)
        flat = []
        for b in blocks:
            if isinstance(b, dict):
                flat.append(b)
            elif isinstance(b, list):
                flat.extend(b)
        product_found = False
        for block in flat:
            if block.get("@type") == "Product" or (isinstance(block.get("@type"), list) and "Product" in block.get("@type")):
                product_found = True
                break
        assert product_found

    def test_parse_jsonld_empty_html(self):
        blocks = parse_jsonld("<html><body></body></html>")
        assert blocks == []

    def test_parse_jsonld_invalid_json(self):
        html = '<script type="application/ld+json">{invalid json}</script>'
        blocks = parse_jsonld(html)
        assert blocks == []


# ── Schema.org Extraction ───────────────────────────────────────────────

class TestSchemaOrgExtraction:
    def test_product_extraction(self):
        schema = extract_schema_org(PRODUCT_HTML)
        assert schema.type == "Product"
        assert schema.name == "Wireless Mouse"
        assert schema.price == "29.99"
        assert schema.price_currency == "USD"
        assert schema.brand == "Logitech"
        assert schema.rating == 4.5
        assert schema.review_count == 1234

    def test_article_extraction(self):
        schema = extract_schema_org(ARTICLE_HTML)
        assert schema.type == "NewsArticle"
        assert schema.name == "AI Advances in 2026"
        assert schema.author == "Jane Smith"
        assert schema.publisher == "Tech News"
        assert "2026-01-15" in schema.date_published

    def test_recipe_extraction(self):
        schema = extract_schema_org(RECIPE_HTML)
        assert schema.type == "Recipe"
        assert schema.name == "Classic Chocolate Chip Cookies"
        assert len(schema.recipe_ingredients) == 3
        assert "2 cups flour" in schema.recipe_ingredients
        assert len(schema.recipe_instructions) == 3
        assert schema.recipe_yield == "24 cookies"

    def test_faq_extraction(self):
        schema = extract_schema_org(FAQ_HTML)
        assert schema.type == "FAQPage"
        assert len(schema.faq_items) == 2
        assert schema.faq_items[0]["question"] == "What is Python?"
        assert "programming language" in schema.faq_items[0]["answer"]

    def test_video_extraction(self):
        schema = extract_schema_org(VIDEO_HTML)
        assert schema.type == "VideoObject"
        assert schema.name == "Introduction to Machine Learning"
        assert schema.duration == "PT15M30S"
        assert "youtube.com" in schema.embed_url

    def test_event_extraction(self):
        schema = extract_schema_org(EVENT_HTML)
        assert schema.type == "Event"
        assert schema.name == "Python Conference 2026"
        assert "2026-06-15" in schema.event_start
        assert "Convention Center" in schema.event_location

    def test_job_extraction(self):
        schema = extract_schema_org(JOB_HTML)
        assert schema.type == "JobPosting"
        assert schema.job_title == "Senior Python Developer"
        assert schema.job_company == "TechCorp"
        assert "San Francisco" in schema.job_location

    def test_empty_html(self):
        schema = extract_schema_org("<html><body></body></html>")
        assert schema.type == ""
        assert schema.name == ""


# ── Content Classification ──────────────────────────────────────────────

class TestContentClassification:
    def test_classifies_product(self):
        schema = extract_schema_org(PRODUCT_HTML)
        cls = classify_content(PRODUCT_HTML, schema)
        assert cls.primary_type == "product"
        assert cls.confidence > 0.3

    def test_classifies_article(self):
        schema = extract_schema_org(ARTICLE_HTML)
        cls = classify_content(ARTICLE_HTML, schema)
        assert cls.primary_type == "article"
        assert cls.confidence > 0.3

    def test_classifies_recipe(self):
        schema = extract_schema_org(RECIPE_HTML)
        cls = classify_content(RECIPE_HTML, schema)
        assert cls.primary_type == "recipe"
        assert cls.confidence > 0.3

    def test_classifies_faq(self):
        schema = extract_schema_org(FAQ_HTML)
        cls = classify_content(FAQ_HTML, schema)
        assert cls.primary_type == "faq"
        assert cls.confidence > 0.3

    def test_classifies_video(self):
        schema = extract_schema_org(VIDEO_HTML)
        cls = classify_content(VIDEO_HTML, schema)
        assert cls.primary_type == "video"
        assert cls.confidence > 0.3

    def test_classifies_event(self):
        schema = extract_schema_org(EVENT_HTML)
        cls = classify_content(EVENT_HTML, schema)
        assert cls.primary_type == "event"
        assert cls.confidence > 0.3

    def test_classifies_job(self):
        schema = extract_schema_org(JOB_HTML)
        cls = classify_content(JOB_HTML, schema)
        assert cls.primary_type == "job"
        assert cls.confidence > 0.3

    def test_unknown_content(self):
        schema = extract_schema_org("<html><body><p>Just text.</p></body></html>")
        cls = classify_content("<html><body><p>Just text.</p></body></html>", schema)
        assert cls.primary_type == "unknown"
        assert cls.confidence == 0.0


# ── Answer Extraction ───────────────────────────────────────────────────

class TestAnswerExtraction:
    def test_faq_answers_extracted(self):
        answers = extract_answers(FAQ_HTML)
        faq_answers = [a for a in answers if a.answer_type == "faq"]
        assert len(faq_answers) >= 2

    def test_answers_have_source_url(self):
        answers = extract_answers(FAQ_HTML, url="https://example.com/faq")
        for a in answers:
            assert a.source_url == "https://example.com/faq"

    def test_answers_limited(self):
        # Create HTML with many FAQ items
        items = "".join(
            f'<div><h3>Q{i}</h3><p>Answer {i}</p></div>'
            for i in range(30)
        )
        html = f"<html><body>{items}</body></html>"
        answers = extract_answers(html)
        assert len(answers) <= 20


# ── Image Alt-Text Extraction ───────────────────────────────────────────

class TestImageExtraction:
    def test_extracts_images_with_alt(self):
        images = extract_images_with_alt(IMAGES_HTML, url="https://example.com")
        urls = [img["url"] for img in images]
        assert any("photo1.jpg" in u for u in urls)

    def test_alt_text_extracted(self):
        images = extract_images_with_alt(IMAGES_HTML, url="https://example.com")
        photo1 = next(img for img in images if "photo1.jpg" in img["url"])
        assert photo1["alt"] == "Mountain landscape"

    def test_caption_extracted(self):
        images = extract_images_with_alt(IMAGES_HTML, url="https://example.com")
        chart = next((img for img in images if "chart.jpg" in img["url"]), None)
        assert chart is not None
        assert chart.get("context") == "Q4 2025 Sales"

    def test_tracker_pixel_excluded(self):
        images = extract_images_with_alt(IMAGES_HTML)
        urls = [img["url"] for img in images]
        assert not any("pixel.gif" in u for u in urls)

    def test_max_images_limit(self):
        images = extract_images_with_alt(IMAGES_HTML, max_images=2)
        assert len(images) <= 2


# ── Microdata Parsing ──────────────────────────────────────────────────

class TestMicrodata:
    def test_extracts_microdata(self):
        items = parse_microdata(MICRODATA_HTML)
        assert len(items) >= 1
        product = next((i for i in items if "Product" in i["type"]), None)
        assert product is not None
        assert product["properties"].get("name") == "Wireless Keyboard"

    def test_empty_html(self):
        items = parse_microdata("<html><body></body></html>")
        assert items == []


# ── RDFa Parsing ────────────────────────────────────────────────────────

class TestRdfa:
    def test_extracts_rdfa(self):
        html = """<html><body>
        <div typeof="schema:Product" vocab="https://schema.org/">
            <span property="schema:name">Test Product</span>
        </div>
        </body></html>"""
        items = parse_rdfa(html)
        assert len(items) >= 1
        assert items[0]["type"] == "schema:Product"
        assert items[0]["properties"].get("schema:name") == "Test Product"

    def test_empty_html(self):
        items = parse_rdfa("<html><body></body></html>")
        assert items == []


# ── Unified Extraction ──────────────────────────────────────────────────

class TestStructuredExtraction:
    def test_full_product_extraction(self):
        result = extract_structured(PRODUCT_HTML)
        assert isinstance(result, StructuredResult)
        assert result.schema_org.type == "Product"
        assert result.classification.primary_type == "product"
        assert len(result.images_with_alt) >= 0
        assert isinstance(result.microdata, list)
        assert isinstance(result.rdfa, list)

    def test_full_article_extraction(self):
        result = extract_structured(ARTICLE_HTML)
        assert result.schema_org.type == "NewsArticle"
        assert result.classification.primary_type == "article"

    def test_full_faq_extraction(self):
        result = extract_structured(FAQ_HTML)
        assert result.schema_org.type == "FAQPage"
        assert result.classification.primary_type == "faq"
        assert len(result.answers) >= 2

    def test_full_recipe_extraction(self):
        result = extract_structured(RECIPE_HTML)
        assert result.schema_org.type == "Recipe"
        assert result.classification.primary_type == "recipe"
        assert len(result.schema_org.recipe_ingredients) == 3
