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

# Real news sources configuration
NEWS_API_KEY = os.getenv("NEWS_API_KEY")  # Optional: get from newsapi.org for more sources

def fetch_hackernews_stories(limit=20):
    """Fetch top stories from Hacker News API"""
    try:
        # Get top story IDs
        top_stories_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
        response = requests.get(top_stories_url, timeout=10)
        story_ids = response.json()[:limit]
        
        articles = []
        for story_id in story_ids[:limit]:
            try:
                story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                story_response = requests.get(story_url, timeout=5)
                story_data = story_response.json()
                
                if story_data and story_data.get('type') == 'story' and story_data.get('url'):
                    article = {
                        "title": story_data.get('title', 'No title'),
                        "link": story_data.get('url', ''),
                        "summary": f"HackerNews discussion with {story_data.get('score', 0)} points and {story_data.get('descendants', 0)} comments",
                        "published": datetime.fromtimestamp(story_data.get('time', 0)).strftime('%Y-%m-%d'),
                        "source": "Hacker News",
                        "category": categorize_article(story_data.get('title', ''))
                    }
                    articles.append(article)
                    
            except Exception as e:
                print(f"Error fetching story {story_id}: {e}")
                continue
                
        return articles
    except Exception as e:
        print(f"Error fetching HackerNews: {e}")
        return []

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
    """Enhanced categorization with better keyword matching"""
    title_lower = title.lower()
    
    # Design keywords (most specific first)
    design_keywords = [
        'design system', 'ui design', 'ux design', 'user experience', 'user interface', 
        'figma', 'sketch', 'adobe xd', 'prototype', 'wireframe', 'mockup', 'typography', 
        'color theory', 'branding', 'visual design', 'interaction design', 'usability', 
        'accessibility', 'design thinking', 'design ops', 'design tools', 'framer',
        'principle', 'invision', 'miro', 'figjam', 'product design', 'web design', 
        'mobile design', 'graphic design'
    ]
    
    if any(keyword in title_lower for keyword in design_keywords):
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

