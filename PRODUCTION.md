# Production Deployment Guide

This guide covers deploying the ANSV Bot application in a production environment.

## Session Persistence

The application now uses **persistent sessions** that survive server restarts:

### Secret Key Management

The Flask secret key is managed automatically with the following priority:

1. **Environment Variable** (highest priority): Set `FLASK_SECRET_KEY` in `.env`
2. **Auto-generated File**: If not set, a persistent key is created in `.flask_secret_key`
3. **Never** store the secret key in git (it's in `.gitignore`)

### Session Storage

Sessions are stored on disk using Flask-Session:

- **Location**: `flask_session/` directory (auto-created)
- **Persistence**: Sessions survive server restarts
- **Security**: Session cookies are signed and encrypted

## Environment Configuration

### Quick Start

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and configure required values:
   - Twitch OAuth credentials
   - Stripe payment keys
   - Bot owner username
   - (Optional) Custom `FLASK_SECRET_KEY`

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Production Environment Variables

**Required for Production:**
- `TWITCH_CLIENT_ID` - Twitch application client ID
- `TWITCH_CLIENT_SECRET` - Twitch application secret
- `STRIPE_SECRET_KEY` - Stripe API secret key
- `STRIPE_PUBLISHABLE_KEY` - Stripe publishable key
- `STRIPE_WEBHOOK_SECRET` - Stripe webhook signing secret
- `BOT_OWNER` - Twitch username of bot administrator

**Optional but Recommended:**
- `FLASK_SECRET_KEY` - Custom secret key (auto-generated if not set)
- `FLASK_ENV=production` - Set production mode
- `SESSION_DIR` - Custom session storage directory
- `DATABASE_PATH` - Custom database location

## Running in Production

### Option 1: Simple Production Mode (Flask Development Server)

  **Not recommended for production with high traffic**

```bash
# Set environment to production
export FLASK_ENV=production

# Run the application
python webapp.py
```

### Option 2: Production Server with Gunicorn (Recommended)

Install Gunicorn:
```bash
pip install gunicorn gevent
```

Run with Gunicorn:
```bash
gunicorn --worker-class gevent --workers 4 --bind 0.0.0.0:5001 webapp:app
```

**Gunicorn Configuration Options:**
- `--workers 4` - Number of worker processes (adjust based on CPU cores)
- `--bind 0.0.0.0:5001` - Listen on all interfaces, port 5001
- `--timeout 120` - Worker timeout in seconds
- `--access-logfile logs/access.log` - Access log location
- `--error-logfile logs/error.log` - Error log location

### Option 3: Systemd Service (Linux)

Create `/etc/systemd/system/ansv-bot.service`:

```ini
[Unit]
Description=ANSV Twitch Bot Web Application
After=network.target

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/path/to/ansv-bot
Environment="PATH=/path/to/ansv-bot/venv/bin"
EnvironmentFile=/path/to/ansv-bot/.env
ExecStart=/path/to/ansv-bot/venv/bin/gunicorn \
    --worker-class gevent \
    --workers 4 \
    --bind 0.0.0.0:5001 \
    --timeout 120 \
    --access-logfile /var/log/ansv-bot/access.log \
    --error-logfile /var/log/ansv-bot/error.log \
    webapp:app

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable ansv-bot
sudo systemctl start ansv-bot
sudo systemctl status ansv-bot
```

## Reverse Proxy Setup

### Nginx Configuration

```nginx
upstream ansv_bot {
    server 127.0.0.1:5001;
}

server {
    listen 80;
    server_name your-domain.com;

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL Configuration
    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    location / {
        proxy_pass http://ansv_bot;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Static files
    location /static {
        alias /path/to/ansv-bot/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

## Security Checklist

- [ ] Set `FLASK_ENV=production` in environment
- [ ] Use strong, unique `FLASK_SECRET_KEY` (or let it auto-generate)
- [ ] Enable HTTPS with valid SSL certificate
- [ ] Configure firewall to restrict access to port 5001 (only from reverse proxy)
- [ ] Set restrictive file permissions on `.env` and `.flask_secret_key` (chmod 600)
- [ ] Regularly backup `messages.db` and `flask_session/` directory
- [ ] Monitor application logs in `logs/app.log`
- [ ] Set up log rotation for application and web server logs
- [ ] Review and restrict database file permissions
- [ ] Enable session timeout (configured in `utils/security.py`)

## Monitoring and Maintenance

### Log Locations

- Application logs: `logs/app.log`
- Channel logs: `logs/{channel_name}.txt`
- Gunicorn access logs: (configure with `--access-logfile`)
- Gunicorn error logs: (configure with `--error-logfile`)

### Database Backups

Regular backups are recommended:

```bash
# Create backup with timestamp
cp messages.db messages.db.backup.$(date +%Y%m%d_%H%M%S)

# Keep only last 7 days of backups
find . -name "messages.db.backup.*" -mtime +7 -delete
```

### Session Cleanup

Flask-Session automatically handles session cleanup, but you can manually clear old sessions:

```bash
# Remove all sessions older than 7 days
find flask_session/ -type f -mtime +7 -delete
```

## Troubleshooting

### Sessions Lost on Restart

**Symptom**: Users are logged out after server restart

**Solution**:
- Verify `.flask_secret_key` file exists and persists across restarts
- Check that `flask_session/` directory is preserved
- Ensure `SESSION_TYPE=filesystem` is configured
- Check file permissions on session storage directory

### Permission Errors

**Symptom**: Cannot write to session directory or secret key file

**Solution**:
```bash
# Fix ownership
chown -R www-data:www-data /path/to/ansv-bot/flask_session
chown www-data:www-data /path/to/ansv-bot/.flask_secret_key

# Fix permissions
chmod 755 /path/to/ansv-bot/flask_session
chmod 600 /path/to/ansv-bot/.flask_secret_key
```

### High Memory Usage

**Symptom**: Application consuming excessive memory

**Solutions**:
- Reduce number of Gunicorn workers
- Enable session cleanup (automatic with Flask-Session)
- Clear old Markov model cache files
- Consider using Redis for session storage (see Advanced Configuration)

## Advanced Configuration

### Using Redis for Sessions (Optional)

For high-traffic deployments, Redis provides better session performance:

1. Install Redis and Python client:
   ```bash
   sudo apt install redis-server
   pip install redis
   ```

2. Update `webapp.py` session configuration:
   ```python
   app.config['SESSION_TYPE'] = 'redis'
   app.config['SESSION_REDIS'] = redis.from_url('redis://localhost:6379')
   ```

3. Restart the application

### Database Optimization

For better performance with large datasets:

```bash
# Vacuum the database to reclaim space and rebuild indexes
sqlite3 messages.db 'VACUUM;'

# Analyze tables for query optimization
sqlite3 messages.db 'ANALYZE;'
```

## Support

For issues or questions:
- Check application logs in `logs/app.log`
- Review this documentation
- Check GitHub issues for similar problems
