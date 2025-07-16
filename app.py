import os
import asyncio
import requests
import json
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from groq import Groq
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import time
import re
from bs4 import BeautifulSoup
import requests
import urllib.parse

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# Check for required environment variables
required_env_vars = {
    "GROQ_API_KEY": os.getenv("GROQ_API_KEY"),
    "SLACK_BOT_TOKEN": os.getenv("SLACK_BOT_TOKEN")
}

missing_vars = [var for var, value in required_env_vars.items() if not value]
if missing_vars:
    print("❌ Missing required environment variables:")
    for var in missing_vars:
        print(f"   - {var}")
    print("\nPlease set these environment variables or create a .env file with:")
    for var in missing_vars:
        print(f"   {var}=your_{var.lower()}_here")
    exit(1)

# Initialize clients
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Rest of your code remains the same...
# User profiles storage (in production, use a database)
user_profiles = {}
user_onboarding_state = {}
recent_articles = {}  # Store recent articles per user for conversation context
conversation_history = {}  # Store recent conversation context per user
shown_articles = {}  # Track articles already shown to users to avoid repetition

# Real news sources configuration
NEWS_API_KEY = os.getenv("NEWS_API_KEY")  # Optional: get from newsapi.org for more sources

def fetch_hackernews_stories_varied(role, interests, limit=20):
    """Fetch HackerNews stories with multiple strategies for variety"""
    try:
        all_articles = []
        
        # Strategy 1: Top stories with random starting point
        print("  - Fetching top stories...")
        top_stories_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
        response = requests.get(top_stories_url, timeout=10)
        story_ids = response.json()
        
        # Multiple random starting points for variety
        for _ in range(2):  # Try 2 different starting points
            start_idx = random.randint(0, min(100, len(story_ids) - limit))
            batch_ids = story_ids[start_idx:start_idx + limit//2]
            articles = fetch_hn_stories_batch(batch_ids, role, interests)
            all_articles.extend(articles)
        
        # Strategy 2: New stories for recent content
        print("  - Fetching new stories...")
        new_stories_url = "https://hacker-news.firebaseio.com/v0/newstories.json"
        response = requests.get(new_stories_url, timeout=10)
        new_story_ids = response.json()
        
        # Get some new stories
        recent_batch = new_story_ids[:limit//2]
        new_articles = fetch_hn_stories_batch(recent_batch, role, interests)
        all_articles.extend(new_articles)
        
        # Strategy 3: Best stories for quality content
        print("  - Fetching best stories...")
        best_stories_url = "https://hacker-news.firebaseio.com/v0/beststories.json"
        response = requests.get(best_stories_url, timeout=10)
        best_story_ids = response.json()
        
        # Random selection from best stories
        best_batch = random.sample(best_story_ids[:50], min(limit//2, len(best_story_ids[:50])))
        best_articles = fetch_hn_stories_batch(best_batch, role, interests)
        all_articles.extend(best_articles)
        
        # Remove duplicates and return varied selection
        unique_articles = remove_duplicate_articles(all_articles)
        random.shuffle(unique_articles)
        
        return unique_articles[:limit]
        
    except Exception as e:
        print(f"Error fetching varied HackerNews: {e}")
        return []

def fetch_hn_stories_batch(story_ids, role, interests):
    """Helper function to fetch a batch of HackerNews stories"""
    articles = []
    for story_id in story_ids:
        try:
            story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            story_response = requests.get(story_url, timeout=5)
            story_data = story_response.json()
            
            if story_data and story_data.get('type') == 'story' and story_data.get('url'):
                title = story_data.get('title', 'No title')
                category = categorize_article(title)
                
                # Filter based on role and interests
                if is_article_relevant(title, role, interests):
                    article = {
                        "title": title,
                        "link": story_data.get('url', ''),
                        "summary": f"HackerNews discussion with {story_data.get('score', 0)} points and {story_data.get('descendants', 0)} comments",
                        "published": datetime.fromtimestamp(story_data.get('time', 0)).strftime('%Y-%m-%d'),
                        "source": "Hacker News",
                        "category": category,
                        "score": story_data.get('score', 0),
                        "hn_id": story_id  # For tracking duplicates
                    }
                    articles.append(article)
                    
        except Exception as e:
            print(f"Error fetching story {story_id}: {e}")
            continue
    
    return articles

def fetch_reddit_tech_posts(limit=15):
    """Fetch posts from tech-related subreddits"""
    try:
        subreddits = ['programming', 'technology', 'MachineLearning', 'startups', 'webdev']
        articles = []
        
        for subreddit in subreddits:
            try:
                url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=5"
                headers = {'User-Agent': 'PulseBot/1.0'}
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()
                
                for post in data['data']['children'][:3]:  # Top 3 from each subreddit
                    post_data = post['data']
                    if not post_data.get('is_self') and post_data.get('url'):
                        article = {
                            "title": post_data.get('title', 'No title'),
                            "link": post_data.get('url', ''),
                            "summary": post_data.get('selftext', '')[:200] + "..." if post_data.get('selftext') else f"Reddit discussion with {post_data.get('score', 0)} upvotes",
                            "published": datetime.fromtimestamp(post_data.get('created_utc', 0)).strftime('%Y-%m-%d'),
                            "source": f"r/{subreddit}",
                            "category": map_subreddit_to_category(subreddit)
                        }
                        articles.append(article)
                        
            except Exception as e:
                print(f"Error fetching from r/{subreddit}: {e}")
                continue
                
        return articles[:limit]
    except Exception as e:
        print(f"Error fetching Reddit: {e}")
        return []

def fetch_newsapi_articles(category="technology", limit=10):
    """Fetch articles from News API (requires API key)"""
    if not NEWS_API_KEY:
        return []
        
    try:
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            'apiKey': NEWS_API_KEY,
            'category': category,
            'language': 'en',
            'pageSize': limit
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        articles = []
        for article_data in data.get('articles', []):
            article = {
                "title": article_data.get('title', 'No title'),
                "link": article_data.get('url', ''),
                "summary": article_data.get('description', 'No description available'),
                "published": article_data.get('publishedAt', '').split('T')[0],
                "source": article_data.get('source', {}).get('name', 'Unknown'),
                "category": category
            }
            articles.append(article)
            
        return articles
    except Exception as e:
        print(f"Error fetching News API: {e}")
        return []

def categorize_article(title):
    """Enhanced categorization with comprehensive design keyword matching"""
    title_lower = title.lower()
    
    # Comprehensive design keywords (most specific first)
    design_keywords = [
        # Core design terms
        'design system', 'ui design', 'ux design', 'user experience', 'user interface',
        'product design', 'web design', 'mobile design', 'graphic design', 'visual design',
        'interaction design', 'interface design', 'experience design', 'service design',
        
        # Design tools and software
        'figma', 'sketch', 'adobe xd', 'adobe', 'photoshop', 'illustrator', 'xd',
        'framer', 'principle', 'invision', 'miro', 'figjam', 'canva', 'affinity',
        
        # Design processes and methodologies
        'prototype', 'wireframe', 'mockup', 'design thinking', 'design ops', 'design sprint',
        'user research', 'user testing', 'usability testing', 'a/b testing', 'persona',
        'user journey', 'journey map', 'information architecture', 'card sorting',
        
        # Visual design concepts
        'typography', 'color theory', 'branding', 'brand identity', 'logo design',
        'icon design', 'illustration', 'layout', 'grid system', 'white space',
        'contrast', 'hierarchy', 'composition', 'palette', 'font', 'typeface',
        
        # UX/UI specific terms
        'usability', 'accessibility', 'user flow', 'navigation', 'menu design',
        'button design', 'form design', 'modal', 'dropdown', 'sidebar', 'header',
        'footer', 'landing page', 'homepage', 'dashboard', 'onboarding',
        
        # Design systems and components
        'component library', 'design token', 'style guide', 'pattern library',
        'atomic design', 'design language', 'component design', 'design consistency',
        
        # Modern design trends
        'dark mode', 'light mode', 'mobile-first', 'responsive design', 'adaptive design',
        'progressive web app', 'pwa', 'micro-interaction', 'animation', 'transition',
        'glassmorphism', 'neumorphism', 'skeuomorphism', 'flat design', 'material design',
        'minimalism', 'maximalism', 'brutalism', 'gradient', 'shadow', 'blur',
        
        # Design roles and teams
        'designer', 'ux designer', 'ui designer', 'product designer', 'graphic designer',
        'visual designer', 'interaction designer', 'design team', 'design lead',
        'design manager', 'design director', 'creative director',
        
        # Design processes
        'design review', 'design critique', 'design feedback', 'design handoff',
        'design collaboration', 'design workflow', 'design process', 'design method',
        
        # Specialized design areas
        'motion design', 'animation design', 'game design', 'automotive design',
        'industrial design', 'fashion design', 'interior design', 'architecture'
    ]
    
    # Check for design keywords
    if any(keyword in title_lower for keyword in design_keywords):
        return 'design'
    
    # Check for design-related context with general terms
    design_context_terms = [
        ('ui', ['component', 'interface', 'web', 'app', 'mobile', 'frontend']),
        ('ux', ['user', 'experience', 'research', 'testing', 'flow']),
        ('design', ['system', 'pattern', 'guide', 'language', 'token', 'tool']),
        ('user', ['interface', 'experience', 'research', 'testing', 'flow', 'journey']),
        ('frontend', ['design', 'ui', 'component', 'interface', 'css', 'html']),
        ('css', ['design', 'layout', 'styling', 'animation', 'responsive']),
        ('component', ['design', 'library', 'system', 'ui', 'react', 'vue']),
        ('responsive', ['design', 'web', 'mobile', 'css', 'layout']),
        ('accessibility', ['design', 'ui', 'ux', 'web', 'inclusive']),
        ('branding', ['identity', 'logo', 'visual', 'brand', 'marketing']),
        ('animation', ['design', 'ui', 'ux', 'motion', 'web', 'css']),
        ('mobile', ['design', 'ui', 'ux', 'app', 'responsive', 'ios', 'android']),
        ('web', ['design', 'ui', 'ux', 'frontend', 'css', 'html', 'responsive'])
    ]
    
    for main_term, context_terms in design_context_terms:
        if main_term in title_lower:
            if any(context in title_lower for context in context_terms):
                return 'design'
    
    # AI/ML keywords
    ai_keywords = [
        'artificial intelligence', 'machine learning', 'deep learning', 'neural network',
        'chatgpt', 'gpt', 'llm', 'openai', 'anthropic', 'groq', 'transformer',
        'ai model', 'ml model', 'data science', 'algorithm'
    ]
    if any(keyword in title_lower for keyword in ai_keywords):
        return 'ai_ml'
    
    # Engineering keywords
    engineering_keywords = [
        'javascript', 'typescript', 'python', 'react', 'vue', 'angular', 'node.js',
        'programming', 'coding', 'developer', 'software development', 'api',
        'framework', 'library', 'github', 'git', 'database', 'backend', 'frontend',
        'full stack', 'devops', 'cloud computing', 'aws', 'docker', 'kubernetes'
    ]
    if any(keyword in title_lower for keyword in engineering_keywords):
        return 'engineering'
    
    # Product keywords
    product_keywords = [
        'product management', 'product manager', 'product strategy', 'roadmap',
        'feature launch', 'user research', 'analytics', 'metrics', 'a/b testing',
        'product development', 'agile', 'scrum'
    ]
    if any(keyword in title_lower for keyword in product_keywords):
        return 'product'
    
    # Business keywords
    business_keywords = [
        'startup', 'funding', 'venture capital', 'vc', 'investment', 'ipo',
        'acquisition', 'revenue', 'business model', 'saas', 'enterprise',
        'market analysis', 'growth hacking'
    ]
    if any(keyword in title_lower for keyword in business_keywords):
        return 'business'
    
    # Technology/General keywords that are still tech-related
    tech_general_keywords = [
        'technology', 'tech', 'innovation', 'digital', 'software', 'hardware',
        'computing', 'internet', 'web', 'mobile', 'app', 'platform',
        'cybersecurity', 'security', 'blockchain', 'cryptocurrency', 'crypto',
        'cloud', 'data', 'analytics', 'automation', 'robotics', 'iot',
        'silicon valley', 'tech company', 'microsoft', 'google', 'apple',
        'amazon', 'facebook', 'meta', 'netflix', 'uber', 'tesla', 'spacex',
        'openai', 'anthropic', 'groq', 'nvidia', 'intel', 'amd'
    ]
    if any(keyword in title_lower for keyword in tech_general_keywords):
        return 'tech_general'
    
    return 'general'

def map_subreddit_to_category(subreddit):
    """Map subreddit names to categories"""
    mapping = {
        'programming': 'engineering',
        'webdev': 'engineering', 
        'MachineLearning': 'ai_ml',
        'startups': 'business',
        'technology': 'general',
        'design': 'design',
        'userexperience': 'design',
        'web_design': 'design'
    }
    return mapping.get(subreddit, 'general')

def map_subreddit_to_category(subreddit):
    """Map subreddit names to categories"""
    mapping = {
        'programming': 'engineering',
        'webdev': 'engineering', 
        'MachineLearning': 'ai_ml',
        'startups': 'business',
        'technology': 'general'
    }
    return mapping.get(subreddit, 'general')

def fetch_guaranteed_tech_articles(user_profile, min_tech_articles=3):
    """Fetch guaranteed tech articles for companies where everyone works in tech"""
    print(f"🔧 Fetching guaranteed tech articles (minimum: {min_tech_articles})")
    
    tech_articles = []
    
    # Tech categories to prioritize
    tech_categories = ['engineering', 'ai_ml', 'product', 'business', 'tech_general']
    
    # Fetch from HackerNews with tech focus
    try:
        print("  - Fetching tech articles from HackerNews...")
        top_stories_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
        response = requests.get(top_stories_url, timeout=10)
        story_ids = response.json()
        
        # Get more stories to find tech ones
        for story_id in story_ids[:100]:  # Check first 100 stories
            try:
                story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                story_response = requests.get(story_url, timeout=5)
                story_data = story_response.json()
                
                if story_data and story_data.get('type') == 'story' and story_data.get('url'):
                    title = story_data.get('title', 'No title')
                    category = categorize_article(title)
                    
                    # Only include tech articles
                    if category in tech_categories:
                        article = {
                            "title": title,
                            "link": story_data.get('url', ''),
                            "summary": f"HackerNews discussion with {story_data.get('score', 0)} points and {story_data.get('descendants', 0)} comments",
                            "published": datetime.fromtimestamp(story_data.get('time', 0)).strftime('%Y-%m-%d'),
                            "source": "Hacker News",
                            "category": category,
                            "score": story_data.get('score', 0),
                            "is_guaranteed_tech": True
                        }
                        tech_articles.append(article)
                        
                        if len(tech_articles) >= min_tech_articles:
                            break
                            
            except Exception as e:
                print(f"Error fetching tech story {story_id}: {e}")
                continue
                
    except Exception as e:
        print(f"Error fetching tech HackerNews: {e}")
    
    # If we don't have enough tech articles, fetch from Reddit tech subreddits
    if len(tech_articles) < min_tech_articles:
        print("  - Fetching additional tech articles from Reddit...")
        try:
            tech_subreddits = ['programming', 'technology', 'MachineLearning', 'startups', 'webdev', 'artificial']
            
            for subreddit in tech_subreddits:
                if len(tech_articles) >= min_tech_articles:
                    break
                    
                try:
                    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=10"
                    headers = {'User-Agent': 'PulseBot/1.0'}
                    response = requests.get(url, headers=headers, timeout=10)
                    data = response.json()
                    
                    for post in data['data']['children']:
                        if len(tech_articles) >= min_tech_articles:
                            break
                            
                        post_data = post['data']
                        title = post_data.get('title', 'No title')
                        category = categorize_article(title)
                        
                        # Only include tech articles
                        if (category in tech_categories and 
                            not post_data.get('is_self') and 
                            post_data.get('url')):
                            
                            article = {
                                "title": title,
                                "link": post_data.get('url', ''),
                                "summary": f"Reddit discussion with {post_data.get('score', 0)} upvotes",
                                "published": datetime.fromtimestamp(post_data.get('created_utc', 0)).strftime('%Y-%m-%d'),
                                "source": f"r/{subreddit}",
                                "category": category,
                                "score": post_data.get('score', 0),
                                "is_guaranteed_tech": True
                            }
                            tech_articles.append(article)
                            
                except Exception as e:
                    print(f"Error fetching from tech r/{subreddit}: {e}")
                    continue
                    
        except Exception as e:
            print(f"Error fetching tech Reddit: {e}")
    
    # If still not enough, add some fallback tech articles
    if len(tech_articles) < min_tech_articles:
        print("  - Adding fallback tech articles...")
        fallback_tech_articles = [
            {
                "title": "Latest Advances in AI Model Architecture and Performance",
                "link": "https://example.com/ai-advances",
                "summary": "Recent developments in AI model efficiency and capability improvements across major tech companies",
                "published": datetime.now().strftime('%Y-%m-%d'),
                "source": "Tech News",
                "category": "ai_ml",
                "is_guaranteed_tech": True
            },
            {
                "title": "Cloud Computing Trends: Serverless and Edge Computing Growth",
                "link": "https://example.com/cloud-trends",
                "summary": "Analysis of how serverless computing and edge infrastructure are reshaping modern application development",
                "published": datetime.now().strftime('%Y-%m-%d'),
                "source": "Tech News",
                "category": "engineering",
                "is_guaranteed_tech": True
            },
            {
                "title": "Startup Funding Landscape: Tech Companies Leading Investment Growth",
                "link": "https://example.com/startup-funding",
                "summary": "Overview of current venture capital trends and which tech sectors are attracting the most investment",
                "published": datetime.now().strftime('%Y-%m-%d'),
                "source": "Business News",
                "category": "business",
                "is_guaranteed_tech": True
            }
        ]
        
        for fallback in fallback_tech_articles:
            if len(tech_articles) >= min_tech_articles:
                break
            tech_articles.append(fallback)
    
    print(f"  ✅ Secured {len(tech_articles)} guaranteed tech articles")
    return tech_articles[:min_tech_articles]

def fetch_real_news(user_profile, limit=15):
    """Fetch real news tailored to user's profile with guaranteed tech articles"""
    print(f"=== FETCHING PERSONALIZED NEWS ===")
    print(f"Profile: {user_profile}")
    
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    print(f"Targeting role: {primary_role}")
    print(f"Targeting interests: {interests}")
    
    # Initialize tracking for this user if not exists
    user_id = user_profile.get('user_id', 'default')
    if user_id not in shown_articles:
        shown_articles[user_id] = set()
    
    # FIRST: Get guaranteed tech articles (at least 3)
    guaranteed_tech_articles = fetch_guaranteed_tech_articles(user_profile, min_tech_articles=3)
    
    # SECOND: Get remaining articles using existing logic
    remaining_limit = max(1, limit - len(guaranteed_tech_articles))
    
    # Fetch from multiple sources with enhanced randomization
    sources = []
    
    # For design roles, prioritize design-heavy sources
    if primary_role == 'design':
        print("🎨 Design role detected - prioritizing design content...")
        
        # HackerNews with design focus
        print("Fetching design-focused HackerNews content...")
        hn_articles = fetch_hackernews_stories_varied(primary_role, interests, limit=25)
        # Filter HackerNews articles more strictly for design content
        hn_design_articles = [a for a in hn_articles if 
                            calculate_article_relevance_score(a, user_profile) > 10]
        sources.append(('HackerNews', hn_design_articles))
        
        # Reddit with heavy design focus
        print("Fetching design-focused Reddit content...")
        reddit_articles = fetch_reddit_varied(primary_role, interests, limit=30)
        # Filter Reddit articles for design relevance
        reddit_design_articles = [a for a in reddit_articles if 
                                calculate_article_relevance_score(a, user_profile) > 5]
        sources.append(('Reddit', reddit_design_articles))
        
        # News API with design keywords
        if NEWS_API_KEY:
            print("Fetching design-focused News API content...")
            news_articles = fetch_newsapi_varied(primary_role, interests, limit=20)
            # Filter news articles for design relevance
            news_design_articles = [a for a in news_articles if 
                                  calculate_article_relevance_score(a, user_profile) > 8]
            sources.append(('NewsAPI', news_design_articles))
    else:
        # Original logic for other roles
        print("Fetching with standard prioritization...")
        
        # HackerNews with multiple strategies
        print("Fetching from HackerNews...")
        hn_articles = fetch_hackernews_stories_varied(primary_role, interests, limit=20)
        sources.append(('HackerNews', hn_articles))
        
        # Reddit with varied subreddits and sort types
        print("Fetching from Reddit...")
        reddit_articles = fetch_reddit_varied(primary_role, interests, limit=20)
        sources.append(('Reddit', reddit_articles))
        
        # News API with different search strategies
        if NEWS_API_KEY:
            print("Fetching from News API...")
            news_articles = fetch_newsapi_varied(primary_role, interests, limit=15)
            sources.append(('NewsAPI', news_articles))
    
    # Combine all sources with source balancing
    all_articles = []
    for source_name, articles in sources:
        print(f"Got {len(articles)} articles from {source_name}")
        all_articles.extend(articles)
    
    # Filter out previously shown articles
    print(f"Total articles before filtering: {len(all_articles)}")
    filtered_articles = []
    for article in all_articles:
        article_id = f"{article['title']}:{article['source']}"
        if article_id not in shown_articles[user_id]:
            filtered_articles.append(article)
    
    print(f"Articles after filtering shown articles: {len(filtered_articles)}")
    
    # Add comprehensive randomization
    random.shuffle(filtered_articles)
    
    # Enhanced scoring based on user profile with randomization
    scored_articles = []
    for article in filtered_articles:
        base_score = calculate_article_relevance_score(article, user_profile)
        # Add randomization to scores to ensure variety
        random_factor = random.uniform(0.9, 1.1)  # Reduced randomization for design to maintain quality
        final_score = base_score * random_factor
        scored_articles.append((final_score, article))
    
    # Sort by score but add some randomization to prevent always same order
    scored_articles.sort(key=lambda x: x[0], reverse=True)
    
    # For design roles, be more selective about quality
    if primary_role == 'design':
        # Only include articles with decent scores
        high_quality_articles = [(score, article) for score, article in scored_articles if score > 5]
        if len(high_quality_articles) < remaining_limit:
            # If we don't have enough high-quality articles, include some lower-scoring ones
            remaining_articles = [(score, article) for score, article in scored_articles if score <= 5]
            top_candidates = high_quality_articles + remaining_articles[:remaining_limit - len(high_quality_articles)]
        else:
            top_candidates = high_quality_articles[:remaining_limit * 2]
    else:
        # Original logic for other roles
        top_candidates = scored_articles[:remaining_limit * 2]
    
    # Add some randomization to final selection
    random.shuffle(top_candidates)
    
    # Remove duplicates by title similarity
    unique_articles = remove_duplicate_articles([article for score, article in top_candidates])
    
    # Final selection of remaining articles
    remaining_articles = unique_articles[:remaining_limit]
    
    # COMBINE: Guaranteed tech articles + remaining articles
    final_articles = guaranteed_tech_articles + remaining_articles
    
    # Shuffle the final list so tech articles aren't always first
    random.shuffle(final_articles)
    
    # Ensure we don't exceed the limit
    final_articles = final_articles[:limit]
    
    # Track the articles we're showing
    for article in final_articles:
        article_id = f"{article['title']}:{article['source']}"
        shown_articles[user_id].add(article_id)
    
    # Clean up old shown articles (keep only last 100 to prevent memory issues)
    if len(shown_articles[user_id]) > 100:
        shown_articles[user_id] = set(list(shown_articles[user_id])[-100:])
    
    print(f"Final articles: {len(final_articles)} after all filtering and randomization")
    
    # Show what we're returning with tech guarantee info
    print("Final articles with tech guarantee:")
    tech_count = 0
    for i, article in enumerate(final_articles):
        is_tech = article.get('category') in ['engineering', 'ai_ml', 'product', 'business', 'tech_general']
        is_guaranteed = article.get('is_guaranteed_tech', False)
        if is_tech:
            tech_count += 1
        status = "🔧 GUARANTEED TECH" if is_guaranteed else ("🔧 TECH" if is_tech else "📰 NON-TECH")
        print(f"  {i+1}. {status} [{article['category']}] - {article['title'][:50]}...")
    
    print(f"📊 Tech articles in final selection: {tech_count}/{len(final_articles)}")
    
    return final_articles

def fetch_hackernews_stories_filtered(role, interests, limit=15):
    """Fetch HackerNews stories with better filtering"""
    try:
        # Get more stories to have better selection
        top_stories_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
        response = requests.get(top_stories_url, timeout=10)
        story_ids = response.json()
        
        # Randomize the starting point to get variety
        start_idx = random.randint(0, min(50, len(story_ids) - limit * 2))
        story_ids = story_ids[start_idx:start_idx + limit * 2]  # Get more to filter from
        
        articles = []
        for story_id in story_ids:
            try:
                story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                story_response = requests.get(story_url, timeout=5)
                story_data = story_response.json()
                
                if story_data and story_data.get('type') == 'story' and story_data.get('url'):
                    title = story_data.get('title', 'No title')
                    category = categorize_article(title)
                    
                    # Filter based on role and interests
                    if is_article_relevant(title, role, interests):
                        article = {
                            "title": title,
                            "link": story_data.get('url', ''),
                            "summary": f"HackerNews discussion with {story_data.get('score', 0)} points and {story_data.get('descendants', 0)} comments",
                            "published": datetime.fromtimestamp(story_data.get('time', 0)).strftime('%Y-%m-%d'),
                            "source": "Hacker News",
                            "category": category,
                            "score": story_data.get('score', 0)
                        }
                        articles.append(article)
                        
                        if len(articles) >= limit:
                            break
                            
            except Exception as e:
                print(f"Error fetching story {story_id}: {e}")
                continue
                
        return articles
    except Exception as e:
        print(f"Error fetching HackerNews: {e}")
        return []

def fetch_reddit_filtered(role, interests, limit=15):
    """Fetch from Reddit with role-specific subreddits"""
    try:
        # Choose subreddits based on role
        role_subreddits = {
            'design': ['design', 'userexperience', 'web_design', 'graphic_design', 'UI_Design', 'productdesign'],
            'engineering': ['programming', 'webdev', 'javascript', 'python', 'reactjs', 'MachineLearning'],
            'product': ['product_management', 'startups', 'entrepreneur', 'productivity'],
            'business': ['startups', 'entrepreneur', 'business', 'investing'],
            'ai_ml': ['MachineLearning', 'artificial', 'deeplearning', 'ChatGPT'],
            'general': ['technology', 'programming', 'startups']
        }
        
        subreddits = role_subreddits.get(role, role_subreddits['general'])
        articles = []
        
        for subreddit in subreddits[:4]:  # Limit to 4 subreddits
            try:
                # Add randomization to get different posts
                sort_types = ['hot', 'top', 'new']
                sort_type = random.choice(sort_types)
                
                url = f"https://www.reddit.com/r/{subreddit}/{sort_type}.json?limit=8"
                headers = {'User-Agent': 'PulseBot/1.0'}
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()
                
                for post in data['data']['children']:
                    post_data = post['data']
                    title = post_data.get('title', 'No title')
                    
                    # Filter by relevance
                    if (not post_data.get('is_self') and 
                        post_data.get('url') and 
                        is_article_relevant(title, role, interests) and
                        post_data.get('score', 0) > 10):  # Minimum score threshold
                        
                        article = {
                            "title": title,
                            "link": post_data.get('url', ''),
                            "summary": post_data.get('selftext', '')[:200] + "..." if post_data.get('selftext') else f"Reddit discussion with {post_data.get('score', 0)} upvotes",
                            "published": datetime.fromtimestamp(post_data.get('created_utc', 0)).strftime('%Y-%m-%d'),
                            "source": f"r/{subreddit}",
                            "category": categorize_article(title),
                            "score": post_data.get('score', 0)
                        }
                        articles.append(article)
                        
                        if len(articles) >= limit:
                            break
                            
            except Exception as e:
                print(f"Error fetching from r/{subreddit}: {e}")
                continue
                
        return articles[:limit]
    except Exception as e:
        print(f"Error fetching Reddit: {e}")
        return []

def fetch_newsapi_filtered(role, interests, limit=10):
    """Fetch from News API with role-specific keywords"""
    if not NEWS_API_KEY:
        return []
        
    try:
        # Build search query based on role and interests
        role_keywords = {
            'design': 'design OR "user experience" OR "UI/UX" OR figma OR adobe',
            'engineering': 'programming OR "software development" OR javascript OR python OR react',
            'product': '"product management" OR startup OR "product launch" OR SaaS',
            'business': 'startup OR funding OR "venture capital" OR IPO OR acquisition',
            'ai_ml': '"artificial intelligence" OR "machine learning" OR AI OR ML OR ChatGPT',
            'general': 'technology OR tech OR startup'
        }
        
        query = role_keywords.get(role, role_keywords['general'])
        
        # Add interests to query
        if interests:
            interest_terms = ' OR '.join([f'"{interest}"' for interest in interests])
            query = f"({query}) OR ({interest_terms})"
        
        url = "https://newsapi.org/v2/everything"
        params = {
            'apiKey': NEWS_API_KEY,
            'q': query,
            'language': 'en',
            'sortBy': 'publishedAt',
            'pageSize': limit,
            'from': (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')  # Last 3 days
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        articles = []
        for article_data in data.get('articles', []):
            title = article_data.get('title', 'No title')
            if is_article_relevant(title, role, interests):
                article = {
                    "title": title,
                    "link": article_data.get('url', ''),
                    "summary": article_data.get('description', 'No description available'),
                    "published": article_data.get('publishedAt', '').split('T')[0],
                    "source": article_data.get('source', {}).get('name', 'Unknown'),
                    "category": categorize_article(title)
                }
                articles.append(article)
                
        return articles
    except Exception as e:
        print(f"Error fetching News API: {e}")
        return []

def is_article_relevant(title, role, interests):
    """Check if article is relevant to user's role and interests - intelligent design detection"""
    title_lower = title.lower()
    
    # Enhanced role-specific keywords with better coverage
    role_keywords = {
        'design': [
            # Core design terms
            'design', 'designer', 'ui', 'ux', 'user experience', 'user interface',
            # Tools and software
            'figma', 'sketch', 'adobe', 'photoshop', 'illustrator', 'xd', 'framer', 'invision',
            # Design concepts
            'prototype', 'wireframe', 'mockup', 'typography', 'visual design', 'graphic design',
            'interface design', 'interaction design', 'product design', 'web design', 'mobile design',
            # Design systems and processes
            'design system', 'design pattern', 'design thinking', 'design process', 'design ops',
            'component library', 'style guide', 'brand', 'branding', 'logo', 'identity',
            # UX/UI specific
            'usability', 'accessibility', 'user research', 'user testing', 'persona', 'journey map',
            'information architecture', 'navigation', 'layout', 'grid', 'color theory', 'contrast',
            # Modern design trends
            'dark mode', 'mobile-first', 'responsive design', 'animation', 'micro-interaction',
            'glassmorphism', 'neumorphism', 'minimalism', 'flat design', 'material design'
        ],
        'engineering': ['programming', 'code', 'developer', 'javascript', 'python', 'react', 'api', 'framework', 'github', 'software', 'technical'],
        'product': ['product', 'management', 'roadmap', 'feature', 'user research', 'analytics', 'metrics', 'strategy'],
        'business': ['business', 'startup', 'funding', 'revenue', 'growth', 'market', 'strategy', 'investment'],
        'ai_ml': ['ai', 'artificial intelligence', 'machine learning', 'ml', 'neural', 'algorithm', 'data science'],
        'general': ['technology', 'tech', 'innovation', 'digital']
    }
    
    # Check role relevance
    role_terms = role_keywords.get(role, role_keywords['general'])
    role_match = any(term in title_lower for term in role_terms)
    
    # Check interest relevance
    interest_match = any(interest.lower() in title_lower for interest in interests)
    
    # For design roles, use comprehensive and intelligent matching
    if role == 'design':
        # 1. Direct role match (from expanded keywords above)
        if role_match:
            return True
        
        # 2. Interest match for design-related interests
        design_interests = ['design', 'ui', 'ux', 'user experience', 'product design', 'graphic design', 'web design', 'visual design']
        if any(interest.lower() in title_lower for interest in interests if interest.lower() in design_interests):
            return True
        
        # 3. Check if categorized as design (leverages our comprehensive categorization)
        if categorize_article(title) == 'design':
            return True
        
        # 4. Design context indicators (visual, creative, aesthetic content)
        design_context_terms = [
            'visual', 'aesthetic', 'beautiful', 'creative', 'artistic', 'style', 'styled',
            'theme', 'color', 'colours', 'font', 'typography', 'layout', 'composition',
            'interface', 'interaction', 'animation', 'transition', 'hover', 'responsive',
            'mobile', 'web', 'app', 'website', 'landing page', 'homepage', 'dashboard',
            'component', 'library', 'system', 'pattern', 'guide', 'guideline',
            'inspiration', 'showcase', 'portfolio', 'gallery', 'collection', 'examples',
            'trends', 'modern', 'minimalist', 'clean', 'elegant', 'stunning', 'awesome',
            'cool', 'amazing', 'love', 'beautiful', 'gorgeous', 'sleek', 'polished'
        ]
        
        if any(term in title_lower for term in design_context_terms):
            return True
        
        # 5. Frontend/web development that's design-relevant
        frontend_terms = ['css', 'html', 'scss', 'sass', 'less', 'styled-components', 'tailwind']
        if any(term in title_lower for term in frontend_terms):
            return True
        
        # 6. Tools and platforms commonly used by designers
        design_tools = ['figma', 'sketch', 'adobe', 'photoshop', 'illustrator', 'xd', 'framer', 'canva']
        if any(tool in title_lower for tool in design_tools):
            return True
        
        # 7. Component/library related (important for design systems)
        component_terms = ['component', 'library', 'components', 'react', 'vue', 'angular']
        if any(term in title_lower for term in component_terms):
            # Check for design context
            design_context = ['design', 'ui', 'ux', 'interface', 'styled', 'theme', 'system']
            if any(context in title_lower for context in design_context):
                return True
        
        # 8. Creative/visual content indicators
        creative_indicators = [
            'cover', 'poster', 'logo', 'icon', 'illustration', 'graphic', 'image',
            'photo', 'picture', 'artwork', 'design', 'mockup', 'prototype',
            'wireframe', 'sketch', 'drawing', 'concept', 'idea', 'creation'
        ]
        
        if any(indicator in title_lower for indicator in creative_indicators):
            return True
        
        # 9. Design process and methodology
        process_terms = [
            'process', 'method', 'approach', 'strategy', 'technique', 'principle',
            'best practice', 'guideline', 'standard', 'framework', 'methodology',
            'workflow', 'pipeline', 'system', 'pattern', 'template'
        ]
        
        if any(term in title_lower for term in process_terms):
            # Check for design context
            design_context = ['design', 'ui', 'ux', 'user', 'interface', 'visual', 'creative']
            if any(context in title_lower for context in design_context):
                return True
        
        # 10. Only exclude if it's clearly non-design technical content
        exclude_terms = [
            'database', 'sql', 'backend', 'server', 'api', 'algorithm', 'data science',
            'machine learning', 'artificial intelligence', 'cryptocurrency', 'blockchain',
            'devops', 'docker', 'kubernetes', 'security', 'hacking', 'penetration testing',
            'bernie sanders', 'politics', 'political', 'senator', 'congress', 'government',
            'foreign keys', 'database design', 'sql query', 'database schema', 'orm',
            'performance optimization', 'caching', 'scaling', 'load balancing'
        ]
        
        # If it contains exclude terms without design context, exclude it
        if any(term in title_lower for term in exclude_terms):
            design_context = ['design', 'ui', 'ux', 'user', 'interface', 'visual', 'frontend']
            if not any(context in title_lower for context in design_context):
                return False
        
        # 11. Final catch-all for general tech terms that might be design-relevant
        general_tech_terms = ['software', 'app', 'web', 'mobile', 'technology', 'tech', 'digital']
        if any(term in title_lower for term in general_tech_terms):
            # Must have some design context to be included
            design_context = ['design', 'ui', 'ux', 'user', 'interface', 'visual', 'creative', 'aesthetic']
            if any(context in title_lower for context in design_context):
                return True
        
        # 12. Default to False for design roles if we haven't matched anything above
        # This ensures we're selective and only include genuinely design-related content
        return False
    
    # For other roles, use the original logic but slightly more strict
    if role_match:
        return True
    
    # Strong interest match
    if interest_match:
        return True
    
    # General tech relevance only if no specific role match
    general_tech = any(term in title_lower for term in ['innovation', 'digital transformation', 'startup', 'product launch'])
    return general_tech

def calculate_article_relevance_score(article, user_profile):
    """Calculate relevance score for article based on user profile - heavily design-focused"""
    score = 0
    title_lower = article['title'].lower()
    summary_lower = article.get('summary', '').lower()
    
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    # MASSIVE boost for design roles with design content
    if primary_role == 'design':
        # Core design terms get huge boost
        core_design_terms = [
            'design', 'designer', 'ui', 'ux', 'user experience', 'user interface',
            'figma', 'sketch', 'adobe', 'prototype', 'wireframe', 'typography',
            'visual design', 'interface design', 'design system', 'component library'
        ]
        
        design_matches = sum(1 for term in core_design_terms if term in title_lower or term in summary_lower)
        if design_matches > 0:
            score += 50 * design_matches  # HUGE boost for design content
        
        # Specific design tool mentions
        design_tools = ['figma', 'sketch', 'adobe', 'photoshop', 'illustrator', 'xd', 'framer', 'invision']
        tool_matches = sum(1 for tool in design_tools if tool in title_lower or tool in summary_lower)
        if tool_matches > 0:
            score += 30 * tool_matches
        
        # Design process and methodology terms
        design_process = [
            'design thinking', 'design process', 'user research', 'user testing', 
            'design ops', 'design sprint', 'persona', 'journey map', 'usability testing'
        ]
        process_matches = sum(1 for term in design_process if term in title_lower or term in summary_lower)
        if process_matches > 0:
            score += 25 * process_matches
        
        # Modern design trends and concepts
        design_trends = [
            'design system', 'dark mode', 'mobile-first', 'responsive design', 'accessibility',
            'micro-interaction', 'animation', 'glassmorphism', 'neumorphism', 'material design'
        ]
        trend_matches = sum(1 for trend in design_trends if trend in title_lower or trend in summary_lower)
        if trend_matches > 0:
            score += 20 * trend_matches
        
        # Design-related frontend tech (but lower priority)
        frontend_tech = ['css', 'html', 'react', 'vue', 'angular', 'component', 'frontend', 'web development']
        frontend_matches = sum(1 for tech in frontend_tech if tech in title_lower or tech in summary_lower)
        if frontend_matches > 0:
            # Only boost if there's also design context
            design_context = ['design', 'ui', 'ux', 'interface', 'user', 'frontend', 'web', 'mobile', 'app']
            if any(context in title_lower or context in summary_lower for context in design_context):
                score += 15 * frontend_matches
        
        # Penalty for non-design tech content
        non_design_tech = [
            'database', 'backend', 'server', 'api', 'algorithm', 'data science',
            'machine learning', 'artificial intelligence', 'cryptocurrency', 'blockchain'
        ]
        non_design_matches = sum(1 for term in non_design_tech if term in title_lower or term in summary_lower)
        if non_design_matches > 0:
            score -= 20 * non_design_matches  # Penalty for non-design content
    
    # Score based on exact category match
    if article.get('category') == primary_role:
        score += 25  # Increased from 10
    
    # Score based on interests (higher for design interests)
    for interest in interests:
        if interest.lower() in title_lower or interest.lower() in summary_lower:
            if interest.lower() in ['design', 'ui', 'ux', 'user experience', 'product design', 'graphic design']:
                score += 15  # Higher for design interests
            else:
                score += 8   # Lower for other interests
    
    # Boost for recent articles
    try:
        article_date = datetime.strptime(article['published'], '%Y-%m-%d')
        days_old = (datetime.now() - article_date).days
        if days_old <= 1:
            score += 12  # Increased from 8
        elif days_old <= 3:
            score += 6   # Increased from 4
        elif days_old <= 7:
            score += 3   # Increased from 2
    except:
        pass
    
    # Boost for popular articles
    if 'score' in article and article['score']:
        if article['score'] > 100:
            score += 5
        elif article['score'] > 50:
            score += 3
    
    # Source-based bonuses for design content
    source = article.get('source', '').lower()
    if primary_role == 'design':
        design_sources = ['design', 'ux', 'ui', 'figma', 'adobe', 'dribbble', 'behance']
        if any(ds in source for ds in design_sources):
            score += 10
    
    return max(0, score)  # Ensure score is never negative

def remove_duplicate_articles(articles):
    """Remove duplicate articles based on title similarity"""
    unique_articles = []
    seen_titles = set()
    
    for article in articles:
        title_clean = article['title'].lower().strip()
        # Simple deduplication by title
        if title_clean not in seen_titles:
            seen_titles.add(title_clean)
            unique_articles.append(article)
    
    return unique_articles

def extract_article_content(url):
    """Extract full article content from URL using BeautifulSoup"""
    try:
        print(f"Extracting content from: {url}")
        
        # Send request with headers to avoid being blocked
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Parse HTML content
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract title
        title = ""
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text().strip()
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "header", "footer", "aside", "advertisement"]):
            script.decompose()
        
        # Try to find main content area
        content_selectors = [
            'article', 'main', '[role="main"]', '.content', '.post-content', 
            '.entry-content', '.article-content', '#content', '.story-body'
        ]
        
        article_content = None
        for selector in content_selectors:
            article_content = soup.select_one(selector)
            if article_content:
                break
        
        # If no specific content area found, use body
        if not article_content:
            article_content = soup.find('body')
        
        # Extract text content
        text = ""
        if article_content:
            # Get all paragraphs and text content
            paragraphs = article_content.find_all(['p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            text_parts = []
            
            for para in paragraphs:
                para_text = para.get_text().strip()
                if para_text and len(para_text) > 20:  # Filter out short snippets
                    text_parts.append(para_text)
            
            text = '\n\n'.join(text_parts)
        
        # Clean up the text (remove excessive whitespace)
        if text:
            text = re.sub(r'\n\s*\n', '\n\n', text)
            text = re.sub(r' +', ' ', text)
            text = re.sub(r'\t', ' ', text)
        
        # Create content object
        content = {
            'title': title,
            'text': text,
            'authors': [],  # Can't easily extract authors with this method
            'publish_date': None,  # Can't easily extract publish date
            'summary': '',
            'url': url
        }
        
        # Limit text length for LLM processing (increased to 20000 chars for better coverage)
        if len(content['text']) > 20000:
            # Smart truncation - try to end at a paragraph boundary
            truncate_at = 20000
            # Look for paragraph breaks near the end
            for i in range(truncate_at - 200, truncate_at):
                if i < len(content['text']) and content['text'][i:i+2] == '\n\n':
                    truncate_at = i
                    break
            
            content['text'] = content['text'][:truncate_at]
            content['truncated'] = True
            # More informative truncation message
            original_length = len(content['text']) if 'text' in content else 0
            content['truncation_info'] = f"Article truncated at {truncate_at} chars (original ~{original_length + 5000} chars)"
        else:
            content['truncated'] = False
        
        print(f"Successfully extracted {len(content['text'])} characters")
        return content
        
    except Exception as e:
        print(f"Error extracting article content: {e}")
        return None

def handle_article_read_request(user_id, user_message, recent_articles, user_profile, channel_id):
    """Handle requests to read/summarize full articles - improved to handle search results"""
    try:
        # Check if user has recent search results
        conversation_context = conversation_history.get(user_id, {})
        last_search = conversation_context.get('last_search')
        
        # Find the article they're asking about
        article_title = identify_article_from_question(user_message, recent_articles)
        
        # If no article found in digest, check search results
        if not article_title and last_search:
            search_results = last_search.get('results', [])
            
            # Try to match against search results with better logic
            message_lower = user_message.lower()
            
            # Check for specific search result references
            if 'rgd' in message_lower and ('top 5' in message_lower or 'top5' in message_lower):
                for result in search_results:
                    if 'rgd' in result['title'].lower() and 'top 5' in result['title'].lower():
                        article_title = result['title']
                        break
            
            # Check for other specific matches
            search_terms = ['builtin', 'built in', 'designerup', 'designer up', 'designrush', 'design rush', 'designsystems.surf']
            for term in search_terms:
                if term in message_lower:
                    for result in search_results:
                        if term in result['title'].lower():
                            article_title = result['title']
                            break
                    if article_title:
                        break
            
            # Fallback: check for any keyword matches
            if not article_title:
                for result in search_results:
                    if any(word in result['title'].lower() for word in message_lower.split() if len(word) > 3):
                        article_title = result['title']
                        break
        
        if not article_title:
            if last_search:
                # Show options from both digest and search results
                options = "I can read articles from:\n\n**Your Recent Digest:**\n"
                for i, article in enumerate(recent_articles[:3], 1):
                    options += f"{i}. {article['title'][:50]}...\n"
                
                options += "\n**Recent Search Results:**\n"
                for i, result in enumerate(last_search.get('results', [])[:3], 1):
                    options += f"{i}. {result['title'][:50]}...\n"
                
                options += "\nTry: 'read the RGD article' or 'read the design system article'"
                
                slack_client.chat_postMessage(
                    channel=channel_id,
                    text=options
                )
            else:
                slack_client.chat_postMessage(
                    channel=channel_id,
                    text="I'm not sure which article you're referring to. Could you be more specific? You can say something like 'read the design article' or 'read article 1'."
                )
            return True
        
        # Find the article object (from digest or search results)
        target_article = None
        article_source = "digest"
        
        # Check digest first
        for article in recent_articles:
            if article['title'] == article_title:
                target_article = article
                break
        
        # Check search results if not found in digest
        if not target_article and last_search:
            for result in last_search.get('results', []):
                if result['title'] == article_title:
                    target_article = {
                        'title': result['title'],
                        'link': result['url'],
                        'summary': result['snippet']
                    }
                    article_source = "search"
                    break
        
        if not target_article:
            slack_client.chat_postMessage(
                channel=channel_id,
                text=f"Sorry, I couldn't find the article '{article_title}'. Could you try being more specific?"
            )
            return True
        
        # Send "reading" message
        slack_client.chat_postMessage(
            channel=channel_id,
            text=f"📖 Reading the full article: {target_article['title'][:60]}..."
        )
        
        # Extract full article content
        article_content = extract_article_content(target_article['link'])
        
        if not article_content or not article_content.get('text'):
            slack_client.chat_postMessage(
                channel=channel_id,
                text=f"Sorry, I couldn't access the full content of this article. The site might be blocking automated access or the article format isn't supported.\n\nBased on the title '{target_article['title']}' and summary, I can still discuss what I know about this topic if you'd like."
            )
            return True
        
        # Create summary prompt
        role = user_profile.get('primary_role', 'professional')
        interests = user_profile.get('secondary_interests', [])
        
        summary_prompt = f"""
        You are PulseBot chatting with a {role} who is interested in {', '.join(interests)}. 
        
        Summarize this article in a conversational way:
        
        Title: {article_content['title']}
        Content: {article_content['text']}
        
        Instructions:
        - Write a comprehensive summary like you're telling a colleague about an important article
        - Keep it informative but conversational (4-6 sentences)
        - Highlight the most interesting/relevant points for someone in {role}
        - Use minimal emojis (0-1 max)
        - Be casual and natural
        - Focus on key insights and actionable information
        - If the article was truncated, mention there's more content but focus on what you did read
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are PulseBot, a conversational AI that summarizes articles in a casual, friendly way for professionals."},
                {"role": "user", "content": summary_prompt}
            ],
            temperature=0.7,
            max_tokens=1000
        )
        
        summary = response.choices[0].message.content.strip()
        
        # Add source information
        if article_source == "search":
            summary = f"**From your recent search:** {summary}"
        
        # Add truncation notice only if significantly truncated
        if article_content.get('truncated'):
            # Only show truncation notice if we truncated a substantial amount
            original_length = len(article_content.get('text', ''))
            if original_length > 15000:  # Only show if original was quite long
                summary += "\n\n*Note: This is a summary of the full article - I can search for more specific details if needed.*"
        
        # Store this in conversation history
        conversation_history[user_id] = {
            'last_article_discussed': article_title,
            'last_conversation': f"User asked to read: '{user_message}' - Bot summarized full article",
            'last_user_message': user_message,
            'conversation_topic': 'full article summary',
            'timestamp': datetime.now().isoformat(),
            'full_content_available': True
        }
        
        # Send summary
        slack_client.chat_postMessage(
            channel=channel_id,
            text=summary
        )
        
        return True
        
    except Exception as e:
        print(f"Error in article read request: {e}")
        slack_client.chat_postMessage(
            channel=channel_id,
            text="Sorry, there was an error reading the article. Please try again."
        )
        return False

def search_web(query, num_results=5):
    """Search the web using DuckDuckGo and return formatted results"""
    try:
        print(f"🔍 Searching web for: {query}")
        
        # DuckDuckGo search URL
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find search results
        results = []
        result_divs = soup.find_all('div', class_='result')
        
        for div in result_divs[:num_results]:
            try:
                # Extract title and URL
                title_link = div.find('a', class_='result__a')
                if not title_link:
                    continue
                    
                title = title_link.get_text().strip()
                url = title_link.get('href')
                
                # Extract snippet
                snippet_div = div.find('div', class_='result__snippet')
                snippet = snippet_div.get_text().strip() if snippet_div else ""
                
                # Filter out very short or unhelpful results
                if title and url and len(title) > 10:
                    results.append({
                        'title': title,
                        'url': url,
                        'snippet': snippet
                    })
                    
            except Exception as e:
                print(f"Error parsing result: {e}")
                continue
        
        print(f"Found {len(results)} search results")
        return results
        
    except Exception as e:
        print(f"Error searching web: {e}")
        return []

def process_search_results(results, query, user_profile):
    """Process search results and create a conversational response with specific details"""
    if not results:
        return "I couldn't find any results for that search. Try rephrasing your query or being more specific."
    
    role = user_profile.get('primary_role', 'professional')
    interests = user_profile.get('secondary_interests', [])
    
    # Build a more structured response with actual search results
    response_parts = []
    
    # Add a brief contextual intro
    if len(results) == 1:
        response_parts.append(f"I found a good resource on {query}:")
    else:
        response_parts.append(f"I found {len(results)} resources on {query}:")
    
    response_parts.append("")  # Empty line
    
    # Add structured results with actual details
    for i, result in enumerate(results[:5], 1):
        title = result['title']
        snippet = result['snippet']
        url = result['url']
        
        # Format each result clearly
        result_text = f"**{i}. {title}**"
        if snippet:
            # Clean up snippet (remove extra whitespace, limit length)
            clean_snippet = snippet.strip()
            if len(clean_snippet) > 150:
                clean_snippet = clean_snippet[:150] + "..."
            result_text += f"\n{clean_snippet}"
        
        result_text += f"\n<{url}>"
        response_parts.append(result_text)
    
    # Add contextual closing based on role
    if role == 'design':
        response_parts.append("\n💡 **For your design work:** I can read any of these articles to get the full details, or search for more specific aspects like 'liquid glass UI patterns' or 'liquid glass implementation guide'.")
    elif role == 'engineering':
        response_parts.append("\n💡 **For development:** I can read the full articles to extract code examples, implementation details, or search for more technical aspects.")
    else:
        response_parts.append("\n💡 **Next steps:** I can read any of these articles for full details, or search for related topics. Just say 'read the [topic] article' or 'search for [related topic]'.")
    
    return "\n".join(response_parts)

def handle_search_request(user_id, query, user_profile, channel_id):
    """Handle web search requests - improved to integrate with recent articles"""
    try:
        # Send searching message
        slack_client.chat_postMessage(
            channel=channel_id,
            text=f"🔍 Searching for: {query}..."
        )
        
        # Perform web search
        results = search_web(query, num_results=5)
        
        # Process and respond
        response = process_search_results(results, query, user_profile)
        
        # Store search context for follow-up questions
        search_context = {
            'query': query,
            'results': results,
            'timestamp': datetime.now().isoformat()
        }
        
        # Update conversation history
        if user_id not in conversation_history:
            conversation_history[user_id] = {}
        
        conversation_history[user_id]['last_search'] = search_context
        conversation_history[user_id]['last_conversation'] = f"User searched for: '{query}'"
        conversation_history[user_id]['conversation_topic'] = 'web search'
        conversation_history[user_id]['timestamp'] = datetime.now().isoformat()
        
        # IMPORTANT: Add search results to recent_articles for easier access
        # Convert search results to article format
        search_articles = []
        for result in results:
            search_article = {
                'title': result['title'],
                'link': result['url'],
                'summary': result['snippet'],
                'published': datetime.now().strftime('%Y-%m-%d'),
                'source': 'Web Search',
                'category': 'search_result'
            }
            search_articles.append(search_article)
        
        # Merge with existing recent articles (search results first)
        if user_id in recent_articles:
            # Keep existing articles but prioritize search results
            merged_articles = search_articles + recent_articles[user_id][:10]  # Limit total
            recent_articles[user_id] = merged_articles
        else:
            recent_articles[user_id] = search_articles
        
        # Send response
        slack_client.chat_postMessage(
            channel=channel_id,
            text=response
        )
        
        return True
        
    except Exception as e:
        print(f"Error handling search request: {e}")
        slack_client.chat_postMessage(
            channel=channel_id,
            text="Sorry, I had trouble searching for that. Please try again."
        )
        return False

def detect_search_request(message):
    """Detect if user wants to search the web"""
    message_lower = message.lower()
    
    search_keywords = [
        'search for', 'find me', 'look up', 'search', 'find resources',
        'find articles', 'find information', 'look for', 'research',
        'what is', 'what are', 'how to', 'where can i find',
        'show me', 'get me', 'i need', 'help me find'
    ]
    
    return any(keyword in message_lower for keyword in search_keywords)

def extract_search_query(message):
    """Extract the actual search query from the message"""
    message_lower = message.lower()
    
    # Remove common search prefixes
    prefixes = [
        'search for', 'find me', 'look up', 'search', 'find resources on',
        'find articles on', 'find information on', 'look for', 'research',
        'what is', 'what are', 'how to', 'where can i find',
        'show me', 'get me', 'i need', 'help me find'
    ]
    
    query = message
    for prefix in prefixes:
        if message_lower.startswith(prefix):
            query = message[len(prefix):].strip()
            break
    
    # Clean up the query
    query = query.strip('?.,!')
    
    return query if query else message

def create_user_profile(user_description):
    """Use Groq to analyze user description and create structured profile"""
    try:
        prompt = f"""
        Analyze this user's description and create a structured profile for personalized news curation:
        
        User description: "{user_description}"
        
        IMPORTANT: Look for specific keywords to categorize their role:
        - If they mention "designer", "design", "UI", "UX", "product design" → primary_role should be "design"
        - If they mention "engineer", "developer", "programming", "coding" → primary_role should be "engineering"
        - If they mention "product manager", "PM" → primary_role should be "product"
        - If they mention "business", "sales", "marketing" → primary_role should be "business"
        - If they mention "AI", "ML", "machine learning", "data science" → primary_role should be "ai_ml"
        
        Extract and return ONLY a valid JSON object with this exact structure:
        {{
            "primary_role": "design",
            "secondary_interests": ["technology", "software", "ui", "ux"],
            "industry": "technology",
            "experience_level": "mid",
            "company_stage": "scale-up",
            "specific_technologies": ["figma", "sketch", "design systems"],
            "content_preferences": "design",
            "summary": "Product designer focused on design and user experience"
        }}
        
        Be very careful to:
        1. Match the role accurately based on their description
        2. Include design-related interests if they mention design
        3. Use valid JSON format only
        4. Don't add any extra text outside the JSON
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are an expert at analyzing user descriptions to create personalized profiles. Always return ONLY valid JSON with no extra text."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,  # Lower temperature for more consistent output
            max_tokens=500
        )
        
        profile_text = response.choices[0].message.content.strip()
        print(f"Raw AI response: {profile_text}")
        
        # Clean up the response - remove any markdown or extra text
        profile_text = profile_text.replace('```json', '').replace('```', '').strip()
        
        # Find JSON object
        start = profile_text.find('{')
        end = profile_text.rfind('}') + 1
        
        if start == -1 or end == 0:
            raise ValueError("No JSON found in response")
            
        profile_json = profile_text[start:end]
        print(f"Extracted JSON: {profile_json}")
        
        profile = json.loads(profile_json)
        
        # Validate and fix the profile
        if not profile.get("primary_role"):
            # Fallback logic based on keywords
            desc_lower = user_description.lower()
            if any(word in desc_lower for word in ["designer", "design", "ui", "ux", "product design"]):
                profile["primary_role"] = "design"
            elif any(word in desc_lower for word in ["engineer", "developer", "programming", "coding"]):
                profile["primary_role"] = "engineering"
            else:
                profile["primary_role"] = "general"
        
        # Ensure secondary_interests is a list
        if not isinstance(profile.get("secondary_interests"), list):
            profile["secondary_interests"] = []
            
        # Add design-related interests if they're a designer
        if profile["primary_role"] == "design":
            design_interests = ["design", "ui", "ux", "product design", "user experience"]
            for interest in design_interests:
                if interest not in profile["secondary_interests"]:
                    profile["secondary_interests"].append(interest)
        
        print(f"Final profile: {profile}")
        return profile
        
    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}")
        print(f"Problematic text: {profile_text}")
        # Return a default profile based on manual parsing
        desc_lower = user_description.lower()
        return {
            "primary_role": "design" if any(word in desc_lower for word in ["designer", "design", "ui", "ux"]) else "engineering",
            "secondary_interests": ["design", "technology"] if "design" in desc_lower else ["technology"],
            "industry": "technology",
            "experience_level": "mid",
            "company_stage": "startup",
            "specific_technologies": [],
            "content_preferences": "design" if "design" in desc_lower else "technical",
            "summary": "Design professional" if "design" in desc_lower else "Tech professional"
        }
    except Exception as e:
        print(f"Error creating profile: {e}")
        return None

def fetch_personalized_news(user_profile, limit=15):
    """Fetch real news tailored to user's profile with better filtering"""
    print(f"=== FETCHING PERSONALIZED NEWS ===")
    print(f"Profile: {user_profile}")
    
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    print(f"Targeting role: {primary_role}")
    print(f"Targeting interests: {interests}")
    
    # Try to fetch real news first
    real_articles = fetch_real_news(user_profile, limit * 2)  # Get more to filter from
    
    if real_articles:
        print(f"Successfully fetched {len(real_articles)} filtered articles")
        
        # Show what we found
        print("Top articles found:")
        for i, article in enumerate(real_articles[:5]):
            print(f"  {i+1}. [{article['category']}] {article['title'][:60]}...")
        
        return real_articles[:limit]
    else:
        print("No real articles found, using fallback")
        # Enhanced fallback based on role
        return get_role_specific_fallback(user_profile, limit)

def get_role_specific_fallback(user_profile, limit=15):
    """Generate role-specific fallback articles"""
    role = user_profile.get("primary_role", "engineering")
    
    design_articles = [
        {
            "title": "Figma's New Auto Layout Features Transform Responsive Design",
            "link": "https://figma.com/blog/auto-layout-4",
            "summary": "Figma introduces advanced auto layout capabilities that make responsive design faster and more intuitive for product teams...",
            "published": datetime.now().strftime('%Y-%m-%d'),
            "source": "Figma Blog",
            "category": "design"
        },
        {
            "title": "Apple's Design System Evolution: From Skeuomorphism to Spatial Computing",
            "link": "https://developer.apple.com/design",
            "summary": "A deep dive into how Apple's design philosophy has evolved and what it means for designers working on next-generation interfaces...",
            "published": datetime.now().strftime('%Y-%m-%d'),
            "source": "Apple Developer",
            "category": "design"
        },
        {
            "title": "The Rise of AI-Powered Design Tools: Threat or Opportunity?",
            "link": "https://uxdesign.cc/ai-design-tools",
            "summary": "Exploring how AI is changing the design landscape and what designers need to know to stay relevant in an AI-first world...",
            "published": datetime.now().strftime('%Y-%m-%d'),
            "source": "UX Design",
            "category": "design"
        },
        {
            "title": "Design Systems at Scale: Lessons from Shopify's Polaris",
            "link": "https://polaris.shopify.com",
            "summary": "How Shopify built and maintains one of the most comprehensive design systems, serving thousands of developers and designers...",
            "published": datetime.now().strftime('%Y-%m-%d'),
            "source": "Shopify",
            "category": "design"
        },
        {
            "title": "User Research in the Age of AI: New Methods for Understanding Behavior",
            "link": "https://nngroup.com/articles/ai-user-research",
            "summary": "Nielsen Norman Group explores how AI is transforming user research methodologies and what researchers need to adapt...",
            "published": datetime.now().strftime('%Y-%m-%d'),
            "source": "Nielsen Norman Group",
            "category": "design"
        }
    ]
    
    ai_articles = [
        {
            "title": "Groq's Latest Chip Architecture Achieves 10x Inference Speed",
            "link": "https://groq.com/news/chip-performance",
            "summary": "Groq's new tensor streaming processor delivers unprecedented performance for LLM inference, changing the economics of AI deployment...",
            "published": datetime.now().strftime('%Y-%m-%d'),
            "source": "Groq",
            "category": "ai_ml"
        },
        {
            "title": "The Science Behind Faster AI: Hardware-Software Co-Design",
            "link": "https://groq.com/technology",
            "summary": "Understanding how purpose-built hardware can dramatically improve AI performance compared to traditional GPU architectures...",
            "published": datetime.now().strftime('%Y-%m-%d'),
            "source": "Groq Technology",
            "category": "ai_ml"
        }
    ]
    
    if role == "design":
        # Mix design articles with some AI (since you work at Groq)
        articles = design_articles + ai_articles[:2]
    else:
        # Fallback to mixed content
        articles = design_articles[:3] + ai_articles
    
    # Shuffle for variety
    random.shuffle(articles)
    return articles[:limit]

def debug_news_fetching(user_profile):
    """Debug function to see what news is being fetched"""
    print("\n=== DEBUG NEWS FETCHING ===")
    
    # Test HackerNews
    print("Testing HackerNews...")
    hn_articles = fetch_hackernews_stories_filtered(
        user_profile.get("primary_role", "design"), 
        user_profile.get("secondary_interests", []), 
        limit=5
    )
    print(f"HackerNews found: {len(hn_articles)} articles")
    for article in hn_articles:
        print(f"  - [{article['category']}] {article['title'][:50]}...")
    
    # Test Reddit
    print("\nTesting Reddit...")
    reddit_articles = fetch_reddit_filtered(
        user_profile.get("primary_role", "design"), 
        user_profile.get("secondary_interests", []), 
        limit=5
    )
    print(f"Reddit found: {len(reddit_articles)} articles")
    for article in reddit_articles:
        print(f"  - [{article['category']}] {article['title'][:50]}...")
    
    print("=== END DEBUG ===\n")

def fetch_mock_news_fallback(user_profile, limit=15):
    """Fallback mock news if real sources fail"""
    MOCK_ARTICLES = [
        {
            "title": "OpenAI Releases GPT-5 with Breakthrough Reasoning Capabilities",
            "link": "https://techcrunch.com/gpt5-release",
            "summary": "OpenAI's latest model shows significant improvements in mathematical reasoning and code generation, potentially transforming how developers work with AI...",
            "published": "2025-07-15",
            "source": "TechCrunch",
            "category": "ai_ml"
        },
        {
            "title": "Meta's New React Compiler Reduces Bundle Sizes by 40%",
            "link": "https://react.dev/compiler-announcement",
            "summary": "The experimental React compiler automatically optimizes components, eliminating the need for manual memoization in most cases...",
            "published": "2025-07-15",
            "source": "React Blog",
            "category": "engineering"
        },
        {
            "title": "GitHub Copilot Enterprise Adds Code Review Automation",
            "link": "https://github.blog/copilot-enterprise-review",
            "summary": "New features include automated security vulnerability detection and compliance checking for enterprise development teams...",
            "published": "2025-07-15",
            "source": "GitHub Blog",
            "category": "engineering"
        }
    ]
    
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    # Filter mock articles based on user profile
    relevant_articles = []
    
    for article in MOCK_ARTICLES:
        if (article["category"] == primary_role or 
            article["category"] in interests or
            any(interest in article["title"].lower() or interest in article["summary"].lower() 
                for interest in interests)):
            relevant_articles.append(article)
    
    return relevant_articles[:limit]

def personalized_summarize_with_groq(articles, user_profile):
    """Create a highly personalized digest based on user profile - optimized for Slack"""
    try:
        profile_summary = user_profile.get("summary", "tech professional")
        role = user_profile.get("primary_role", "engineering")
        interests = user_profile.get("secondary_interests", [])
        experience = user_profile.get("experience_level", "mid")
        
        # Prepare content for summarization - use only the first 5 articles that will be shown in Slack
        content = "\n\n".join([
            f"Article {i+1}: {article['title']}\nSummary: {article['summary']}\nSource: {article['source']}\nCategory: {article['category']}"
            for i, article in enumerate(articles[:5])  # Only use first 5 articles that will be shown
        ])
        
        prompt = f"""
        Create a personalized daily digest for this user:
        Profile: {profile_summary}
        Role: {role} ({experience} level)
        Interests: {', '.join(interests)}
        
        Today's articles:
        {content}
        
        Instructions:
        1. Write about ALL 5 articles provided above in the exact order given (Article 1, Article 2, Article 3, Article 4, Article 5)
        2. Start with a brief personalized greeting mentioning their role
        3. For each article, use this exact format:
           
           **Article [number]: [Title]**
           [1-2 sentence summary of what the article is about]
           Why it matters: [1 sentence explaining why this is relevant to them]
        
        4. Use a casual, conversational tone like chatting with a friend
        5. Keep the ENTIRE response under 1500 characters
        6. Use minimal emojis (1-2 max total) and only when they feel natural
        7. Format as plain text, no markdown formatting
        8. Don't be overly formal or structured in the greeting
        9. CRITICAL: You MUST write about all 5 articles in order - no skipping articles
        
        CRITICAL: Keep response under 1500 characters total. Be concise but engaging and natural.
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are a conversational news curator who creates concise, engaging digests under 1500 characters. You chat naturally like a friend, using minimal emojis and keeping things casual."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800  # Reduced token limit
        )
        
        digest = response.choices[0].message.content.strip()
        
        # Ensure it's not too long for Slack
        if len(digest) > 1500:
            digest = digest[:1450] + "..."
        
        return digest
    
    except Exception as e:
        print(f"Error with personalized summarization: {e}")
        return None


# Replace your existing handle_conversation function with this enhanced version:

def handle_conversation(user_id, user_message, channel_id):
    """Handle conversational interactions with enhanced AI capabilities and article context"""
    try:
        user_profile = user_profiles.get(user_id, {})
        
        # Get recent articles for context
        user_recent_articles = recent_articles.get(user_id, [])
        
        # Check if user wants to search the web
        if detect_search_request(user_message):
            search_query = extract_search_query(user_message)
            return handle_search_request(user_id, search_query, user_profile, channel_id)
        
        # Check if user wants to read/summarize a full article
        if is_article_read_request(user_message):
            # Special handling for summary requests without specific article
            message_lower = user_message.lower()
            conversation_context = conversation_history.get(user_id, {})
            
            # If they ask for a summary without specifying an article, use conversation context
            if ('summary' in message_lower and 
                not any(keyword in message_lower for keyword in ['the', 'article', 'rgd', 'built', 'design system']) and
                conversation_context.get('last_search')):
                
                # Use the first search result as context
                last_search = conversation_context.get('last_search')
                if last_search and last_search.get('results'):
                    first_result = last_search['results'][0]
                    enhanced_message = f"read {first_result['title']}"
                    return handle_article_read_request(user_id, enhanced_message, user_recent_articles, user_profile, channel_id)
            
            return handle_article_read_request(user_id, user_message, user_recent_articles, user_profile, channel_id)
        
        # Check if user is asking about specific articles (include user_id for context)
        article_question = detect_article_question(user_message, user_recent_articles, user_id)
        
        if article_question:
            # Handle article-specific questions
            return handle_article_question(user_id, user_message, user_recent_articles, user_profile, channel_id)
        else:
            # Handle general conversation (now much more powerful)
            return handle_general_conversation(user_id, user_message, user_recent_articles, user_profile, channel_id)
        
    except Exception as e:
        print(f"Error in conversation: {e}")
        # Fallback response
        slack_client.chat_postMessage(
            channel=channel_id,
            text="I'm here to help with anything! Ask me questions, search for information, or discuss your articles."
        )
        return False

def detect_article_question(user_message, recent_articles, user_id=None):
    """Detect if user is asking about specific articles, including follow-up questions"""
    message_lower = user_message.lower()
    
    # Check for article-related keywords
    article_keywords = [
        'article', 'story', 'news', 'post', 'link', 'read about', 'more about',
        'tell me more', 'explain', 'details', 'summary', 'what about',
        'thoughts on', 'opinion on', 'article 1', 'article 2', 'first article',
        'second article', 'that article', 'this article', 'the article about',
        'read the', 'summarize', 'full article', 'entire article'
    ]
    
    has_article_keyword = any(keyword in message_lower for keyword in article_keywords)
    
    # Check if they mention specific article titles or topics
    mentions_article_content = False
    if recent_articles:
        for article in recent_articles[:5]:  # Check top 5 articles
            title_words = article['title'].lower().split()
            # Check if 2+ words from title appear in message
            title_matches = sum(1 for word in title_words if len(word) > 3 and word in message_lower)
            if title_matches >= 2:
                mentions_article_content = True
                break
    
    # Check for follow-up questions about previous conversation
    follow_up_indicators = [
        'this', 'that', 'it', 'they', 'the designer', 'the article', 'the story',
        'more about', 'tell me more', 'continue', 'go on', 'expand on',
        'thought process', 'design process', 'approach', 'strategy', 'method',
        'this designer', 'that designer', 'their approach', 'their process',
        'their thinking', 'their strategy', 'their method', 'discuss it further',
        'talk more about', 'dive deeper', 'explore more', 'learn more', 'lets discuss',
        'discuss further', 'keep talking', 'continue discussing'
    ]
    
    # If user has recent conversation history, check if this looks like a follow-up
    has_follow_up = False
    if user_id and user_id in conversation_history:
        last_context = conversation_history[user_id].get('last_article_discussed')
        if last_context and any(indicator in message_lower for indicator in follow_up_indicators):
            has_follow_up = True
    
    return has_article_keyword or mentions_article_content or has_follow_up

def is_article_read_request(user_message):
    """Detect if user wants to read/summarize a full article - improved context awareness"""
    message_lower = user_message.lower()
    
    read_keywords = [
        'read the', 'read article', 'read full', 'read entire',
        'summarize the', 'summarize article', 'full article', 'entire article',
        'can you read', 'could you read', 'read and summarize',
        'what does the article say', 'what\'s in the article',
        'word summary', 'give me a summary'
    ]
    
    # Enhanced contextual read requests
    contextual_read_keywords = [
        'read it', 'summarize it', 'can you read it', 'could you read it',
        'read this', 'summarize this', 'can you summarize', 'could you summarize',
        'give me the full', 'show me the full', 'what does it say',
        'tell me what it says', 'break it down', 'explain it in detail',
        'dive into it', 'get the details', 'full details', 'complete summary'
    ]
    
    # Also check for summary requests with specific word counts
    import re
    if re.search(r'\d+\s*word\s*summary', message_lower):
        return True
    
    # Check standard read keywords
    if any(keyword in message_lower for keyword in read_keywords):
        return True
    
    # Check contextual read keywords (for when user refers to article as "it")
    if any(keyword in message_lower for keyword in contextual_read_keywords):
        return True
    
    # Check for "summarize" with contextual pronouns
    if 'summarize' in message_lower and any(pronoun in message_lower for pronoun in ['it', 'this', 'that', 'the article']):
        return True
    
    # Check for "read" with contextual pronouns
    if 'read' in message_lower and any(pronoun in message_lower for pronoun in ['it', 'this', 'that', 'the article']):
        return True
    
    return False

def create_article_suggestions(recent_articles, user_profile):
    """Create helpful article suggestions for users"""
    if not recent_articles:
        return "I don't have any recent articles to discuss right now. Try `/digest` to get your latest personalized news!"
    
    role = user_profile.get('primary_role', 'professional')
    
    suggestions = "Here are some articles from your recent digest you might want to discuss:\n\n"
    
    for i, article in enumerate(recent_articles[:5], 1):
        category_emoji = {
            "engineering": "⚙️",
            "design": "🎨", 
            "product": "📱",
            "business": "💼",
            "ai_ml": "🤖",
            "crypto": "₿"
        }.get(article.get("category", "general"), "📰")
        
        title = article['title']
        if len(title) > 60:
            title = title[:57] + "..."
        
        suggestions += f"{category_emoji} **Article {i}**: {title}\n"
    
    suggestions += f"\n💡 Just ask me things like:\n"
    suggestions += f"• \"Tell me more about article 1\"\n"
    suggestions += f"• \"What are your thoughts on the {recent_articles[0]['category']} article?\"\n"
    suggestions += f"• \"Read the full article about {recent_articles[0]['title'].split()[0]}\"\n"
    suggestions += f"• \"Summarize the entire article\"\n"
    
    return suggestions

def handle_article_question(user_id, user_message, recent_articles, user_profile, channel_id):
    """Handle questions specifically about articles - improved for better context handling"""
    try:
        # Get conversation context
        conversation_context = conversation_history.get(user_id, {})
        last_article_discussed = conversation_context.get('last_article_discussed')
        last_conversation = conversation_context.get('last_conversation', '')
        is_continuation = bool(conversation_context.get('last_conversation'))
        
        # Check if user is asking for a specific length summary
        message_lower = user_message.lower()
        summary_length_request = None
        if 'word summary' in message_lower or 'word summary' in message_lower:
            import re
            word_match = re.search(r'(\d+)\s*word\s*summary', message_lower)
            if word_match:
                summary_length_request = int(word_match.group(1))
        
        # Check if this is a read request for the article we're already discussing
        is_read_request = is_article_read_request(user_message)
        if is_read_request and last_article_discussed:
            print(f"🔍 Read request detected for ongoing conversation about: {last_article_discussed}")
            return handle_article_read_request(user_id, f"read {last_article_discussed}", recent_articles, user_profile, channel_id)
        
        # Build detailed articles context
        articles_context = build_detailed_articles_context(recent_articles)
        
        # Add conversation context to prompt
        conversation_context_text = ""
        if last_article_discussed and last_conversation:
            conversation_topic = conversation_context.get('conversation_topic', 'general discussion')
            last_user_message = conversation_context.get('last_user_message', '')
            
            conversation_context_text = f"""
        
        RECENT CONVERSATION CONTEXT:
        Last article discussed: {last_article_discussed}
        Previous conversation topic: {conversation_topic}
        User's last message: "{last_user_message}"
        Previous conversation: {last_conversation}
        
        CRITICAL: If the user asks follow-up questions like "let's discuss it further", "tell me more", 
        "dive deeper", "can you read it", "summarize", etc., they want to CONTINUE discussing the SAME article and topic from the previous conversation.
        
        DO NOT change topics or ask for clarification - continue the conversation naturally about the same article.
        """
        
        # Enhanced conversation context awareness  
        greeting_instruction = "- DO NOT start with greetings like 'Hey!' or 'Hi!' - this is a continuing conversation" if is_continuation else "- You can start with a brief greeting if appropriate, but keep it natural"
        
        # Special handling for summary requests
        if summary_length_request:
            length_instruction = f"- Provide a {summary_length_request}-word summary as requested"
        else:
            length_instruction = "- Keep responses concise but informative (2-4 sentences typically)"
        
        # Create article-focused prompt
        article_prompt = f"""
        You are PulseBot, a conversational AI assistant that helps users discuss news articles. The user has this profile:
        
        Role: {user_profile.get('primary_role', 'professional')}
        Industry: {user_profile.get('industry', 'technology')}
        Interests: {', '.join(user_profile.get('secondary_interests', []))}
        
        RECENT ARTICLES FROM THEIR DIGEST:
        {articles_context}
        {conversation_context_text}
        
        User's question: "{user_message}"
        
        INSTRUCTIONS:
        1. Identify which article they're asking about based on their question and conversation context
        2. If it's a follow-up question, continue the previous conversation naturally
        3. Provide insightful analysis about the specific article
        4. Connect to their role/interests when relevant
        5. Be conversational and natural - like chatting with a knowledgeable friend
        
        RESPONSE STYLE:
        - Be conversational and casual, not formal
        {greeting_instruction}
        - Use minimal emojis (0-1 max per response) and only when they feel natural
        {length_instruction}
        - Don't over-structure your response with bullet points or sections
        - Sound like you're having a normal conversation, not giving a presentation
        - Reference specific details from the articles naturally
        - Don't ask unnecessary follow-up questions unless genuinely needed
        - Be honest about limitations - you can only see article titles and summaries, not full content
        - Focus on providing specific insights rather than vague commentary
        - If they ask for a summary of an article you can't fully access, acknowledge this and offer to search for more information
        
        IMPORTANT: If they ask you to read/summarize a full article, let them know that you can actually read the full article content. Suggest they say something like "read the full article" or "summarize the entire article" to get the complete content.
        
        If you can't identify a specific article AND there's no conversation context, briefly ask for clarification with specific options.
        """
        
        # Check if this is a very vague article question
        vague_questions = [
            'article', 'articles', 'news', 'stories', 'what articles', 'any articles',
            'show me articles', 'list articles', 'what news', 'recent news'
        ]
        
        user_message_lower = user_message.lower().strip()
        if any(vague in user_message_lower for vague in vague_questions) and len(user_message_lower) < 20:
            # Provide article suggestions instead of AI response
            suggestions = create_article_suggestions(recent_articles, user_profile)
            slack_client.chat_postMessage(
                channel=channel_id,
                text=suggestions
            )
            return True
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are PulseBot, a conversational AI assistant who discusses technology and design news. You chat naturally like a knowledgeable friend, using minimal emojis and keeping things casual."},
                {"role": "user", "content": article_prompt}
            ],
            temperature=0.7,
            max_tokens=1200 if summary_length_request and summary_length_request > 300 else 800
        )
        
        bot_response = response.choices[0].message.content.strip()
        
        # Store conversation context for follow-up questions
        article_discussed = identify_article_from_question(user_message, recent_articles, last_article_discussed)
        if article_discussed:
            conversation_history[user_id] = {
                'last_article_discussed': article_discussed,
                'last_conversation': f"User asked: '{user_message}' - Bot responded about: {article_discussed}",
                'last_user_message': user_message,
                'conversation_topic': extract_conversation_topic(user_message, article_discussed),
                'timestamp': datetime.now().isoformat()
            }
        
        # Send response
        slack_client.chat_postMessage(
            channel=channel_id,
            text=bot_response
        )
        
        return True
        
    except Exception as e:
        print(f"Error in article question handling: {e}")
        return False

def handle_general_conversation(user_id, user_message, recent_articles, user_profile, channel_id):
    """Handle general conversation - now a full-featured AI assistant"""
    try:
        # Check if this might be a follow-up that should be handled as article question
        conversation_context = conversation_history.get(user_id, {})
        last_article_discussed = conversation_context.get('last_article_discussed')
        last_search = conversation_context.get('last_search')
        
        # Broad follow-up indicators that might have been missed
        broad_follow_up_indicators = [
            'discuss it further', 'talk more', 'dive deeper', 'explore more', 'learn more',
            'lets discuss', 'discuss further', 'keep talking', 'continue discussing',
            'more on this', 'elaborate', 'expand', 'go deeper'
        ]
        
        message_lower = user_message.lower()
        
        # If user has recent context and uses broad follow-up language, redirect to article handler
        if (last_article_discussed and 
            any(indicator in message_lower for indicator in broad_follow_up_indicators) and
            len(user_message.split()) <= 6):  # Short follow-up requests
            
            print(f"Redirecting '{user_message}' to article handler due to follow-up context")
            return handle_article_question(user_id, user_message, recent_articles, user_profile, channel_id)
        
        # Build comprehensive context
        context_parts = []
        
        # Add recent articles context
        if recent_articles:
            context_parts.append("Recent articles from their digest:\n" + "\n".join([
                f"- {article['title']}: {article['summary'][:100]}..."
                for article in recent_articles[:3]
            ]))
        
        # Add recent search context if available
        if last_search:
            context_parts.append(f"Recent search: '{last_search['query']}' with {len(last_search.get('results', []))} results")
        
        # Add conversation history if available
        if conversation_context.get('last_conversation'):
            context_parts.append(f"Recent conversation: {conversation_context['last_conversation']}")
        
        full_context = "\n\n".join(context_parts) if context_parts else ""
        
        # Enhanced conversation prompt - now handles ANY topic
        # Determine if this is a new conversation or continuation
        is_continuation = bool(conversation_context.get('last_conversation'))
        greeting_instruction = "- DO NOT start with greetings like 'Hey!' or 'Hi!' - this is a continuing conversation" if is_continuation else "- You can start with a brief greeting if appropriate, but keep it natural"
        
        conversation_prompt = f"""
        You are PulseBot, a helpful AI assistant chatting with a user who has this profile:
        
        Role: {user_profile.get('primary_role', 'professional')}
        Industry: {user_profile.get('industry', 'technology')}
        Experience: {user_profile.get('experience_level', 'mid')}
        Interests: {', '.join(user_profile.get('secondary_interests', []))}
        Company: {user_profile.get('company_stage', 'Unknown')}
        
        Context from recent interactions:
        {full_context}
        
        User message: "{user_message}"
        
        You are a knowledgeable AI assistant who can help with:
        - Design questions (UI/UX, design systems, tools like Figma, best practices)
        - Technology discussions (frameworks, programming, AI/ML, product development)
        - Industry trends and news analysis
        - Career advice and professional development
        - General questions about any topic
        - Creative problem-solving
        - Product strategy and business insights
        
        CONVERSATION STYLE:
        - Be casual and conversational, like chatting with a knowledgeable friend
        {greeting_instruction}
        - Use minimal emojis (0-1 max per response) and only when they feel natural
        - Keep responses concise but informative (2-5 sentences typically)
        - Sound natural, not robotic or overly formal
        - Reference their background/interests when relevant
        - Don't always ask follow-up questions - sometimes just share insights
        - Be helpful and informative while staying conversational
        - If they mention Groq, you can be enthusiastic since that's their company
        - If you need more information to answer well, suggest they search for it
        - Focus on providing specific, actionable information rather than vague responses
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are PulseBot, a helpful and knowledgeable AI assistant. You chat naturally and provide valuable insights while maintaining a casual, friendly tone."},
                {"role": "user", "content": conversation_prompt}
            ],
            temperature=0.8,
            max_tokens=600
        )
        
        bot_response = response.choices[0].message.content.strip()
        
        # Update conversation history
        conversation_history[user_id] = {
            'last_conversation': f"User asked: '{user_message}' - Bot provided general assistance",
            'last_user_message': user_message,
            'conversation_topic': 'general assistance',
            'timestamp': datetime.now().isoformat()
        }
        
        # Send response
        slack_client.chat_postMessage(
            channel=channel_id,
            text=bot_response
        )
        
        return True
        
    except Exception as e:
        print(f"Error in general conversation: {e}")
        return False

def build_detailed_articles_context(recent_articles):
    """Build detailed context about recent articles for article-specific questions"""
    if not recent_articles:
        return "No recent articles available."
    
    context = ""
    for i, article in enumerate(recent_articles[:5], 1):
        context += f"""
        ARTICLE {i}:
        Title: {article['title']}
        Source: {article['source']}
        Category: {article['category']}
        Summary: {article['summary']}
        Link: {article['link']}
        Published: {article['published']}
        
        """
    
    return context.strip()

def identify_article_from_question(user_message, recent_articles, last_article_discussed=None):
    """Identify which article the user is asking about - improved to handle search results"""
    message_lower = user_message.lower()
    
    print(f"🔍 Identifying article from: '{user_message}'")
    print(f"📝 Available articles: {[article['title'][:50] + '...' for article in recent_articles[:5]]}")
    print(f"🕐 Last discussed: {last_article_discussed}")
    
    # Check for specific article number references FIRST (higher priority)
    if 'article 1' in message_lower or 'first article' in message_lower or 'article number 1' in message_lower:
        if recent_articles:
            print(f"✅ Article 1 match: {recent_articles[0]['title']}")
            return recent_articles[0]['title']
    elif 'article 2' in message_lower or 'second article' in message_lower or 'article number 2' in message_lower:
        if len(recent_articles) > 1:
            print(f"✅ Article 2 match: {recent_articles[1]['title']}")
            return recent_articles[1]['title']
    elif 'article 3' in message_lower or 'third article' in message_lower or 'article number 3' in message_lower:
        if len(recent_articles) > 2:
            print(f"✅ Article 3 match: {recent_articles[2]['title']}")
            return recent_articles[2]['title']
    elif 'article 4' in message_lower or 'fourth article' in message_lower or 'article number 4' in message_lower:
        if len(recent_articles) > 3:
            print(f"✅ Article 4 match: {recent_articles[3]['title']}")
            return recent_articles[3]['title']
    elif 'article 5' in message_lower or 'fifth article' in message_lower or 'article number 5' in message_lower:
        if len(recent_articles) > 4:
            print(f"✅ Article 5 match: {recent_articles[4]['title']}")
            return recent_articles[4]['title']
    
    # Check for specific search result references
    if 'rgd' in message_lower and ('top 5' in message_lower or 'top5' in message_lower):
        for article in recent_articles:
            if 'rgd' in article['title'].lower() and 'top 5' in article['title'].lower():
                print(f"✅ RGD Top 5 match: {article['title']}")
                return article['title']
    
    # Check for design system related requests
    if 'design system' in message_lower:
        for article in recent_articles:
            if 'design system' in article['title'].lower():
                print(f"✅ Design system match: {article['title']}")
                return article['title']
    
    # Check for other specific keywords or partial matches
    search_terms = [
        ('builtin', 'built in'),
        ('designerup', 'designer up'),
        ('designrush', 'design rush'),
        ('designsystems.surf', 'design systems'),
        ('material design', 'material'),
        ('carbon design', 'carbon'),
        ('atlassian', 'atlassian')
    ]
    
    for term, alt_term in search_terms:
        if term in message_lower or alt_term in message_lower:
            for article in recent_articles:
                if term in article['title'].lower() or alt_term in article['title'].lower():
                    print(f"✅ Keyword match ({term}/{alt_term}): {article['title']}")
                    return article['title']
    
    # Check for follow-up indicators that suggest they're continuing previous conversation
    follow_up_indicators = [
        'this', 'that', 'it', 'they', 'the designer', 'the article', 'the story',
        'more about', 'tell me more', 'continue', 'go on', 'expand on',
        'thought process', 'design process', 'approach', 'strategy', 'method',
        'this designer', 'that designer', 'their approach', 'their process',
        'their thinking', 'their strategy', 'their method', 'discuss it further',
        'talk more about', 'dive deeper', 'explore more', 'learn more'
    ]
    
    # If it looks like a follow-up and we have previous context, use that
    if last_article_discussed and any(indicator in message_lower for indicator in follow_up_indicators):
        print(f"✅ Follow-up detected, using previous article: {last_article_discussed}")
        return last_article_discussed
    
    # Enhanced keyword matching for specific topics
    topic_keywords = {
        'devin': ['devin', 'cognition', 'windsurf', 'acquisition', 'acquire', 'ai ide'],
        'figma': ['figma', 'design tool', 'prototype'],
        'react': ['react', 'javascript', 'frontend', 'web development'],
        'ai': ['ai', 'artificial intelligence', 'machine learning', 'ml'],
        'design': ['design', 'designer', 'ui', 'ux', 'visual', 'graphic'],
        'korean': ['korean', 'air', 'airline', 'fly korean', 'campaign'],
        'indesign': ['indesign', 'brochure', 'typography', 'layout'],
        'qr_code': ['qr code', 'qr codes', 'qr', 'code', 'capital letters', 'lower-case', 'smaller'],
        'junior_developer': ['junior developer', 'junior', 'developer', 'extinction', 'programming', 'dark age'],
        'regex': ['regex', 'regular expressions', 'javascript', 'linear matching', 'optimization'],
        'framework': ['framework', 'language framework', 'self maintained', 'maintained'],
        'mercedes': ['mercedes', 'mercedes-benz', 'cla', 'shooting brake', 'electric', 'estate car']
    }
    
    # Check for topic-specific matches
    for topic, keywords in topic_keywords.items():
        if any(keyword in message_lower for keyword in keywords):
            print(f"🎯 Topic match found: '{topic}' (keywords: {keywords})")
            for article in recent_articles[:10]:  # Check more articles
                article_lower = article['title'].lower()
                article_summary = article.get('summary', '').lower()
                
                # Check if article contains topic keywords
                if any(keyword in article_lower or keyword in article_summary for keyword in keywords):
                    print(f"✅ Article match: '{article['title']}' matches topic '{topic}'")
                    return article['title']
    
    # Check for article title matches (original logic)
    best_match = None
    best_score = 0
    
    for article in recent_articles[:10]:  # Check more articles
        title_words = article['title'].lower().split()
        summary_words = article.get('summary', '').lower().split()
        
        # Count matching words (excluding common words)
        common_words = ['the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'a', 'an', 'is', 'are', 'was', 'were']
        significant_words = [word for word in title_words + summary_words if len(word) > 3 and word not in common_words]
        
        matches = sum(1 for word in significant_words if word in message_lower)
        
        if matches > best_score and matches >= 1:  # Lower threshold for better matching
            best_score = matches
            best_match = article['title']
    
    print(f"🎯 Final result: {best_match if best_match else 'No match found'}")
    return best_match

def extract_conversation_topic(user_message, article_title):
    """Extract the specific topic/aspect being discussed about an article"""
    message_lower = user_message.lower()
    
    # Common topics people ask about
    if any(word in message_lower for word in ['acquisition', 'acquire', 'buy', 'purchase', 'deal']):
        return 'acquisition details'
    elif any(word in message_lower for word in ['designer', 'design process', 'thought process', 'approach', 'method']):
        return 'design process'
    elif any(word in message_lower for word in ['technical', 'technology', 'how it works', 'implementation']):
        return 'technical details'
    elif any(word in message_lower for word in ['impact', 'implications', 'affects', 'industry']):
        return 'industry impact'
    elif any(word in message_lower for word in ['opinion', 'thoughts', 'what do you think']):
        return 'analysis and opinion'
    elif any(word in message_lower for word in ['future', 'what happens next', 'predictions']):
        return 'future implications'
    else:
        return 'general discussion'

def cleanup_old_conversation_history():
    """Clean up conversation history older than 24 hours"""
    try:
        cutoff_time = datetime.now() - timedelta(hours=24)
        users_to_remove = []
        
        for user_id, context in conversation_history.items():
            if 'timestamp' in context:
                context_time = datetime.fromisoformat(context['timestamp'])
                if context_time < cutoff_time:
                    users_to_remove.append(user_id)
        
        for user_id in users_to_remove:
            del conversation_history[user_id]
            
        print(f"Cleaned up conversation history for {len(users_to_remove)} users")
    except Exception as e:
        print(f"Error cleaning up conversation history: {e}")

def should_respond_to_message(text):
    """Enhanced logic to determine if we should respond to a message - now more inclusive"""
    text_lower = text.strip().lower()
    
    # Skip very short messages (but be more lenient)
    if len(text_lower) <= 1:
        return False
    
    # Skip common acknowledgments (but be more selective)
    skip_phrases = [
        'thanks', 'thank you', 'ok', 'okay', 'cool', 'nice', 'good', 'great', 
        'awesome', 'got it', 'sure', 'yep', 'yes', 'no'
    ]
    
    # Only skip if it's exactly one of these phrases
    if text_lower in skip_phrases:
        return False
    
    # Skip commands that aren't ours
    if text_lower.startswith('!') or text_lower.startswith('/'):
        return False
    
    # Strong indicators for conversation (expanded)
    strong_triggers = [
        'what', 'how', 'why', 'when', 'where', 'tell me', 'explain', 'thoughts', 
        'think', 'opinion', 'should i', 'can you', 'more about', 'details', 
        'summary', 'article', 'story', 'news', 'read about', 'link',
        'search', 'find', 'look up', 'help', 'show me', 'get me', 'i need'
    ]
    
    # Questions (ending with ?)
    if text.endswith('?'):
        return True
    
    # Contains strong conversation triggers
    if any(trigger in text_lower for trigger in strong_triggers):
        return True
    
    # Professional/technical terms that indicate they want to discuss
    professional_terms = [
        'design', 'ui', 'ux', 'figma', 'prototype', 'user experience', 'interface',
        'programming', 'code', 'developer', 'framework', 'api', 'javascript', 'python',
        'ai', 'machine learning', 'algorithm', 'startup', 'product', 'feature',
        'groq', 'technology', 'development', 'software', 'app', 'platform'
    ]
    
    if any(term in text_lower for term in professional_terms) and len(text_lower) > 5:
        return True
    
    # Greetings should get a response
    greetings = ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']
    if any(greeting in text_lower for greeting in greetings):
        return True
    
    # Longer messages that might be conversational (lowered threshold)
    if len(text_lower) > 15:
        return True
    
    # Default to responding - we want to be helpful
    return len(text_lower) > 3

def format_slack_message(digest, articles, user_profile):
    """Format the digest for Slack with proper length limits"""
    role = user_profile.get("primary_role", "professional")
    
    # Ensure digest isn't too long
    if len(digest) > 2500:
        digest = digest[:2400] + "..."
    
    message_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🌅 Your Daily Digest - {datetime.now().strftime('%B %d')}"
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Curated for: {role.title()} | {len(articles)} articles"
                }
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": digest
            }
        },
        {
            "type": "divider"
        }
    ]
    
    # Add top 5 article links only
    for i, article in enumerate(articles[:5]):
        category_emoji = {
            "engineering": "⚙️",
            "design": "🎨", 
            "product": "📱",
            "business": "💼",
            "ai_ml": "🤖",
            "crypto": "₿"
        }.get(article.get("category", "general"), "📰")
        
        # Truncate title if too long
        title = article['title']
        if len(title) > 60:
            title = title[:57] + "..."
        
        message_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{category_emoji} <{article['link']}|{title}>"
            }
        })
    
    # Add conversation prompt with article question examples
    message_blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "💬 *Ask me about any articles!* Try: \"Tell me more about the Figma article\", \"What are your thoughts on the AI story?\", or \"Read the full article\" to get the complete content."
            }
        ]
    })
    
    message_blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "🔄 `/digest` for new articles | 📚 `/articles` to see all articles | 🔍 `/search [query]` to search the web | 🧠 `/context` to see conversation history | ⚙️ `/preferences` to update your profile"
            }
        ]
    })
    
    return message_blocks



