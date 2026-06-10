# Web Dashboard — VPS Deployment

The web dashboard (FastAPI, default port `5001`) serves the Twitch-login-gated
control surface and each channel's **private TTS source URL** (`/tts/<token>`),
which streamers add to OBS as a Browser Source.

On a public VPS, **run it behind an HTTPS reverse proxy.** The app itself speaks
plain HTTP; the proxy terminates TLS, and the app trusts the proxy's forwarded
headers so it knows the real scheme/host. Session cookies are then marked
`Secure`, and the OAuth callback uses your public `https://` URL.

## 1. Twitch app
Create (or reuse) an app at <https://dev.twitch.tv/console> and register the
**OAuth Redirect URL** to your public callback, exactly:

```
https://bot.example.com/auth/twitch/callback
```

Put the client id/secret and that same URL in `settings.conf`:

```ini
[oauth]
twitch_client_id = ...
twitch_client_secret = ...
twitch_redirect_uri = https://bot.example.com/auth/twitch/callback
```

## 2. `[web]` config
```ini
[web]
host = 127.0.0.1            ; bound locally; the proxy faces the internet
port = 5001
secret_key = <run: python -c "import secrets; print(secrets.token_urlsafe(48))">
secure_cookies = true       ; cookies are HTTPS-only behind the proxy
forwarded_allow_ips = 127.0.0.1
```

`secret_key` **must** be a stable random value on a server — the random fallback
resets every session on each restart.

## 3. Reverse proxy (Caddy — automatic HTTPS)
`/etc/caddy/Caddyfile`:

```
bot.example.com {
    reverse_proxy 127.0.0.1:5001
}
```

Caddy obtains/renews a Let's Encrypt cert automatically and sets
`X-Forwarded-Proto: https`, which the app trusts via `forwarded_allow_ips`.

<details>
<summary>nginx equivalent</summary>

```nginx
server {
    listen 443 ssl;
    server_name bot.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket upgrade (dashboard + TTS sources):
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```
</details>

> WebSockets matter here: the dashboard event stream (`/ws/events`) and every
> TTS source (`/ws/tts/<token>`) use them, so the proxy must pass the
> `Upgrade`/`Connection` headers (Caddy does this automatically).

## 4. Use it
1. A streamer visits `https://bot.example.com`, logs in with Twitch.
2. They land on `/sources` and copy their URL: `https://bot.example.com/tts/<token>`.
3. They add that URL as an OBS **Browser Source**. Done.

If a URL leaks, hit **Rotate** on the `/sources` page to invalidate it.

## Exposing uvicorn directly (no proxy)
Set `host = 0.0.0.0` and open the port, but then **you** are responsible for TLS
(point `secret_key`, `secure_cookies`, and the redirect URI accordingly). The
reverse-proxy route is strongly recommended.
