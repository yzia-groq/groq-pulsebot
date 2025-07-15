import os
import asyncio
import requests
import json
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
    """Categorize article based on title keywords"""
    title_lower = title.lower()
    
    # Design keywords (prioritize since you're a designer)
    design_keywords = [
        'design', 'ui', 'ux', 'user experience', 'user interface', 'figma', 'sketch', 
        'adobe', 'prototype', 'wireframe', 'mockup', 'design system', 'typography', 
        'color', 'branding', 'visual', 'interaction design', 'usability', 'accessibility',
        'product design', 'web design', 'mobile design', 'design thinking', 'design ops',
        'design tools', 'framer', 'principle', 'invision', 'miro', 'figjam'
    ]
    if any(keyword in title_lower for keyword in design_keywords):
        return 'design'
    
    # AI/ML keywords
    ai_keywords = ['ai', 'artificial intelligence', 'machine learning', 'ml', 'gpt', 'llm', 'neural', 'openai', 'anthropic', 'groq']
    if any(keyword in title_lower for keyword in ai_keywords):
        return 'ai_ml'
    
    # Engineering keywords
    engineering_keywords = ['javascript', 'python', 'react', 'node', 'programming', 'code', 'developer', 'github', 'framework', 'api', 'backend', 'frontend']
    if any(keyword in title_lower for keyword in engineering_keywords):
        return 'engineering'
    
    # Product keywords
    product_keywords = ['product management', 'product manager', 'pm', 'roadmap', 'feature', 'user research', 'analytics', 'metrics']
    if any(keyword in title_lower for keyword in product_keywords):
        return 'product'
    
    # Business keywords
    business_keywords = ['startup', 'funding', 'vc', 'investment', 'ipo', 'acquisition', 'revenue', 'saas', 'business model']
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
    """Fetch real news from multiple sources based on user profile"""
    print(f"Fetching real news for profile: {user_profile.get('primary_role', 'general')}")
    
    all_articles = []
    
    # Fetch from HackerNews (always good tech content)
    print("Fetching from HackerNews...")
    hn_articles = fetch_hackernews_stories(limit=10)
    all_articles.extend(hn_articles)
    
    # Fetch from Reddit
    print("Fetching from Reddit...")
    reddit_articles = fetch_reddit_tech_posts(limit=10)
    all_articles.extend(reddit_articles)
    
    # Fetch from News API if available
    if NEWS_API_KEY:
        print("Fetching from News API...")
        news_articles = fetch_newsapi_articles(limit=5)
        all_articles.extend(news_articles)
    
    # Filter and sort articles based on user profile
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    # Score articles based on relevance
    scored_articles = []
    for article in all_articles:
        score = 0
        title_lower = article['title'].lower()
        
        # Score based on primary role
        if article['category'] == primary_role:
            score += 3
        
        # Score based on interests
        for interest in interests:
            if interest.lower() in title_lower:
                score += 2
        
        # Boost recent articles
        try:
            article_date = datetime.strptime(article['published'], '%Y-%m-%d')
            days_old = (datetime.now() - article_date).days
            if days_old <= 1:
                score += 2
            elif days_old <= 7:
                score += 1
        except:
            pass
        
        scored_articles.append((score, article))
    
    # Sort by score and return top articles
    scored_articles.sort(key=lambda x: x[0], reverse=True)
    
    return [article for score, article in scored_articles[:limit]]

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
    """Fetch real news tailored to user's profile"""
    print(f"=== FETCHING REAL NEWS ===")
    print(f"Profile: {user_profile}")
    
    # Try to fetch real news first
    real_articles = fetch_real_news(user_profile, limit)
    
    if real_articles:
        print(f"Successfully fetched {len(real_articles)} real articles")
        return real_articles
    else:
        print("Failed to fetch real news, falling back to mock articles")
        # Fallback to mock articles if real news fails
        return fetch_mock_news_fallback(user_profile, limit)

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
    """Create a highly personalized digest based on user profile"""
    try:
        profile_summary = user_profile.get("summary", "tech professional")
        role = user_profile.get("primary_role", "engineering")
        interests = user_profile.get("secondary_interests", [])
        experience = user_profile.get("experience_level", "mid")
        
        # Prepare content for summarization
        content = "\n\n".join([
            f"Title: {article['title']}\nSummary: {article['summary']}\nSource: {article['source']}\nCategory: {article['category']}"
            for article in articles
        ])
        
        prompt = f"""
        Create a personalized daily digest for this user:
        Profile: {profile_summary}
        Role: {role} ({experience} level)
        Interests: {', '.join(interests)}
        
        Today's articles:
        {content}
        
        Instructions:
        1. Select 5-7 most relevant articles for this specific user
        2. Prioritize based on their role, interests, and experience level
        3. Write engaging summaries (2-3 sentences each)
        4. Explain WHY each article matters to them personally
        5. Use a conversational tone like a knowledgeable colleague
        6. Add relevant emojis and format nicely
        7. Start with a personalized greeting mentioning their role/interests
        
        Make it feel like it was curated specifically for them!
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are a personalized news curator who understands each user's unique professional needs and interests."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        return response.choices[0].message.content.strip()
    
    except Exception as e:
        print(f"Error with personalized summarization: {e}")
        return None

def handle_conversation(user_id, user_message, channel_id):
    """Handle conversational interactions about news and articles"""
    try:
        user_profile = user_profiles[user_id]
        
        # Get recent articles for context
        user_recent_articles = recent_articles.get(user_id, [])
        articles_context = ""
        if user_recent_articles:
            articles_context = "\n\nRecent articles from their digest:\n" + "\n".join([
                f"- {article['title']}: {article['summary'][:100]}..."
                for article in user_recent_articles[:3]
            ])
        
        # Use Groq to generate contextual responses
        conversation_prompt = f"""
        You are PulseBot, a helpful and knowledgeable news assistant. The user has this profile:
        Role: {user_profile.get('primary_role', 'professional')}
        Industry: {user_profile.get('industry', 'technology')}
        Experience: {user_profile.get('experience_level', 'mid')}
        Interests: {', '.join(user_profile.get('secondary_interests', []))}
        {articles_context}
        
        User message: "{user_message}"
        
        Respond helpfully as a knowledgeable colleague. You can discuss:
        - News and industry trends relevant to their role
        - Article explanations and deeper insights
        - Career advice and professional development
        - Technology predictions and analysis
        - Startup and business insights
        
        Be conversational, insightful, and concise (2-4 sentences max).
        Reference specific articles from their recent digest when relevant.
        If they want a new digest, suggest using /digest.
        If they're asking about updating preferences, mention /preferences.
        
        IMPORTANT: Only respond to genuine questions or conversation starters. 
        If the message seems like just an acknowledgment or very short response, politely engage but keep it brief.
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are PulseBot, a knowledgeable news and industry assistant who provides insightful, helpful responses."},
                {"role": "user", "content": conversation_prompt}
            ],
            temperature=0.7,
            max_tokens=400
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
            text="I'm here to help with news and industry insights! Ask me about trends in your field, or use `/digest` for your personalized news. 💡"
        )
        return False