def get_conversation_starters(user_profile):
    """Generate conversation starters based on user profile"""
    role = user_profile.get('primary_role', 'professional')
    
    starters = {
        'design': [
            "What design trends are you excited about this year? 🎨",
            "Have you tried any new design tools lately?",
            "What's the biggest UX challenge you're working on?",
            "Any interesting design patterns you've discovered recently?",
            "How's the design system work going at Groq?"
        ],
        'engineering': [
            "What's your favorite framework to work with right now?",
            "Any interesting technical challenges you're solving?",
            "Have you experimented with any new dev tools?",
            "What's your take on the latest AI development tools?"
        ],
        'business': [
            "What market trends are you keeping an eye on?",
            "Any interesting startup stories caught your attention?",
            "How's the business side of tech evolving?"
        ]
    }
    
    return starters.get(role, starters['design'])

def send_simple_digest(digest, articles, user_profile, channel_id):
    """Send a simple text-only digest if blocks fail"""
    try:
        role = user_profile.get("primary_role", "professional")
        
        # Create simple text message
        message = f"🌅 *Your Daily Digest - {datetime.now().strftime('%B %d')}*\n"
        message += f"_Curated for: {role.title()}_\n\n"
        message += digest + "\n\n"
        message += "*📚 Top Articles:*\n"
        
        for i, article in enumerate(articles[:5]):
            category_emoji = {
                "engineering": "⚙️",
                "design": "🎨", 
                "product": "📱",
                "business": "💼",
                "ai_ml": "🤖",
                "crypto": "₿"
            }.get(article.get("category", "general"), "📰")
            
            title = article['title']
            if len(title) > 60:
                title = title[:57] + "..."
            
            message += f"{category_emoji} <{article['link']}|{title}>\n"
        
        message += "\n💬 *Ask me about any articles!* Try: \"Tell me more about the Figma article\", \"What are your thoughts on the AI story?\", or \"Read the full article\" to get the complete content."
        message += "\n🔄 `/digest` for new articles | 📚 `/articles` to see all articles | 🧠 `/context` to see conversation history | ⚙️ `/preferences` to update your profile"
        
        response = slack_client.chat_postMessage(
            channel=channel_id,
            text=message
        )
        return True
        
    except Exception as e:
        print(f"Error sending simple digest: {e}")
        return False