def fetch_real_news(user_profile, limit=15):
    """Fetch real news from multiple sources based on user profile with better filtering"""
    print(f"Fetching real news for profile: {user_profile}")
    
    all_articles = []
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    print(f"User role: {primary_role}, Interests: {interests}")
    
    # Fetch from HackerNews with role-based filtering
    print("Fetching from HackerNews...")
    hn_articles = fetch_hackernews_stories_filtered(primary_role, interests, limit=15)
    all_articles.extend(hn_articles)
    
    # Fetch from Reddit with targeted subreddits
    print("Fetching from Reddit...")
    reddit_articles = fetch_reddit_filtered(primary_role, interests, limit=15)
    all_articles.extend(reddit_articles)
    
    # Fetch from News API with relevant keywords
    if NEWS_API_KEY:
        print("Fetching from News API...")
        news_articles = fetch_newsapi_filtered(primary_role, interests, limit=10)
        all_articles.extend(news_articles)
    
    # Add variety by shuffling
    random.shuffle(all_articles)
    
    # Enhanced scoring based on user profile
    scored_articles = []
    for article in all_articles:
        score = calculate_article_relevance_score(article, user_profile)
        scored_articles.append((score, article))
    
    # Sort by score and return top articles
    scored_articles.sort(key=lambda x: x[0], reverse=True)
    
    # Remove duplicates by title similarity
    unique_articles = remove_duplicate_articles([article for score, article in scored_articles])
    
    print(f"Final articles: {len(unique_articles)} after filtering and deduplication")
    for i, article in enumerate(unique_articles[:5]):
        print(f"  {i+1}. [{article['category']}] {article['title'][:50]}...")
    
    return unique_articles[:limit]

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
    """Check if article is relevant to user's role and interests"""
    title_lower = title.lower()
    
    # Role-specific keywords
    role_keywords = {
        'design': ['design', 'ui', 'ux', 'user experience', 'figma', 'sketch', 'adobe', 'prototype', 'wireframe', 'typography', 'visual', 'interface', 'usability', 'accessibility'],
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
    
    # General tech relevance
    general_tech = any(term in title_lower for term in ['tech', 'digital', 'software', 'app', 'platform', 'innovation'])
    
    return role_match or interest_match or general_tech

def calculate_article_relevance_score(article, user_profile):
    """Calculate relevance score for article based on user profile"""
    score = 0
    title_lower = article['title'].lower()
    summary_lower = article.get('summary', '').lower()
    
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    # Score based on category match
    if article.get('category') == primary_role:
        score += 10
    
    # Score based on interests
    for interest in interests:
        if interest.lower() in title_lower or interest.lower() in summary_lower:
            score += 5
    
    # Boost for recent articles
    try:
        article_date = datetime.strptime(article['published'], '%Y-%m-%d')
        days_old = (datetime.now() - article_date).days
        if days_old <= 1:
            score += 8
        elif days_old <= 3:
            score += 4
        elif days_old <= 7:
            score += 2
    except:
        pass
    
    # Boost for popular articles (if score available)
    if 'score' in article and article['score']:
        if article['score'] > 100:
            score += 3
        elif article['score'] > 50:
            score += 2
    
    # Special boost for design-related content (since you're a designer)
    design_terms = ['design', 'ui', 'ux', 'figma', 'user experience', 'prototype', 'visual']
    if primary_role == 'design' and any(term in title_lower for term in design_terms):
        score += 15
    
    return score

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
        
        # Prepare content for summarization
        content = "\n\n".join([
            f"Title: {article['title']}\nSummary: {article['summary']}\nSource: {article['source']}\nCategory: {article['category']}"
            for article in articles[:8]  # Limit to top 8 articles
        ])
        
        prompt = f"""
        Create a personalized daily digest for this user:
        Profile: {profile_summary}
        Role: {role} ({experience} level)
        Interests: {', '.join(interests)}
        
        Today's articles:
        {content}
        
        Instructions:
        1. Select 4-5 most relevant articles for this specific user
        2. Write engaging summaries (1-2 sentences each)
        3. Explain WHY each article matters to them personally
        4. Use a conversational tone like a knowledgeable colleague
        5. Start with a brief personalized greeting mentioning their role
        6. Keep the ENTIRE response under 1500 characters
        7. Use emojis sparingly (max 3-4 total)
        8. Format as plain text, no markdown
        
        CRITICAL: Keep response under 1500 characters total. Be concise but engaging.
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are a personalized news curator who creates concise, engaging digests under 1500 characters."},
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
    """Handle conversational interactions with enhanced AI capabilities"""
    try:
        user_profile = user_profiles.get(user_id, {})
        
        # Get recent articles for context
        user_recent_articles = recent_articles.get(user_id, [])
        articles_context = ""
        if user_recent_articles:
            articles_context = "\n\nRecent articles from their digest:\n" + "\n".join([
                f"- {article['title']}: {article['summary'][:100]}..."
                for article in user_recent_articles[:5]
            ])
        
        # Enhanced conversation prompt for design-focused responses
        conversation_prompt = f"""
        You are PulseBot, an intelligent and friendly AI assistant specialized in design, technology, and industry insights. You're having a conversation with a user who has this profile:
        
        Role: {user_profile.get('primary_role', 'professional')}
        Industry: {user_profile.get('industry', 'technology')}
        Experience: {user_profile.get('experience_level', 'mid')}
        Interests: {', '.join(user_profile.get('secondary_interests', []))}
        Company: {user_profile.get('company_stage', 'Unknown')}
        {articles_context}
        
        User message: "{user_message}"
        
        Respond as a knowledgeable colleague who understands design, technology, and industry trends. You can:
        
        🎨 **Design & UX:**
        - Discuss design trends, tools (Figma, Sketch, Adobe), and methodologies
        - Analyze UX patterns, accessibility, and user research
        - Share insights about design systems, prototyping, and design ops
        - Comment on design-related news and product launches
        
        🚀 **Technology & Products:**
        - Explain new frameworks, tools, and technologies
        - Discuss AI/ML developments (especially relevant to Groq!)
        - Analyze product strategies and market trends
        - Share startup and business insights
        
        💬 **Conversation Style:**
        - Be conversational, insightful, and engaging
        - Use relevant emojis naturally (but don't overdo it)
        - Reference specific articles from their recent digest when relevant
        - Ask follow-up questions to keep the conversation going
        - Share personal insights and opinions, not just facts
        - Keep responses concise but substantial (2-5 sentences)
        
        🔧 **Commands:**
        - If they want a new digest: suggest `/digest`
        - If they want to update preferences: mention `/preferences`
        - If they ask about features: explain what you can do
        
        IMPORTANT: 
        - Be natural and conversational, not robotic
        - Show genuine interest in their work and questions
        - Tailor your response to their role (especially design focus)
        - If they mention Groq, you can be enthusiastic since that's their company!
        - Don't always end with questions - sometimes just share insights
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are PulseBot, a knowledgeable and conversational AI assistant who specializes in design, technology, and industry insights. You have a warm, collegial personality and deep expertise in UX/UI design, product development, and tech trends."},
                {"role": "user", "content": conversation_prompt}
            ],
            temperature=0.8,  # Higher temperature for more creative/conversational responses
            max_tokens=600
        )
        
        bot_response = response.choices[0].message.content.strip()
        
        # Send response
        slack_client.chat_postMessage(
            channel=channel_id,
            text=bot_response
        )
        
        return True
        
    except Exception as e:
        print(f"Error in conversation: {e}")
        # Fallback response
        slack_client.chat_postMessage(
            channel=channel_id,
            text="I'm here to chat about design, tech trends, and industry insights! What's on your mind? 💭"
        )
        return False

        # Also update your message handling logic to be more conversational:

        # In your handle_slack_events function, replace the message handling section with this:

        # Handle app mentions and direct messages
        if data.get('type') == 'event_callback':
            event = data.get('event', {})
            
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
                    # Make it MUCH more conversational - respond to almost everything
                    # Filter out very short acknowledgments
                    should_respond = (
                        len(text.strip()) > 2 and  # More than just "ok", "hi", etc.
                        text.strip().lower() not in ['thanks', 'thank you', 'ok', 'okay', 'cool', 'nice', 'good', 'great', 'awesome'] and
                        not text.strip().startswith('!')  # Skip commands that aren't ours
                    )
                    
                    if should_respond:
                        def handle_async_conversation():
                            handle_conversation(user_id, text, channel)
                        
                        thread = threading.Thread(target=handle_async_conversation)
                        thread.start()
                else:
                    # New user - start onboarding
                    def send_async_onboarding():
                        send_onboarding_message(user_id, channel)
                    
                    thread = threading.Thread(target=send_async_onboarding)
                    thread.start()

        # Add some fun conversation starters:

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
    
    # Add conversation prompt
    message_blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "💬 Ask me about any articles or just chat! | 🔄 `/digest` | ⚙️ `/preferences`"
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
        
        message += "\n💬 Ask me about any articles or just chat!"
        
        response = slack_client.chat_postMessage(
            channel=channel_id,
            text=message
        )
        return True
        
    except Exception as e:
        print(f"Error sending simple digest: {e}")
        return False


