"""ContentExtractor tests on synthetic HTML."""

from __future__ import annotations

from jiro.extract import ContentExtractor, node_to_markdown

PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>My Great Article</title>
  <meta property="og:title" content="My Great Article">
  <meta name="description" content="A description here">
  <script type="application/ld+json">{"@type":"Article","headline":"My Great Article"}</script>
</head>
<body>
  <nav><a href="/">Home</a> <a href="/about">About</a></nav>
  <article>
    <h1>My Great Article</h1>
    <p>This is the <strong>first</strong> paragraph of the article body.</p>
    <p>And this is the second paragraph with a <a href="https://external.example/x">link</a>.</p>
    <ul><li>item one</li><li>item two</li></ul>
    <img src="/images/hero.png" alt="hero image">
  </article>
  <footer>copyright</footer>
</body>
</html>
"""


def test_title_and_metadata():
    ex = ContentExtractor(PAGE, url="https://site.example/article")
    result = ex.extract()
    assert result.title == "My Great Article"
    assert result.metadata["description"] == "A description here"
    assert result.json_ld[0]["headline"] == "My Great Article"


def test_markdown_output():
    ex = ContentExtractor(PAGE, url="https://site.example/article")
    result = ex.extract()
    assert "# My Great Article" in result.markdown
    assert "**first**" in result.markdown
    assert "[link](https://external.example/x)" in result.markdown
    assert "- item one" in result.markdown
    assert "![hero image](https://site.example/images/hero.png)" in result.markdown


def test_links_and_images():
    ex = ContentExtractor(PAGE, url="https://site.example/article")
    result = ex.extract()
    urls = [l["url"] for l in result.links]
    assert "https://external.example/x" in urls
    assert result.images[0]["url"] == "https://site.example/images/hero.png"
