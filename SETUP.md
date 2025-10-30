# ANSV Bot - Setup Guide

## Quick Start

```bash
# 1. Initial installation
./launch.sh setup

# 2. Configure credentials (see below)
nano settings.conf

# 3. Start the service
./launch.sh start

# 4. Access web interface
# http://localhost:5001
```

## Configuration

### 1. Create Twitch OAuth App

You need to create a Twitch OAuth application for user authentication:

1. Go to https://dev.twitch.tv/console
2. Click "Register Your Application"
3. Fill in the details:
   - **Name**: ANSV Bot (or your app name)
   - **OAuth Redirect URLs**: `http://localhost:5001/auth/twitch/callback`
     - For production: `https://yourdomain.com/auth/twitch/callback`
   - **Category**: Chat Bot or Application Integration
4. Click "Create"
5. Copy the **Client ID** and generate a **Client Secret**

### 2. Configure settings.conf

Edit `settings.conf` and add your OAuth credentials:

```ini
[oauth]
; Twitch OAuth App for web authentication
twitch_client_id = your_client_id_from_step_1
twitch_client_secret = your_client_secret_from_step_1
twitch_redirect_uri = http://localhost:5001/auth/twitch/callback
```

### 3. Configure Stripe (for payments)

If you want to enable premium subscriptions:

1. Go to https://dashboard.stripe.com/
2. Get your API keys
3. Add to `settings.conf`:

```ini
[stripe]
publishable_key = pk_test_...
secret_key = sk_test_...
webhook_secret = whsec_...  # From Stripe webhook settings
price_id = price_...  # Your premium plan price ID
```

4. Set up webhook endpoint:
   - URL: `https://yourdomain.com/stripe/webhook`
   - Events: `checkout.session.completed`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.deleted`, `customer.subscription.updated`

### 4. Bot Credentials (Optional)

If you want the bot to actually connect to Twitch chat:

```ini
[auth]
tmi_token = oauth:your_bot_oauth_token
client_id = your_bot_client_id
nickname = YourBotName
```

Get bot OAuth token from: https://twitchapps.com/tmi/

## Service Management

```bash
# Start service
./launch.sh start

# Stop service
./launch.sh stop

# Restart service
./launch.sh restart

# View logs
./launch.sh logs 100

# Check status
./launch.sh status

# Deploy updates
./launch.sh deploy
```

## Troubleshooting

### "OAuth not configured" error

This means you haven't set up the `[oauth]` section in `settings.conf`. See step 1 and 2 above.

### "Database migration failed"

Run migrations manually:
```bash
./launch.sh migrate
```

### Service won't start

Check logs:
```bash
./launch.sh logs
tail -f logs/ansv.log
```

### Port 5001 already in use

Change the port in `settings.conf`:
```ini
[web]
port = 8080
```

## Production Deployment

For production, you should:

1. **Use HTTPS**: Set up a reverse proxy (nginx, caddy) with SSL
2. **Update redirect URI**: Change `twitch_redirect_uri` to your domain
3. **Secure secrets**: Use environment variables or a secrets manager
4. **Enable monitoring**: Set up log aggregation and monitoring
5. **Backup regularly**: Use `./launch.sh backup` in a cron job
6. **Update webhook endpoint**: Configure Stripe webhook to your domain

## Architecture

- **Web Interface**: Flask app on port 5001 (configurable)
- **Bot**: TwitchIO bot connecting to Twitch IRC
- **Database**: SQLite (users.db for users, messages.db for chat data)
- **TTS**: Optional Bark AI text-to-speech (requires premium)
- **Payments**: Stripe integration for subscriptions

## Support

- Issues: https://github.com/your-repo/issues
- Docs: Check this file and comments in settings.conf
- Twitch OAuth: https://dev.twitch.tv/docs/authentication
- Stripe: https://stripe.com/docs/webhooks