def send_digest_to_user(user_id, channel_id=None):
    """Send personalized digest to a user with error handling"""
    try:
        # Check if user has a profile
        if user_id not in user_profiles:
            return send_onboarding_message(user_id, channel_id)
        
        user_profile = user_profiles[user_id]
        
        # Fetch personalized news
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
                text=f"Daily personalized digest"
            )
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
                            "text": "💡 You can chat with me about any articles, update your profile with `/preferences`, or get a fresh digest anytime with `/digest`."
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
                    # Handle conversation
                    conversation_triggers = [
                        'what', 'how', 'why', 'tell me', 'thoughts', 'think', 'opinion', 
                        'should i', 'explain', 'more about', 'details', '?'
                    ]
                    
                    is_conversation = (
                        len(text.strip()) > 10 and
                        (any(trigger in text.lower() for trigger in conversation_triggers) or 
                         text.endswith('?') or 
                         any(word in text.lower() for word in ['design', 'ui', 'ux', 'figma', 'article']))
                    )
                    
                    if is_conversation:
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
        "recent_articles": {user_id: len(articles) for user_id, articles in recent_articles.items()}
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

if __name__ == '__main__':
    scheduler.start()
    print("PulseBot started! Daily digests scheduled for 9 AM.")
    app.run(debug=True, port=8000, host='127.0.0.1')