def send_digest_to_user(user_id, channel_id=None):
    """Send personalized digest to a user with enhanced freshness tracking"""
    try:
        # Check if user has a profile
        if user_id not in user_profiles:
            return send_onboarding_message(user_id, channel_id)
        
        user_profile = user_profiles[user_id].copy()
        user_profile['user_id'] = user_id  # Add user_id for tracking
        
        # Show tracking info in debug
        shown_count = len(shown_articles.get(user_id, set()))
        print(f"User {user_id} has {shown_count} previously shown articles tracked")
        
        # Fetch personalized news with tracking
        articles = fetch_personalized_news(user_profile, limit=15)
        if not articles:
            return False
        
        # Store articles for conversation context
        recent_articles[user_id] = articles
            
        # Generate AI digest
        digest = personalized_summarize_with_groq(articles, user_profile)
        if not digest:
            digest = f"Here are today's top stories curated for your role as a {user_profile.get('primary_role', 'professional')}:"
        
        # Try to send with blocks first
        try:
            message_blocks = format_slack_message(digest, articles, user_profile)
            
            target_channel = channel_id if channel_id else user_id
            response = slack_client.chat_postMessage(
                channel=target_channel,
                blocks=message_blocks,
                text=f"Daily personalized digest with {len(articles)} fresh articles"
            )
            
            print(f"✅ Sent digest with {len(articles)} articles to user {user_id}")
            return True
            
        except SlackApiError as e:
            print(f"Blocks failed: {e}. Trying simple text...")
            # Fallback to simple text message   
            target_channel = channel_id if channel_id else user_id
            return send_simple_digest(digest, articles, user_profile, target_channel)
        
    except Exception as e:
        print(f"Error sending digest: {e}")
        return False

