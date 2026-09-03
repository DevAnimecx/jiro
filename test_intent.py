from jiro.search.intent import IntentType, classify_intent, IntentClassifier
from jiro.config import Settings

s = Settings.load()

test_queries = [
    'https://example.com',
    '@elonmusk latest tweets',
    'reddit r/Python top posts',
    'What is the best Python web framework?',
    'latest AI news today',
    'trending on twitter',
    'monitor competitor pricing',
    'youtube.com/watch?v=abc123',
    'tiktok.com/@user/video/123',
    'instagram.com/p/ABC123/',
    'linkedin.com/in/username',
    'facebook.com/user/posts/123',
    't.me/channel/123',
    'threads.net/@user/post/abc',
    'bsky.app/profile/user/post/xyz',
    'pinterest.com/pin/12345',
    'news.ycombinator.com/item?id=123',
    'compare iPhone 15 vs Samsung S24',
    'how to scrape websites with Python',
]

print('Testing intent classifier:')
for query in test_queries:
    result = classify_intent(query, s)
    print(f'  "{query[:50]}" -> {result.intent.value} (conf: {result.confidence:.2f}, platform: {result.platform}, action: {result.action})')