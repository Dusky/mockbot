# Beta Launch Checklist

## Critical Setup (Must Do First)

### 1. Environment Setup
- [ ] Run `./launch.sh setup` to create virtual environment and install dependencies
- [ ] Copy `settings.example.conf` to `settings.conf`
- [ ] Configure Twitch OAuth credentials in `settings.conf`
- [ ] Configure bot account token in `settings.conf`
- [ ] Test that service starts: `./launch.sh start`

### 2. Twitch OAuth Application
- [ ] Create Twitch OAuth app at https://dev.twitch.tv/console
- [ ] Set redirect URL: `http://YOUR_DOMAIN:5001/auth/twitch/callback`
- [ ] Add Client ID to settings.conf `[oauth]` section
- [ ] Add Client Secret to settings.conf `[oauth]` section
- [ ] Update redirect URI in settings.conf if not localhost

### 3. Bot Account Setup
- [ ] Create dedicated Twitch account for the bot
- [ ] Get OAuth token for bot account (use https://twitchapps.com/tmi/)
- [ ] Add bot token to settings.conf `[twitch]` section
- [ ] Test bot can connect to a channel

## Premium/Payment Setup (Optional but Recommended)

### 4. Stripe Configuration (if offering Premium)
- [ ] Create Stripe account at https://stripe.com
- [ ] Create Premium product in Stripe ($2/month)
- [ ] Get Stripe Secret Key and add to settings.conf `[stripe]` section
- [ ] Get Stripe Publishable Key and add to settings.conf
- [ ] Create webhook endpoint in Stripe dashboard
- [ ] Add Stripe webhook secret to settings.conf
- [ ] Test subscription flow end-to-end

### 5. TTS Dependencies (if offering Premium)
- [ ] Run `./launch.sh setup-tts` (downloads ~2.7GB)
- [ ] Verify TTS works: test in dashboard after enabling premium
- [ ] Check disk space (need ~10GB for models and cache)

## Security & Access Control

### 6. User Management
- [ ] Create your admin account via web signup
- [ ] Run `./launch.sh make-admin YOUR_USERNAME` to promote yourself
- [ ] Test admin panel access at `/beta`
- [ ] Verify you can see all channels as admin

### 7. Security Hardening
- [ ] Change default secret keys in settings.conf
- [ ] Review `ALLOWED_COLUMNS` whitelist in webapp.py (line 4355)
- [ ] Verify premium TTS checks work (try accessing TTS without premium)
- [ ] Test that non-owners can't modify other channels
- [ ] Check that session tokens expire properly

## Bug Fixes & Known Issues

### 8. Issues We Just Fixed
- [x] TTS setting not persisting (fixed - channel_configs table created)
- [x] Premium security bypass vulnerability (fixed in recent commit)
- [x] Landing page design (redesigned)

### 9. Test Critical Paths
- [ ] **User Registration Flow**
  - Sign up with Twitch OAuth
  - Complete onboarding
  - See dashboard

- [ ] **Channel Configuration**
  - Toggle settings (Join Channel, Auto Reply, TTS)
  - Settings persist after restart
  - Changes reflect immediately

- [ ] **Bot Functionality**
  - Bot joins channel when "Join Channel" enabled
  - Bot learns from chat history
  - Bot generates messages with `!ansv speak`
  - Auto-reply works when enabled

- [ ] **Premium Features** (if enabled)
  - Purchase premium subscription
  - TTS toggle appears and works
  - TTS generates audio files
  - TTS files play in browser
  - Subscription cancellation works

## Production Readiness

### 10. Server Configuration
- [ ] Set up proper domain name (not localhost)
- [ ] Configure HTTPS/SSL certificate (Let's Encrypt)
- [ ] Set up reverse proxy (nginx/caddy)
- [ ] Configure firewall (allow ports 80, 443)
- [ ] Set up systemd service for auto-restart
- [ ] Configure log rotation

### 11. Monitoring & Backups
- [ ] Set up automated database backups: `./launch.sh backup`
- [ ] Create cron job for daily backups
- [ ] Monitor disk space (databases grow over time)
- [ ] Set up error alerting (email/discord webhook)
- [ ] Check logs regularly: `./launch.sh logs`

### 12. Performance
- [ ] Test with multiple channels (5-10)
- [ ] Monitor memory usage under load
- [ ] Check database query performance
- [ ] Ensure TTS generation doesn't block bot
- [ ] Test concurrent user access to dashboard

## Beta Tester Instructions

### 13. Documentation for Testers
- [ ] Create simple setup guide for beta users
- [ ] Document known limitations
- [ ] Provide feedback collection method (Discord/Form)
- [ ] Set expectations (it's beta software)
- [ ] Create troubleshooting FAQ

### 14. Beta Test Plan
- [ ] **Week 1**: 2-3 close friends
  - Test basic bot functionality
  - Test user signup and onboarding
  - Collect initial feedback

- [ ] **Week 2**: 5-10 trusted users
  - Test premium subscriptions
  - Test TTS functionality
  - Monitor server performance

- [ ] **Week 3+**: Expand based on stability
  - Fix critical bugs from Week 1-2
  - Add requested features
  - Prepare for wider release

## Pre-Launch Testing Checklist

### 15. Manual Tests to Run
```bash
# 1. Environment check
./launch.sh check

# 2. Start service
./launch.sh start

# 3. Check status
./launch.sh status

# 4. View logs
./launch.sh logs 50

# 5. Test restart
./launch.sh restart
```

### 16. Web Interface Tests
- [ ] Visit landing page - looks good?
- [ ] Sign up with test Twitch account
- [ ] Complete onboarding flow
- [ ] Access streamer dashboard
- [ ] Toggle all settings on/off
- [ ] Generate a test message
- [ ] Check TTS (if premium)
- [ ] Log out and log back in
- [ ] Verify settings persisted

### 17. Bot Functionality Tests
- [ ] Bot joins your channel
- [ ] Type in chat - bot learns messages
- [ ] `!ansv speak` - bot generates message
- [ ] `!ansv start` - auto-reply enables
- [ ] `!ansv stop` - auto-reply disables
- [ ] `!ansv lines 50` - setting changes
- [ ] `!ansv trust USERNAME` - adds trusted user

## Known Limitations & Disclaimers

### 18. Communicate to Beta Testers
- **Free tier**: Full Markov bot functionality, unlimited use
- **Premium tier**: TTS costs $2/month due to inference costs
- **Data**: Chat messages are stored locally to train the model
- **Beta status**: Expect bugs, provide feedback
- **No SLA**: This is hobby software, no uptime guarantees
- **Privacy**: Don't store sensitive information in chat

## Launch Day Checklist

### 19. Final Steps
- [ ] Backup databases one more time
- [ ] Restart service to ensure clean state
- [ ] Send invite links to beta testers
- [ ] Monitor logs actively for first few hours
- [ ] Be available for support questions
- [ ] Have rollback plan ready (backups!)

## Post-Launch Monitoring

### 20. First 24 Hours
- [ ] Check error logs every 2 hours
- [ ] Monitor disk space
- [ ] Watch for database corruption
- [ ] Track user signups
- [ ] Respond to user feedback quickly
- [ ] Fix critical bugs immediately

### 21. First Week
- [ ] Daily log review
- [ ] Database backup verification
- [ ] Performance monitoring
- [ ] User feedback analysis
- [ ] Bug triage and prioritization
- [ ] Feature request tracking

---

## Quick Start Commands

```bash
# Initial setup
./launch.sh setup
cp settings.example.conf settings.conf
nano settings.conf  # Add your credentials

# Start service
./launch.sh start

# Check status
./launch.sh status

# View logs
./launch.sh logs

# Restart after changes
./launch.sh restart

# Create admin
./launch.sh make-admin YOUR_USERNAME

# Backup before risky changes
./launch.sh backup
```

---

## Emergency Contacts

- **Database corruption**: `./launch.sh restore <backup_file>`
- **Service won't start**: Check `logs/ansv.log`
- **Bot won't join**: Verify bot OAuth token
- **TTS not working**: Check PyTorch installed, premium active
- **Payment issues**: Check Stripe webhook logs

---

**Good luck with your beta launch! 🚀**