def send_onboarding_message(user_id, channel_id=None):
    """Send onboarding message to new users"""
    try:
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "👋 Welcome to PulseBot!"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "I'm here to deliver personalized industry news that matters to you! To get started, I need to learn about you."
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Tell me about yourself:*\n• What's your role/job title?\n• What industry do you work in?\n• What technologies or topics interest you?\n• What stage company do you work at?\n\nJust reply with a message describing yourself - I'll use AI to create your personalized profile!"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "💡 Example: _'I'm a senior software engineer at a startup, focused on machine learning and Python. I'm interested in AI trends, new frameworks, and startup news.'_"
                    }
                ]
            }
        ]
        
        # Mark user as in onboarding
        user_onboarding_state[user_id] = {"state": "awaiting_profile"}
        
        target_channel = channel_id if channel_id else user_id
        response = slack_client.chat_postMessage(
            channel=target_channel,
            blocks=blocks,
            text="Welcome to PulseBot! Tell me about yourself to get started."
        )
        
        return True
        
    except Exception as e:
        print(f"Error sending onboarding message: {e}")
        return False

def process_user_profile_input(user_id, user_input, channel_id=None):
    """Process user's profile description and create their profile"""
    print(f"=== CREATE PROFILE DEBUG ===")
    print(f"User ID: {user_id}")
    print(f"Input: {user_input}")
    print(f"Channel: {channel_id}")
    
    try:
        # Create profile using AI
        print("Calling create_user_profile...")
        profile = create_user_profile(user_input)
        print(f"Created profile: {profile}")
        
        if profile:
            # Save profile
            user_profiles[user_id] = profile
            user_onboarding_state.pop(user_id, None)  # Remove from onboarding
            print(f"Profile saved for user {user_id}")
            
            # Send confirmation
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "✅ Profile Created!"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Here's what I learned about you:*\n• **Role:** {profile.get('primary_role', 'N/A')}\n• **Industry:** {profile.get('industry', 'N/A')}\n• **Experience:** {profile.get('experience_level', 'N/A')}\n• **Interests:** {', '.join(profile.get('secondary_interests', []))}\n\n_{profile.get('summary', 'Profile created successfully!')}_"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🚀 You're all set! You'll receive personalized news digests every morning at 9 AM. Try `/digest` now to see your first personalized digest!"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "💡 You can chat with me about any articles, see all your articles with `/articles`, check conversation history with `/context`, update your profile with `/preferences`, or get a fresh digest anytime with `/digest`."
                        }
                    ]
                }
            ]
            
            target_channel = channel_id if channel_id else user_id
            print(f"Sending confirmation to {target_channel}")
            
            response = slack_client.chat_postMessage(
                channel=target_channel,
                blocks=blocks,
                text="Profile created successfully!"
            )
            print(f"Slack response: {response}")
            
            return True
        else:
            # Error creating profile
            print("Profile creation returned None")
            target_channel = channel_id if channel_id else user_id
            slack_client.chat_postMessage(
                channel=target_channel,
                text="❌ Sorry, I had trouble understanding your description. Could you try describing yourself again with more details about your role and interests?"
            )
            return False
            
    except Exception as e:
        print(f"Error processing profile input: {e}")
        import traceback
        traceback.print_exc()
        
        # Send error message to user
        try:
            target_channel = channel_id if channel_id else user_id
            slack_client.chat_postMessage(
                channel=target_channel,
                text="❌ Sorry, there was an error processing your profile. Please try again."
            )
        except:
            pass
        
        return False