def format_slack_message(digest, articles, user_profile):
    """Format the digest for Slack with proper formatting and links"""
    role = user_profile.get("primary_role", "professional")
    
    message_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🌅 Your Personalized Digest - {datetime.now().strftime('%B %d, %Y')}"
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Curated for: {role.title()} | {len(articles)} articles analyzed"
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
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*📚 Read the Full Articles:*"
            }
        }
    ]
    
    # Add article links with categories
    for i, article in enumerate(articles[:6]):
        category_emoji = {
            "engineering": "⚙️",
            "design": "🎨", 
            "product": "📱",
            "business": "💼",
            "ai_ml": "🤖",
            "crypto": "₿"
        }.get(article.get("category", "general"), "📰")
        
        message_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{category_emoji} <{article['link']}|{article['title']}>"
            }
        })
    
    # Add conversation prompt
    message_blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "💬 Ask me about any of these articles! | 🔄 Use `/digest` for a fresh digest | ⚙️ Use `/preferences` to update your profile"
            }
        ]
    })
    
    return message_blocks

def send_digest_to_user(user_id, channel_id=None):
    """Send personalized digest to a user"""
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
            digest = "Here are today's top stories curated for you:"
        
        # Format for Slack
        message_blocks = format_slack_message(digest, articles, user_profile)
        
        # Send message
        if channel_id:
            response = slack_client.chat_postMessage(
                channel=channel_id,
                blocks=message_blocks,
                text=f"Daily personalized digest"
            )
        else:
            response = slack_client.chat_postMessage(
                channel=user_id,
                blocks=message_blocks,
                text=f"Daily personalized digest"
            )
        
        return True
        
    except SlackApiError as e:
        print(f"Slack API error: {e}")
        return False
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

if __name__ == '__main__':
    scheduler.start()
    print("PulseBot started! Daily digests scheduled for 9 AM.")
    app.run(debug=True, port=8000, host='127.0.0.1')