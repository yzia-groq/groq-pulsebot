import os
import asyncio
# import feedparser  # Temporarily commented out due to Python 3.13 issue
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from groq import Groq
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import time

app = Flask(__name__)

# Initialize clients
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# User profiles storage (in production, use a database)
user_profiles = {}
user_onboarding_state = {}

# Mock news data for testing (we'll replace this with real RSS later)
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
        "title": "Y Combinator Demo Day: AI Startups Dominate S25 Batch",
        "link": "https://techcrunch.com/yc-demo-day-2025",
        "summary": "Over 60% of Y Combinator's summer 2025 batch focuses on AI applications, with notable companies in healthcare, developer tools, and robotics...",
        "published": "2025-07-15",
        "source": "TechCrunch",
        "category": "business"
    },
    {
        "title": "Figma Introduces AI-Powered Design System Generator",
        "link": "https://figma.com/ai-design-systems",
        "summary": "Designers can now generate comprehensive design systems from simple prompts, including components, tokens, and documentation...",
        "published": "2025-07-15",
        "source": "Figma Blog",
        "category": "design"
    },
    {
        "title": "Stripe Launches Embedded Financial Services for SaaS",
        "link": "https://stripe.com/embedded-finance",
        "summary": "SaaS companies can now offer banking, lending, and payment services directly to their customers through Stripe's new platform...",
        "published": "2025-07-15",
        "source": "Stripe Blog",
        "category": "product"
    }
]

# Expanded news sources with more specific categories
NEWS_SOURCES = {
    "engineering": [
        "https://techcrunch.com/feed/",
        "https://hnrss.org/frontpage",
        "https://stackoverflow.blog/feed/",
        "https://github.blog/feed/",
        "https://dev.to/feed"
    ],
    "design": [
        "https://www.designernews.co/feed",
        "https://uxplanet.org/feed",
        "https://www.smashingmagazine.com/feed/",
        "https://dribbble.com/shots.rss"
    ],
    "product": [
        "https://www.producthunt.com/feed",
        "https://firstround.com/review/feed/",
        "https://www.mindtheproduct.com/feed/"
    ],
    "business": [
        "https://techcrunch.com/category/startups/feed/",
        "https://news.ycombinator.com/rss",
        "https://a16z.com/feed/"
    ],
    "ai_ml": [
        "https://blog.openai.com/rss/",
        "https://ai.googleblog.com/feeds/posts/default",
        "https://research.fb.com/feed/"
    ],
    "crypto": [
        "https://cointelegraph.com/rss",
        "https://coindesk.com/arc/outboundfeeds/rss/"
    ]
}

def fetch_news(category="engineering", limit=10):
    """Fetch latest news from RSS feeds for a given category"""
    articles = []
    sources = NEWS_SOURCES.get(category, NEWS_SOURCES["engineering"])
    
    for source_url in sources:
        try:
            feed = feedparser.parse(source_url)
            for entry in feed.entries[:limit//len(sources)]:
                article = {
                    "title": entry.title,
                    "link": entry.link,
                    "summary": getattr(entry, 'summary', '')[:200] + "...",
                    "published": getattr(entry, 'published', ''),
                    "source": feed.feed.title if hasattr(feed.feed, 'title') else source_url
                }
                articles.append(article)
        except Exception as e:
            print(f"Error fetching from {source_url}: {e}")
    
    return articles[:limit]

def create_user_profile(user_description):
    """Use Groq to analyze user description and create structured profile"""
    try:
        prompt = f"""
        Analyze this user's description and create a structured profile for personalized news curation:
        
        User description: "{user_description}"
        
        Extract and return a JSON structure with:
        {{
            "primary_role": "main job role (engineering/design/product/business/ai_ml/crypto)",
            "secondary_interests": ["list", "of", "secondary", "interests"],
            "industry": "industry they work in",
            "experience_level": "junior/mid/senior",
            "company_stage": "startup/scale-up/enterprise",
            "specific_technologies": ["technologies", "they", "mentioned"],
            "content_preferences": "technical/business/news/trends",
            "summary": "2-sentence summary of their profile"
        }}
        
        Be specific and infer details from context. If unclear, make reasonable assumptions.
        """
        
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "You are an expert at analyzing user descriptions to create personalized news profiles. Always return valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=800
        )
        
        import json
        profile_text = response.choices[0].message.content.strip()
        
        # Extract JSON from response
        try:
            start = profile_text.find('{')
            end = profile_text.rfind('}') + 1
            profile_json = profile_text[start:end]
            profile = json.loads(profile_json)
            return profile
        except:
            # Fallback parsing
            return {
                "primary_role": "engineering",
                "secondary_interests": [],
                "industry": "technology",
                "experience_level": "mid",
                "company_stage": "startup",
                "specific_technologies": [],
                "content_preferences": "technical",
                "summary": "General tech professional"
            }
        
    except Exception as e:
        print(f"Error creating profile: {e}")
        return None