# Find this line in your app.py and REPLACE the entire @app.route('/test') function:

@app.route('/test', methods=['GET', 'POST'])
def test():
    """Debug endpoint to see what Slack is sending"""
    print("=== TEST ENDPOINT HIT ===")
    print(f"Method: {request.method}")
    print(f"Content-Type: {request.content_type}")
    print(f"Headers: {dict(request.headers)}")
    
    if request.method == 'POST':
        if request.content_type and 'application/json' in request.content_type:
            data = request.get_json()
            print(f"JSON Data: {data}")
        else:
            data = request.form.to_dict()
            print(f"Form Data: {data}")
            
            # If this is a slash command, handle it here as a workaround
            if 'command' in data:
                print("*** SLASH COMMAND DETECTED IN TEST ENDPOINT ***")
                print("*** THIS SHOULD BE GOING TO /slack/events ***")
                
                command = data['command']
                user_id = data['user_id']
                channel_id = data['channel_id']
                text = data.get('text', '')
                
                if command == '/preferences':
                    if user_id in user_profiles:
                        profile = user_profiles[user_id]
                        if text.strip():
                            # Update profile
                            try:
                                new_profile = create_user_profile(text)
                                if new_profile:
                                    user_profiles[user_id] = new_profile
                                    return jsonify({
                                        'response_type': 'ephemeral',
                                        'text': f'✅ Profile updated!\n• **Role:** {new_profile.get("primary_role", "N/A")}\n• **Industry:** {new_profile.get("industry", "N/A")}\n• **Interests:** {", ".join(new_profile.get("secondary_interests", []))}'
                                    })
                                else:
                                    return jsonify({
                                        'response_type': 'ephemeral',
                                        'text': '❌ Error updating profile. Please try again.'
                                    })
                            except Exception as e:
                                print(f"Error updating profile: {e}")
                                return jsonify({
                                    'response_type': 'ephemeral',
                                    'text': '❌ Error updating profile. Please try again.'
                                })
                        else:
                            # Show current profile
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': f'**Current Profile:**\n• **Role:** {profile.get("primary_role", "N/A")}\n• **Industry:** {profile.get("industry", "N/A")}\n• **Experience:** {profile.get("experience_level", "N/A")}\n• **Interests:** {", ".join(profile.get("secondary_interests", []))}\n\nTo update: `/preferences [describe yourself again]`'
                            })
                    else:
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '❌ No profile found. Use `/digest` to get started!'
                        })
                
                elif command == '/digest':
                    if user_id in user_profiles:
                        try:
                            success = send_digest_to_user(user_id, channel_id)
                            if success:
                                return jsonify({
                                    'response_type': 'in_channel',
                                    'text': '✅ Your personalized digest has been sent!'
                                })
                            else:
                                return jsonify({
                                    'response_type': 'ephemeral',
                                    'text': '❌ Error generating digest. Please try again.'
                                })
                        except Exception as e:
                            print(f"Error sending digest: {e}")
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': '❌ Error generating digest. Please try again.'
                            })
                    else:
                        send_onboarding_message(user_id, channel_id)
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '👋 Welcome! Setting up your profile...'
                        })
    
    return jsonify({
        "status": "working", 
        "method": request.method,
        "note": "This endpoint should not be receiving Slack commands but is handling them as a workaround"
    })

