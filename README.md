# 🤖 PulseBot - AI-Powered Personalized News Digest

A Slack bot that delivers personalized industry news digests using Groq's lightning-fast AI inference.

## 🚀 Features

- **Conversational Onboarding**: Users describe themselves in natural language
- **AI Profile Creation**: Groq analyzes descriptions to create structured user profiles  
- **Personalized Curation**: News tailored to role, interests, and experience level
- **Instant Delivery**: Fast responses powered by Groq's inference speed
- **Slack Integration**: Seamless `/digest` commands and DM conversations

## 🛠️ Setup

### Prerequisites
- Python 3.9+
- Slack workspace admin access
- Groq API key
- ngrok (for local development)

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/yzia-groq/groq-pulsebot.git
cd pulsebot
```

2. **Set up virtual environment**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Configure environment variables**
```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export GROQ_API_KEY="your-groq-api-key"
```

4. **Create Slack App**
- Go to https://api.slack.com/apps
- Create new app "PulseBot"
- Add Bot Token Scopes: `chat:write`, `commands`, `im:write`, `im:history`
- Create slash commands: `/digest`, `/preferences`
- Enable Event Subscriptions with `message.im` and `app_mention`
- Install to workspace and copy Bot Token

5. **Start the application**
```bash
# Terminal 1: Start ngrok
ngrok http 8000

# Terminal 2: Start Flask app
python3 app.py
```

6. **Update Slack App URLs**
- Set Request URLs to: `https://your-ngrok-url.ngrok.io/slack/events`

## 📱 Usage

1. **First time**: Run `/digest` in Slack
2. **Describe yourself**: "I'm a senior software engineer focused on AI and machine learning"
3. **Get personalized digest**: Bot creates your profile and delivers curated news
4. **Daily digests**: Automatic delivery at 9 AM (when scheduling is enabled)

## 🎯 User Flow

```
User: /digest
Bot: Tell me about yourself...
User: I'm a product manager interested in AI tools and startup funding
Bot: ✅ Profile Created! [shows extracted profile]
Bot: 🌅 Your Personalized Digest [curated news with explanations]
```

## 🏗️ Architecture

- **Flask Backend**: Handles Slack webhooks and API calls
- **Groq AI**: Profile analysis and content summarization  
- **Slack SDK**: Bot interactions and messaging
- **Mock News System**: Placeholder for RSS integration
- **In-Memory Storage**: User profiles and onboarding state

## 🧪 Development

### Current Status
- ✅ Slack bot integration
- ✅ AI-powered onboarding  
- ✅ Profile creation from natural language
- ✅ Personalized content curation
- ✅ Mock news articles
- 🚧 Real RSS feed integration (Python 3.13 compatibility issue)
- 🚧 Database persistence
- 🚧 Daily scheduling

### Adding Team Members
1. Invite to Slack workspace where bot is installed
2. Have them run `/digest` to create profiles
3. Instant personalized news!

## 🔧 Configuration

### Supported User Roles
- Engineering (`engineering`)
- Design (`design`) 
- Product Management (`product`)
- Business/Startup (`business`)
- AI/ML Research (`ai_ml`)
- Crypto/Web3 (`crypto`)

### News Sources (Mock)
- TechCrunch, Hacker News, Stack Overflow Blog
- Designer News, UX Planet, Smashing Magazine
- Product Hunt, First Round Review
- Y Combinator News, A16z Blog

## 🚀 Deployment

For production deployment:
1. Replace in-memory storage with database (PostgreSQL/MongoDB)
2. Fix RSS feed integration for real news
3. Deploy to cloud platform (Heroku, Railway, etc.)
4. Set up proper environment variable management
5. Enable daily scheduling with cron jobs

## 🤝 Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/new-feature`
3. Commit changes: `git commit -am 'Add new feature'`
4. Push to branch: `git push origin feature/new-feature`
5. Submit pull request

## 📄 License

MIT License - see LICENSE file for details

## 🎉 Demo

Perfect for demonstrating:
- **Groq's inference speed** for real-time AI processing
- **Personalized AI applications** beyond generic chatbots
- **Practical workplace AI integration** 
- **Conversational UX design**

Built for [Groq Internal Hackathon 2025] 🏆