def fetch_personalized_news(user_profile, limit=15):
    """Fetch news tailored to user's profile (using mock data for now)"""
    # For now, use mock articles filtered by user interests
    # TODO: Replace with real RSS parsing once feedparser issue is resolved
    
    primary_role = user_profile.get("primary_role", "engineering")
    interests = user_profile.get("secondary_interests", [])
    
    # Filter mock articles based on user profile
    relevant_articles = []
    
    for article in MOCK_ARTICLES:
        # Include if matches primary role or interests
        if (article["category"] == primary_role or 
            article["category"] in interests or
            any(interest in article["title"].lower() or interest in article["summary"].lower() 
                for interest in interests)):
            relevant_articles.append(article)
    
    # If we don't have enough relevant articles, add some general ones
    if len(relevant_articles) < 3:
        relevant_articles.extend([article for article in MOCK_ARTICLES 
                                if article not in relevant_articles][:limit-len(relevant_articles)])
    
    return relevant_articles[:limit]

def get_source_category(source_url):
    """Determine category of a news source"""
    for category, urls in NEWS_SOURCES.items():
        if source_url in urls:
            return category
    return "general"

# TODO: Real RSS implementation (once Python 3.13 compatibility is resolved)
def fetch_real_news(category="engineering", limit=10):
    """Future implementation for real RSS feeds"""
    # This will be implemented once feedparser works
    pass

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
    
    # Add feedback section
    message_blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "💬 Reply with feedback to improve your digest | 🔄 Use `/digest` for a fresh digest"
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
                            "text": "💡 You can update your profile anytime by describing yourself again, or use `/preferences` to make quick changes."
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

@app.route('/slack/events', methods=['POST'])
def handle_slack_events():
    """Handle Slack events and slash commands"""
    
    # Handle different content types from Slack
    if request.content_type and 'application/json' in request.content_type:
        data = request.get_json()
    else:
        # Slash commands come as form data
        data = request.form.to_dict()
    
    print(f"=== INCOMING SLACK EVENT ===")
    print(f"Content-Type: {request.content_type}")
    print(f"Event type: {data.get('type')}")
    print(f"Command: {data.get('command')}")
    print(f"Full data: {data}")
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
            # Send immediate digest or start onboarding
            if user_id in user_profiles:
                success = send_digest_to_user(user_id, channel_id)
                if success:
                    return jsonify({
                        'response_type': 'in_channel',
                        'text': '🚀 Generating your personalized digest...'
                    })
                else:
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': '❌ Sorry, there was an error generating your digest.'
                    })
            else:
                # Start onboarding
                send_onboarding_message(user_id, channel_id)
                return jsonify({
                    'response_type': 'ephemeral',
                    'text': '👋 Welcome! I need to learn about you first to create personalized digests.'
                })
        
        elif command == '/preferences':
            # Show current profile or help update it
            if user_id in user_profiles:
                profile = user_profiles[user_id]
                if text.strip():
                    # Update profile with new description
                    new_profile = create_user_profile(text)
                    if new_profile:
                        user_profiles[user_id] = new_profile
                        return jsonify({
                            'response_type': 'ephemeral',
                            'text': f'✅ Profile updated! New role: {new_profile.get("primary_role", "N/A")}'
                        })
                else:
                    # Show current profile
                    return jsonify({
                        'response_type': 'ephemeral',
                        'text': f'**Current Profile:**\n• Role: {profile.get("primary_role", "N/A")}\n• Industry: {profile.get("industry", "N/A")}\n• Interests: {", ".join(profile.get("secondary_interests", []))}\n\nTo update: `/preferences [describe yourself again]`'
                    })
            else:
                return jsonify({
                    'response_type': 'ephemeral',
                    'text': '❌ No profile found. Use `/digest` to get started!'
                })
    
    # Handle app mentions and direct messages
    if data.get('type') == 'event_callback':
        event = data.get('event', {})
        print(f"=== EVENT CALLBACK ===")
        print(f"Event: {event}")
        print(f"Event type: {event.get('type')}")
        
        if event.get('type') == 'message' and 'subtype' not in event:
            user_id = event.get('user')
            text = event.get('text', '')
            channel = event.get('channel')
            
            print(f"=== MESSAGE EVENT ===")
            print(f"User ID: {user_id}")
            print(f"Text: {text}")
            print(f"Channel: {channel}")
            print(f"User onboarding state: {user_onboarding_state}")
            print("====================")
            
            # Skip bot messages
            if user_id == data.get('authorizations', [{}])[0].get('user_id'):
                print("Skipping bot message")
                return jsonify({'status': 'ok'})
            
            # Check if user is in onboarding
            if user_id in user_onboarding_state:
                print("User is in onboarding - processing profile input")
                process_user_profile_input(user_id, text, channel)
                return jsonify({'status': 'ok'})
            else:
                print("User not in onboarding state")
    
    return jsonify({'status': 'ok'})

@app.route('/test-digest')
def test_digest():
    """Test endpoint to manually trigger a digest"""
    articles = fetch_news("engineering", 10)
    digest = summarize_with_groq(articles, "engineer")
    return jsonify({
        "articles_count": len(articles),
        "digest": digest,
        "articles": articles[:3]  # Show first 3 for testing
    })

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

@app.route('/test', methods=['GET', 'POST'])
def test():
    return jsonify({"status": "working", "method": request.method})


@app.route('/debug')
def debug():
    return jsonify({
        "user_onboarding_state": user_onboarding_state,
        "user_profiles": list(user_profiles.keys()),
        "total_users": len(user_profiles)
    })

if __name__ == '__main__':
    scheduler.start()
    print("PulseBot started! Daily digests scheduled for 9 AM.")
    app.run(debug=True, port=8000, host='127.0.0.1')