@app.route('/slack/events', methods=['POST'])
def handle_slack_events():
    """Handle Slack events and slash commands"""
    
    try:
        # Handle different content types from Slack
        if request.content_type and 'application/json' in request.content_type:
            data = request.get_json()
        else:
            # Slash commands come as form data
            data = request.form.to_dict()
        
        print(f"=== INCOMING SLACK EVENT ===")
        print(f"Content-Type: {request.content_type}")
        print(f"Data: {data}")
        print("============================")
        
        # Handle URL verification (JSON)
        if data.get('type') == 'url_verification':
            return jsonify({'challenge': data.get('challenge')})
        
        # Handle slash commands (form data)
        if 'command' in data:
            command = data['command']
            user_id = data['user_id']
            channel_id = data['channel_id']
            text = data.get('text', '')
            
            print(f"=== SLASH COMMAND ===")
            print(f"Command: {command}")
            print(f"User: {user_id}")
            print(f"Channel: {channel_id}")
            print("====================")
            
            if command == '/digest':
                try:
                    # Send immediate response to Slack
                    if user_id in user_profiles:
                        # Use threading to send digest asynchronously
                        def send_async_digest():
                            try:
                                success = send_digest_to_user(user_id, channel_id)
                                if not success:
                                    slack_client.chat_postMessage(
                                        channel=channel_id,
                                        text='❌ Sorry, there was an error generating your digest. Please try again.'
                                    )
                            except Exception as e:
                                print(f"Error in async digest: {e}")
                                slack_client.chat_postMessage(
                                    channel=channel_id,
                                    text='❌ Sorry, there was an error generating your digest.'
                                )
                        
                        # Start thread and return immediate response
                        thread = threading.Thread(target=send_async_digest)
                        thread.start()
                        
                        return jsonify({
                            'response_type': 'in_channel',
                            'text': '🚀 Generating your personalized digest...'
                        })
                    else:
                        # Start onboarding in a thread
                        def start_async_onboarding():
                            send_onboarding_message(user_id, channel_id)
                        
                        thread = threading.Thread(target=start_async_onboarding)
                        thread.start()
                        
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '👋 Welcome! Setting up your profile...'
                        })
                        
                except Exception as e:
                    print(f"Error in /digest command: {e}")
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': '❌ Sorry, there was an error. Please try again.'
                    })
            
            elif command == '/preferences':
                try:
                    # Show current profile or help update it
                    if user_id in user_profiles:
                        profile = user_profiles[user_id]
                        if text.strip():
                            # Update profile with new description
                            def update_async_profile():
                                new_profile = create_user_profile(text)
                                if new_profile:
                                    user_profiles[user_id] = new_profile
                                    slack_client.chat_postMessage(
                                        channel=channel_id,
                                        text=f'✅ Profile updated!\n• **Role:** {new_profile.get("primary_role", "N/A")}\n• **Industry:** {new_profile.get("industry", "N/A")}\n• **Interests:** {", ".join(new_profile.get("secondary_interests", []))}'
                                    )
                                else:
                                    slack_client.chat_postMessage(
                                        channel=channel_id,
                                        text='❌ Sorry, there was an error updating your profile. Please try again.'
                                    )
                            
                            thread = threading.Thread(target=update_async_profile)
                            thread.start()
                            
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': '🔄 Updating your profile...'
                            })
                        else:
                            # Show current profile
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': f'**Current Profile:**\n• **Role:** {profile.get("primary_role", "N/A")}\n• **Industry:** {profile.get("industry", "N/A")}\n• **Experience:** {profile.get("experience_level", "N/A")}\n• **Interests:** {", ".join(profile.get("secondary_interests", []))}\n\nTo update: `/preferences [describe yourself again]`'
                            })
                    else:
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '❌ No profile found. Use `/digest` to get started!'
                        })
                except Exception as e:
                    print(f"Error in /preferences command: {e}")
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': '❌ Sorry, there was an error. Please try again.'
                    })
            
            elif command == '/articles':
                try:
                    if user_id in user_profiles:
                        user_profile = user_profiles[user_id]
                        user_articles = recent_articles.get(user_id, [])
                        
                        if user_articles:
                            suggestions = create_article_suggestions(user_articles, user_profile)
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': suggestions
                            })
                        else:
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': "You don't have any recent articles yet. Use `/digest` to get your personalized news!"
                            })
                    else:
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '❌ No profile found. Use `/digest` to get started!'
                        })
                except Exception as e:
                    print(f"Error in /articles command: {e}")
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': '❌ Sorry, there was an error. Please try again.'
                    })
            
            elif command == '/search':
                try:
                    if user_id in user_profiles:
                        user_profile = user_profiles[user_id]
                        
                        if text.strip():
                            # Perform search in background
                            def search_async():
                                handle_search_request(user_id, text.strip(), user_profile, channel_id)
                            
                            thread = threading.Thread(target=search_async)
                            thread.start()
                            
                            return jsonify({
                                'response_type': 'in_channel',
                                'text': f'🔍 Searching for: {text.strip()}...'
                            })
                        else:
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': '❌ Please provide a search query. Example: `/search design systems`'
                            })
                    else:
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '❌ No profile found. Use `/digest` to get started!'
                        })
                except Exception as e:
                    print(f"Error in /search command: {e}")
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': '❌ Sorry, there was an error. Please try again.'
                    })
            
            elif command == '/context':
                try:
                    if user_id in user_profiles:
                        user_context = conversation_history.get(user_id, {})
                        user_articles = recent_articles.get(user_id, [])
                        
                        if user_context:
                            last_article = user_context.get('last_article_discussed', 'None')
                            last_topic = user_context.get('conversation_topic', 'None')
                            last_message = user_context.get('last_user_message', 'None')
                            timestamp = user_context.get('timestamp', 'None')
                            
                            context_text = f"""**Current Conversation Context:**
• **Last Article Discussed:** {last_article}
• **Topic:** {last_topic}
• **Your Last Message:** "{last_message}"
• **Time:** {timestamp}

**Available Articles:** {len(user_articles)}

💡 You can now ask follow-up questions like:
• "let's discuss it further"
• "tell me more about that"
• "dive deeper"
• "what are the implications?"
"""
                        else:
                            context_text = "No conversation context yet. Start by asking about an article!"
                            
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': context_text
                        })
                    else:
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '❌ No profile found. Use `/digest` to get started!'
                        })
                except Exception as e:
                    print(f"Error in /context command: {e}")
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': '❌ Sorry, there was an error. Please try again.'
                    })
            
            elif command == '/help':
                help_text = """
🤖 **PulseBot - Your AI Assistant**

**Daily News:**
• `/digest` - Get personalized news digest
• `/articles` - View your recent articles
• `/preferences` - Update your profile
• `/refresh` - Reset article history for completely fresh content

**Web Search:**
• `/search [query]` - Search the web for any topic
• Just ask: "find me resources on design systems"

**Article Reading:**
• "Read the full article" - Get complete article content
• "Tell me more about article 2" - Discuss specific articles
• "What are your thoughts on [topic]?" - Get AI analysis

**General Chat:**
• Ask questions about design, tech, or anything
• Get help with problems or projects
• Discuss industry trends and insights

**Context:**
• `/context` - See conversation history
• I remember what we've discussed and can continue conversations

**Examples:**
• "What's the best way to build a design system?"
• "Find me articles about React performance"
• "Read the full article about AI trends"
• "Help me understand this design pattern"

**Tip:** If you're seeing the same articles repeatedly, use `/refresh` to reset your history!

Just chat naturally - I'm here to help! 💬
"""
                return jsonify({
                    'response_type': 'ephemeral',
                    'text': help_text
                })
            
            elif command == '/refresh':
                try:
                    if user_id in user_profiles:
                        # Clear shown articles for this user
                        cleared = clear_shown_articles(user_id)
                        if cleared:
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': '🔄 Cleared your article history! Your next `/digest` will show completely fresh content.'
                            })
                        else:
                            return jsonify({
                                'response_type': 'ephemeral',
                                'text': '✅ Article history was already clear. Your next `/digest` will show fresh content.'
                            })
                    else:
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': '❌ No profile found. Use `/digest` to get started!'
                        })
                except Exception as e:
                    print(f"Error in /refresh command: {e}")
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': '❌ Sorry, there was an error. Please try again.'
                    })
            
            elif command == '/help':
                help_text = """
🤖 **PulseBot - Your AI Assistant**

**Daily News:**
• `/digest` - Get personalized news digest
• `/articles` - View your recent articles
• `/preferences` - Update your profile
• `/refresh` - Reset article history for completely fresh content

**Web Search:**
• `/search [query]` - Search the web for any topic
• Just ask: "find me resources on design systems"

**Article Reading:**
• "Read the full article" - Get complete article content
• "Tell me more about article 2" - Discuss specific articles
• "What are your thoughts on [topic]?" - Get AI analysis

**General Chat:**
• Ask questions about design, tech, or anything
• Get help with problems or projects
• Discuss industry trends and insights

**Context:**
• `/context` - See conversation history
• I remember what we've discussed and can continue conversations

**Examples:**
• "What's the best way to build a design system?"
• "Find me articles about React performance"
• "Read the full article about AI trends"
• "Help me understand this design pattern"

**Tip:** If you're seeing the same articles repeatedly, use `/refresh` to reset your history!

Just chat naturally - I'm here to help! 💬
"""
                return jsonify({
                    'response_type': 'ephemeral',
                    'text': help_text
                })
        
        # Handle app mentions and direct messages
        if data.get('type') == 'event_callback':
            event = data.get('event', {})
            print(f"=== EVENT CALLBACK ===")
            print(f"Event type: {event.get('type')}")
            
            if event.get('type') == 'message' and 'subtype' not in event:
                user_id = event.get('user')
                text = event.get('text', '')
                channel = event.get('channel')
                
                # Skip bot messages
                if user_id == data.get('authorizations', [{}])[0].get('user_id'):
                    return jsonify({'status': 'ok'})
                
                # Check if user is in onboarding
                if user_id in user_onboarding_state:
                    def process_async_profile():
                        process_user_profile_input(user_id, text, channel)
                    
                    thread = threading.Thread(target=process_async_profile)
                    thread.start()
                    
                elif user_id in user_profiles:
                    # Enhanced conversation detection - more responsive to article questions
                    should_respond = should_respond_to_message(text)
                    
                    if should_respond:
                        def handle_async_conversation():
                            handle_conversation(user_id, text, channel)
                        
                        thread = threading.Thread(target=handle_async_conversation)
                        thread.start()
                else:
                    def send_async_onboarding():
                        send_onboarding_message(user_id, channel)
                    
                    thread = threading.Thread(target=send_async_onboarding)
                    thread.start()
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        print(f"Error in handle_slack_events: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

def daily_digest_job():
    """Job to send daily digests to all users with profiles"""
    print(f"Running daily digest job at {datetime.now()}")
    
    # Clean up old conversation history
    cleanup_old_conversation_history()
    
    # Clean up old shown articles
    cleanup_old_shown_articles()
    
    for user_id, profile in user_profiles.items():
        try:
            send_digest_to_user(user_id)
            time.sleep(1)  # Rate limiting
        except Exception as e:
            print(f"Error sending digest to {user_id}: {e}")

# Set up scheduler for daily digests
scheduler = BackgroundScheduler()
scheduler.add_job(
    func=daily_digest_job,
    trigger="cron",
    hour=9,  # 9 AM daily
    minute=0,
    id='daily_digest'
)


@app.route('/debug')
def debug():
    return jsonify({
        "user_onboarding_state": user_onboarding_state,
        "user_profiles": list(user_profiles.keys()),
        "total_users": len(user_profiles),
        "recent_articles": {user_id: len(articles) for user_id, articles in recent_articles.items()},
        "conversation_history": {user_id: context.get('last_article_discussed', 'None') for user_id, context in conversation_history.items()},
        "shown_articles": {user_id: len(articles) for user_id, articles in shown_articles.items()},
        "freshness_stats": {user_id: get_article_freshness_stats(user_id) for user_id in user_profiles.keys()}
    })

@app.route('/health')
def health():
    return "PulseBot is running!"

@app.route('/debug-news/<user_id>')
def debug_news_for_user(user_id):
    """Debug endpoint to test news fetching for a specific user"""
    if user_id not in user_profiles:
        return jsonify({"error": "User not found"})
    
    user_profile = user_profiles[user_id]
    
    # Run debug
    debug_news_fetching(user_profile)
    
    # Fetch articles
    articles = fetch_personalized_news(user_profile, limit=10)
    
    return jsonify({
        "user_profile": user_profile,
        "articles_found": len(articles),
        "articles": [
            {
                "title": article["title"],
                "category": article["category"],
                "source": article["source"],
                "relevance_score": calculate_article_relevance_score(article, user_profile)
            }
            for article in articles
        ]
    })

@app.route('/test-conversation/<user_id>')
def test_conversation_for_user(user_id):
    """Test endpoint to see how conversation detection works"""
    if user_id not in user_profiles:
        return jsonify({"error": "User not found"})
    
    user_profile = user_profiles[user_id]
    user_articles = recent_articles.get(user_id, [])
    
    # Test different message types
    test_messages = [
        "What about the AI article?",
        "Tell me more about the first article",
        "articles",
        "What's your opinion on the design story?",
        "Explain the Figma article",
        "thanks",
        "How does React work?",
        "What articles do you have?",
        "id love to know more about this designers thought process",
        "tell me more about that",
        "what about this article?",
        "continue",
        "tell me more about devin acquiring windsurf",
        "lets discuss it further",
        "dive deeper",
        "explore more about the acquisition"
    ]
    
    # Set up test conversation context
    if user_articles:
        conversation_history[user_id] = {
            'last_article_discussed': user_articles[0]['title'],
            'last_conversation': "User asked about the first article",
            'timestamp': datetime.now().isoformat()
        }
    
    results = []
    for msg in test_messages:
        is_article_question = detect_article_question(msg, user_articles, user_id)
        should_respond = should_respond_to_message(msg)
        identified_article = identify_article_from_question(msg, user_articles, conversation_history.get(user_id, {}).get('last_article_discussed'))
        
        results.append({
            "message": msg,
            "is_article_question": is_article_question,
            "should_respond": should_respond,
            "identified_article": identified_article,
            "handler": "article_question" if is_article_question else ("general_conversation" if should_respond else "no_response")
        })
    
    return jsonify({
        "user_profile": user_profile,
        "recent_articles_count": len(user_articles),
        "article_titles": [article["title"] for article in user_articles[:3]],
        "conversation_context": conversation_history.get(user_id, {}),
        "test_results": results,
        "article_suggestions": create_article_suggestions(user_articles, user_profile)
    })

def fetch_reddit_varied(role, interests, limit=20):
    """Fetch from Reddit with comprehensive variation strategies"""
    try:
        # Enhanced subreddit selection by role with much more design focus
        role_subreddits = {
            'design': [
                # Core design communities
                'design', 'userexperience', 'web_design', 'graphic_design', 'UI_Design', 'productdesign',
                # Design inspiration and showcases
                'DesignPorn', 'typography', 'minimalism', 'logodesign', 'identitydesign', 'branddesign',
                # Tool-specific communities
                'figma', 'adobe', 'photoshop', 'illustrator', 'AdobeXD', 'sketch',
                # UX/UI specific
                'userexperience', 'UXDesign', 'UXResearch', 'userinterface', 'InteractionDesign',
                # Design systems and frontend
                'designsystems', 'webdev', 'Frontend', 'css', 'webdesign', 'mobiledesign',
                # Creative and visual
                'graphic_design', 'visualdesign', 'art', 'creativity', 'design_critiques',
                # Modern design trends
                'MaterialDesign', 'DarkMode', 'accessibility', 'responsive', 'animation'
            ],
            'engineering': ['programming', 'webdev', 'javascript', 'python', 'reactjs', 'MachineLearning', 'coding', 'softwareengineering', 'Frontend', 'Backend'],
            'product': ['product_management', 'startups', 'entrepreneur', 'productivity', 'SaaS', 'products', 'business'],
            'business': ['startups', 'entrepreneur', 'business', 'investing', 'marketing', 'sales', 'freelance'],
            'ai_ml': ['MachineLearning', 'artificial', 'deeplearning', 'ChatGPT', 'OpenAI', 'datascience', 'AI'],
            'general': ['technology', 'programming', 'startups', 'TechNews', 'gadgets']
        }
        
        subreddits = role_subreddits.get(role, role_subreddits['general'])
        all_articles = []
        
        # For design roles, prioritize design-heavy subreddits
        if role == 'design':
            # Priority design subreddits (higher chance of selection)
            priority_design_subreddits = [
                'design', 'userexperience', 'UI_Design', 'productdesign', 'DesignPorn', 
                'typography', 'figma', 'UXDesign', 'webdesign', 'graphic_design'
            ]
            
            # Ensure we always get some priority design subreddits
            priority_selection = random.sample(priority_design_subreddits, min(4, len(priority_design_subreddits)))
            
            # Add some variety from the full list
            remaining_subreddits = [s for s in subreddits if s not in priority_selection]
            variety_selection = random.sample(remaining_subreddits, min(3, len(remaining_subreddits)))
            
            selected_subreddits = priority_selection + variety_selection
        else:
            # For other roles, use original logic
            selected_subreddits = random.sample(subreddits, min(6, len(subreddits)))
        
        # Multiple sort types and time periods for variety
        sort_configs = [
            ('hot', None),
            ('top', 'day'),
            ('top', 'week'),
            ('new', None),
            ('rising', None)
        ]
        
        for subreddit in selected_subreddits:
            # Randomly select sort type for this subreddit
            sort_type, time_filter = random.choice(sort_configs)
            
            try:
                # Build URL with time filter if needed
                url = f"https://www.reddit.com/r/{subreddit}/{sort_type}.json?limit=10"
                if time_filter:
                    url += f"&t={time_filter}"
                
                headers = {'User-Agent': 'PulseBot/1.0'}
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()
                
                subreddit_articles = []
                for post in data['data']['children']:
                    post_data = post['data']
                    title = post_data.get('title', 'No title')
                    
                    # More lenient filtering for variety
                    if (not post_data.get('is_self') and 
                        post_data.get('url') and 
                        post_data.get('score', 0) > 5 and  # Lower threshold for more variety
                        len(title) > 10):  # Basic quality check
                        
                        # Check relevance but be more permissive
                        if is_article_relevant(title, role, interests) or random.random() < 0.3:  # 30% chance to include even if not perfectly relevant
                            article = {
                                "title": title,
                                "link": post_data.get('url', ''),
                                "summary": post_data.get('selftext', '')[:200] + "..." if post_data.get('selftext') else f"Reddit discussion with {post_data.get('score', 0)} upvotes",
                                "published": datetime.fromtimestamp(post_data.get('created_utc', 0)).strftime('%Y-%m-%d'),
                                "source": f"r/{subreddit}",
                                "category": categorize_article(title),
                                "score": post_data.get('score', 0),
                                "reddit_id": post_data.get('id'),  # For tracking duplicates
                                "sort_type": sort_type  # Track how it was fetched
                            }
                            subreddit_articles.append(article)
                
                # Take a random sample from each subreddit
                if subreddit_articles:
                    sample_size = min(3, len(subreddit_articles))
                    sampled_articles = random.sample(subreddit_articles, sample_size)
                    all_articles.extend(sampled_articles)
                    
            except Exception as e:
                print(f"Error fetching from r/{subreddit}: {e}")
                continue
        
        # Final randomization and return
        random.shuffle(all_articles)
        return all_articles[:limit]
        
    except Exception as e:
        print(f"Error fetching varied Reddit: {e}")
        return []

def fetch_newsapi_varied(role, interests, limit=15):
    """Fetch from News API with varied search strategies"""
    if not NEWS_API_KEY:
        return []
        
    try:
        all_articles = []
        
        # Strategy 1: Role-based keywords
        role_keywords = {
            'design': ['design', 'UI/UX', 'figma', 'adobe', 'user experience', 'interface design'],
            'engineering': ['programming', 'software development', 'javascript', 'python', 'react', 'API'],
            'product': ['product management', 'startup', 'SaaS', 'product launch', 'user research'],
            'business': ['startup', 'funding', 'venture capital', 'IPO', 'business strategy'],
            'ai_ml': ['artificial intelligence', 'machine learning', 'AI', 'neural networks', 'deep learning'],
            'general': ['technology', 'tech news', 'innovation', 'digital transformation']
        }
        
        keywords = role_keywords.get(role, role_keywords['general'])
        
        # Strategy 2: Multiple search approaches
        search_strategies = [
            ('everything', 'publishedAt', 1),  # Recent articles
            ('everything', 'popularity', 2),   # Popular articles
            ('top-headlines', 'publishedAt', 3) # Top headlines
        ]
        
        for endpoint, sort_by, days_back in search_strategies:
            # Random keyword selection
            selected_keywords = random.sample(keywords, min(3, len(keywords)))
            query = ' OR '.join([f'"{keyword}"' for keyword in selected_keywords])
            
            # Add interests to query
            if interests:
                interest_terms = random.sample(interests, min(2, len(interests)))
                interest_query = ' OR '.join([f'"{interest}"' for interest in interest_terms])
                query = f'({query}) OR ({interest_query})'
            
            url = f"https://newsapi.org/v2/{endpoint}"
            params = {
                'apiKey': NEWS_API_KEY,
                'q': query,
                'language': 'en',
                'sortBy': sort_by,
                'pageSize': limit // len(search_strategies),
                'from': (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            for article_data in data.get('articles', []):
                title = article_data.get('title', 'No title')
                if title and 'removed' not in title.lower():  # Filter out removed articles
                    article = {
                        "title": title,
                        "link": article_data.get('url', ''),
                        "summary": article_data.get('description', 'No description available'),
                        "published": article_data.get('publishedAt', '').split('T')[0],
                        "source": article_data.get('source', {}).get('name', 'Unknown'),
                        "category": categorize_article(title),
                        "strategy": endpoint  # Track which strategy found this
                    }
                    all_articles.append(article)
        
        # Remove duplicates and randomize
        unique_articles = remove_duplicate_articles(all_articles)
        random.shuffle(unique_articles)
        
        return unique_articles[:limit]
        
    except Exception as e:
        print(f"Error fetching varied News API: {e}")
        return []

def clear_shown_articles(user_id):
    """Clear shown articles for a user to reset their digest"""
    if user_id in shown_articles:
        shown_articles[user_id].clear()
        print(f"Cleared shown articles for user {user_id}")
        return True
    return False

def cleanup_old_shown_articles():
    """Clean up old shown articles to prevent memory bloat"""
    try:
        for user_id in list(shown_articles.keys()):
            if len(shown_articles[user_id]) > 150:  # Keep last 150 articles
                # Convert to list, keep last 100, convert back to set
                articles_list = list(shown_articles[user_id])
                shown_articles[user_id] = set(articles_list[-100:])
        
        print(f"Cleaned up shown articles for {len(shown_articles)} users")
    except Exception as e:
        print(f"Error cleaning up shown articles: {e}")

def get_article_freshness_stats(user_id):
    """Get statistics about article freshness for a user"""
    stats = {
        'total_shown': len(shown_articles.get(user_id, set())),
        'recent_articles': len(recent_articles.get(user_id, [])),
        'has_profile': user_id in user_profiles
    }
    return stats

if __name__ == '__main__':
    scheduler.start()
    print("PulseBot started! Daily digests scheduled for 9 AM.")
    app.run(debug=True, port=8000, host='127.0.0.1')