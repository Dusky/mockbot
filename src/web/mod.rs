use crate::db::{CorpusLineRow, TtsLogRow, UserAccount};
use crate::markov::{BrainKind, BrainWeight, GenerationParams, GenerationResult};
use crate::state::{AppState, IrcControlCmd};
use crate::tts::list_available_voices;
use axum::{
    body::Body,
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        DefaultBodyLimit, Path, Query, State,
    },
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tower_http::cors::CorsLayer;
use tracing::info;

const UI_HTML: &str = include_str!("ui.html");

pub async fn run_web_server(state: Arc<AppState>) -> anyhow::Result<()> {
    let bind_addr = state.config.web.bind_addr.clone();
    let app = Router::new()
        .route("/", get(index_handler))
        .route("/channels", get(index_handler))
        .route("/admin", get(index_handler))
        .route("/obs/:channel", get(obs_overlay_handler))
        .route("/outputs/:channel/:filename", get(serve_wav_handler))
        // Public minimal summary + Auth endpoints
        .route("/api/public/summary", get(public_summary_handler))
        .route("/auth/twitch/callback", get(twitch_oauth_callback_page))
        .route("/api/auth/twitch", post(auth_twitch_handler))
        .route("/api/auth/login", post(auth_login_handler))
        .route("/api/auth/register", post(auth_register_handler))
        .route("/api/auth/me", get(auth_me_handler))
        // Authenticated Streamer + Admin endpoints (scoped by role!)
        .route("/api/status", get(status_handler))
        .route("/api/events", get(events_handler))
        .route("/api/ws", get(ws_handler))
        .route("/api/public/channels", get(public_channels_handler))
        .route("/api/public/generate", post(sandbox_generate_handler))
        .route("/api/tts/voices", get(tts_voices_handler))
        .route("/api/tts/logs", get(tts_logs_handler))
        .route("/api/tts/generate", post(tts_generate_handler))
        .route("/api/tts/skip", post(tts_skip_handler))
        .route("/api/tts/save_voice", post(tts_save_voice_handler))
        .route("/api/tts/upload_voice", post(tts_upload_voice_handler))
        .route("/api/tts/delete_voice", post(tts_delete_voice_handler))
        .route("/api/tts/reset_drift", post(tts_reset_drift_handler))
        .route("/api/tts/clone_audio", post(tts_clone_audio_handler))
        .route("/api/tts/delete_log", post(tts_delete_log_handler))
        .route("/api/tts/test_obs", post(tts_test_obs_handler))
        .route("/api/admin/channel", post(update_channel_handler))
        .route("/api/admin/channel/delete", post(admin_delete_channel_handler))
        .route("/api/admin/speak", post(speak_handler))
        .route("/api/admin/irc_cmd", post(admin_irc_cmd_handler))
        .route("/api/admin/brains/train", post(admin_train_brain_handler))
        .route("/api/admin/brains/import", post(custom_import_corpus_handler))
        .route("/api/admin/brains/voice", post(admin_set_brain_voice_handler))
        .route("/api/admin/brains/lines", get(search_brain_lines_handler))
        .route("/api/admin/brains/chatters", get(top_chatters_handler))
        .route("/api/admin/brains/mock_chatter", post(mock_chatter_preview_handler))
        .route("/api/admin/brains/export", get(export_brain_lines_handler))
        .route(
            "/api/admin/brains/delete_line",
            post(delete_brain_line_handler),
        )
        .route(
            "/api/admin/brains/delete_query",
            post(delete_brain_query_handler),
        )
        .route("/api/admin/brains/delete", post(admin_delete_brain_handler))
        .route("/api/admin/brains/merge", post(admin_merge_brain_handler))
        .route(
            "/api/admin/brains/purge_origin",
            post(admin_purge_origin_handler),
        )
        .route("/api/admin/brains/trim", post(admin_trim_brain_handler))
        .route("/api/admin/users", get(admin_list_users_handler))
        .route("/api/admin/users", post(admin_upsert_user_handler))
        .route("/api/admin/users/delete", post(admin_delete_user_handler))
        .route("/api/admin/backup_db", get(admin_backup_db_handler))
        .route("/api/public/join", post(public_join_handler))
        .route("/api/public/part", post(public_part_handler))
        .layer(DefaultBodyLimit::max(64 * 1024 * 1024))
        .layer(CorsLayer::permissive())
        .with_state(state.clone());

    let listener = tokio::net::TcpListener::bind(&bind_addr).await?;
    info!("WebUI & API server listening on http://{}", bind_addr);
    state.log_system("INFO", format!("WebUI & API listening on http://{}", bind_addr));
    axum::serve(listener, app).await?;
    Ok(())
}

async fn index_handler() -> Html<&'static str> {
    Html(UI_HTML)
}

async fn obs_overlay_handler(
    Path(channel): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Html<String> {
    let clean = channel.trim().trim_start_matches('#').to_lowercase();
    let (obs_key, idle_img, talk_img) = {
        let chans = state.channels.read().unwrap();
        if let Some(rt) = chans.get(&clean) {
            (
                rt.record.obs_key.clone(),
                rt.record.obs_idle_img.clone(),
                rt.record.obs_talk_img.clone(),
            )
        } else {
            (String::new(), String::new(), String::new())
        }
    };
    let html = format!(
        r#"<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>ANSV2 OBS Bark TTS Overlay — #{clean}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
  body {{ margin: 0; padding: 24px; background: transparent; overflow: hidden; font-family: 'IBM Plex Mono', monospace; color: #f4f0e6; }}
  #stage {{ display: none; align-items: flex-end; gap: 18px; max-width: 760px; }}
  #avatar {{
    max-width: 140px;
    max-height: 140px;
    object-fit: contain;
    transition: transform 0.06s ease-out;
    transform-origin: bottom center;
  }}
  #banner {{
    background: rgba(20, 19, 17, 0.94);
    border: 1px solid #5c564b;
    border-left: 4px solid #d94b2a;
    border-radius: 4px;
    padding: 14px 18px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.55);
  }}
  .voice-tag {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.1em; color: #d94b2a; margin-bottom: 4px; }}
  .line-text {{ font-size: 1.05rem; font-weight: 600; line-height: 1.4; }}
</style>
</head>
<body>
  <div id="stage">
    <img id="avatar" class="hidden" alt="" />
    <div id="banner">
      <div class="voice-tag" id="voice-label">BARK TTS</div>
      <div class="line-text" id="tts-text"></div>
    </div>
  </div>
  <audio id="player" crossorigin="anonymous" preload="auto"></audio>
  <script>
    const targetChannel = {clean:?};
    const requiredKey = {obs_key:?};
    const idleImg = {idle_img:?};
    const talkImg = {talk_img:?} || idleImg;

    const urlParams = new URLSearchParams(location.search);
    if (requiredKey && urlParams.get('key') !== requiredKey) {{
      document.body.innerHTML = '';
      throw new Error('Invalid OBS overlay key');
    }}

    const vol = Math.min(1, Math.max(0, parseFloat(urlParams.get('volume') || '1.0')));
    const showBanner = urlParams.get('banner') !== 'false';
    const queue = [];
    let playing = false;
    const player = document.getElementById('player');
    player.volume = isNaN(vol) ? 1.0 : vol;
    const stage = document.getElementById('stage');
    const banner = document.getElementById('banner');
    const avatar = document.getElementById('avatar');

    if (idleImg) {{
      avatar.src = idleImg;
      avatar.style.display = 'block';
    }} else {{
      avatar.style.display = 'none';
    }}
    if (!showBanner) {{
      banner.style.display = 'none';
    }}

    let audioCtx = null;
    let analyser = null;
    let dataArr = null;

    function ensureAnalyser() {{
      if (audioCtx) return;
      try {{
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const src = audioCtx.createMediaElementSource(player);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        dataArr = new Uint8Array(analyser.frequencyBinCount);
        src.connect(analyser);
        analyser.connect(audioCtx.destination);
      }} catch (_) {{}}
    }}

    function animateAvatar() {{
      if (!playing) return;
      if (analyser && dataArr && idleImg) {{
        analyser.getByteTimeDomainData(dataArr);
        let sum = 0;
        for (let i = 0; i < dataArr.length; i++) {{
          const v = (dataArr[i] - 128) / 128;
          sum += v * v;
        }}
        const rms = Math.sqrt(sum / dataArr.length);
        if (rms > 0.035) {{
          if (avatar.src !== talkImg && talkImg) avatar.src = talkImg;
          const lift = Math.min(10, Math.round(rms * 42));
          avatar.style.transform = `translateY(-${{lift}}px) scale(1.03)`;
        }} else {{
          if (avatar.src !== idleImg) avatar.src = idleImg;
          avatar.style.transform = 'none';
        }}
      }}
      requestAnimationFrame(animateAvatar);
    }}

    function playNext() {{
      if (playing || queue.length === 0) return;
      playing = true;
      ensureAnalyser();
      if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
      const item = queue.shift();
      document.getElementById('voice-label').textContent = item.voice_preset;
      document.getElementById('tts-text').textContent = item.text;
      stage.style.display = 'flex';
      player.src = item.audio_url;
      player.play().then(() => requestAnimationFrame(animateAvatar)).catch(() => finish());
    }}
    function finish() {{
      stage.style.display = 'none';
      if (idleImg) avatar.src = idleImg;
      avatar.style.transform = 'none';
      playing = false;
      setTimeout(playNext, 400);
    }}
    player.onended = finish;
    player.onerror = finish;

    function connect() {{
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(`${{proto}}//${{location.host}}/api/ws`);
      ws.onmessage = (e) => {{
        try {{
          const ev = JSON.parse(e.data);
          if (ev.type === 'tts_generated' && ev.channel === targetChannel) {{
            queue.push(ev);
            playNext();
          }} else if (ev.type === 'tts_skip' && ev.channel === targetChannel) {{
            queue.length = 0;
            player.pause();
            player.removeAttribute('src');
            finish();
          }}
        }} catch (_) {{}}
      }};
      ws.onclose = () => setTimeout(connect, 3000);
    }}
    connect();
  </script>
</body>
</html>"#
    );
    Html(html)
}

async fn serve_wav_handler(
    Path((channel, filename)): Path<(String, String)>,
) -> impl IntoResponse {
    let clean_chan = channel
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        .collect::<String>();
    let clean_file = filename
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-' || *c == '.')
        .collect::<String>();
    if !clean_file.ends_with(".wav") {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let full_path = format!("/home/matt/ansv2/outputs/{}/{}", clean_chan, clean_file);
    match tokio::fs::read(&full_path).await {
        Ok(bytes) => Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "audio/wav")
            .header(header::CACHE_CONTROL, "public, max-age=86400")
            .body(Body::from(bytes))
            .unwrap(),
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

#[derive(Serialize)]
struct PublicSummary {
    bot_nick: String,
    irc_connected: bool,
    tts_ready: bool,
    public_join_enabled: bool,
    joined_channels: usize,
    twitch_client_id: String,
    twitch_redirect_uri: String,
}

async fn public_summary_handler(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let joined = state
        .channels
        .read()
        .unwrap()
        .values()
        .filter(|c| c.currently_joined)
        .count();
    let public_nick = if !state.config.twitch.mockbot_nickname.trim().is_empty() {
        state.config.twitch.mockbot_nickname.clone()
    } else {
        state.config.twitch.nickname.clone()
    };
    Json(PublicSummary {
        bot_nick: public_nick,
        irc_connected: state.irc_connected.load(Ordering::Relaxed),
        tts_ready: state.tts_ready.load(Ordering::Relaxed),
        public_join_enabled: state.config.web.public_join_enabled,
        joined_channels: joined,
        twitch_client_id: state.config.twitch.client_id.clone(),
        twitch_redirect_uri: state.config.twitch.oauth_redirect_uri.clone(),
    })
}

#[derive(Serialize)]
struct PublicChannelCard {
    channel_name: String,
    display_name: String,
    avatar_url: String,
    bio: String,
    followers: u32,
    is_live: bool,
    stream_title: String,
    stream_game: String,
    stream_viewers: u32,
    currently_joined: bool,
    tts_enabled: bool,
    private_brain: bool,
    corpus_lines: usize,
    vocab_size: usize,
    sample_quote: String,
}

async fn public_channels_handler(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let chans: Vec<crate::state::ChannelRuntime> = {
        let map = state.channels.read().unwrap();
        map.values()
            .filter(|c| c.record.join_enabled)
            .cloned()
            .collect()
    };

    let mut out = Vec::new();
    {
        let eng = state.engine.read().unwrap();
        let b_stats = eng.list_brain_stats();
        for rt in chans {
            let chan_brain = format!("channel:{}", rt.record.channel_name);
            let (corpus_lines, vocab_size) = b_stats
                .iter()
                .find(|b| b.name == chan_brain)
                .map(|b| (b.sentence_count, b.unique_tokens))
                .unwrap_or((0, 0));

            let sample_quote = if !rt.record.private_brain {
                let params = crate::markov::GenerationParams {
                    brains: rt.record.brain_mix.clone(),
                    order: rt.record.markov_order,
                    temperature: rt.record.temperature,
                    seed: None,
                    min_words: 4,
                    max_words: 28,
                    max_chars: 220,
                    max_overlap_ratio: 0.70,
                    max_overlap_total: 15,
                    max_attempts: 25,
                    recent_outputs: Vec::new(),
                    blocked_words: rt.record.blocked_words.clone(),
                };
                eng.generate(&params).map(|r| r.text).unwrap_or_default()
            } else {
                String::new()
            };

            let display_name = if rt.display_name.is_empty() {
                rt.record.channel_name.clone()
            } else {
                rt.display_name.clone()
            };

            out.push(PublicChannelCard {
                channel_name: rt.record.channel_name,
                display_name,
                avatar_url: rt.avatar_url,
                bio: rt.bio,
                followers: rt.followers,
                is_live: rt.is_stream_live,
                stream_title: rt.stream_title,
                stream_game: rt.stream_game,
                stream_viewers: rt.stream_viewers,
                currently_joined: rt.currently_joined,
                tts_enabled: rt.record.tts_enabled,
                private_brain: rt.record.private_brain,
                corpus_lines,
                vocab_size,
                sample_quote,
            });
        }
    }

    // Sort live channels first, then by corpus_lines desc, then followers desc
    out.sort_by(|a, b| {
        b.is_live
            .cmp(&a.is_live)
            .then_with(|| b.corpus_lines.cmp(&a.corpus_lines))
            .then_with(|| b.followers.cmp(&a.followers))
    });

    Json(out)
}

async fn twitch_oauth_callback_page() -> Html<&'static str> {
    Html(
        r#"<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Authenticating with Twitch...</title>
  <style>
    body { background: #141311; color: #f4f0e6; font-family: monospace; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .box { border: 1px solid #3d3932; padding: 24px; background: #211f1b; max-width: 420px; }
  </style>
</head>
<body>
  <div class="box" id="status">Verifying Twitch login...</div>
  <script>
    (async () => {
      const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
      const queryParams = new URLSearchParams(location.search);
      const access_token = hashParams.get('access_token') || queryParams.get('access_token');
      const code = queryParams.get('code');
      const statusEl = document.getElementById('status');

      if (!access_token && !code) {
        statusEl.textContent = 'No OAuth token or code returned from Twitch.';
        return;
      }

      try {
        const res = await fetch('/api/auth/twitch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            access_token: access_token || null,
            code: code || null,
            redirect_uri: location.origin + '/auth/twitch/callback'
          })
        });
        const data = await res.json();
        if (data.ok && data.result) {
          localStorage.setItem('ansv2_auth_token', data.result.api_token);
          location.replace('/');
        } else {
          statusEl.textContent = data.message || 'Twitch authentication failed.';
        }
      } catch (e) {
        statusEl.textContent = 'Error contacting server: ' + e;
      }
    })();
  </script>
</body>
</html>"#,
    )
}

#[derive(Deserialize)]
struct TwitchAuthReq {
    access_token: Option<String>,
    code: Option<String>,
    redirect_uri: Option<String>,
}

async fn auth_twitch_handler(
    State(state): State<Arc<AppState>>,
    Json(req): Json<TwitchAuthReq>,
) -> impl IntoResponse {
    let mut token_opt = req
        .access_token
        .map(|t| t.trim().trim_start_matches("oauth:").to_string())
        .filter(|t| !t.is_empty());

    // If OAuth authorization code was passed and client_secret is configured, exchange code for access_token
    if token_opt.is_none() {
        if let Some(code) = req.code.as_deref().filter(|s| !s.is_empty()) {
            let redirect = req
                .redirect_uri
                .unwrap_or_else(|| state.config.twitch.oauth_redirect_uri.clone());
            if let Ok(exchanged) = exchange_twitch_code(
                &state.config.twitch.client_id,
                &state.config.twitch.client_secret,
                code,
                &redirect,
            )
            .await
            {
                token_opt = Some(exchanged);
            }
        }
    }

    let Some(access_token) = token_opt else {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse::<UserAccount> {
                ok: false,
                message: "Missing Twitch OAuth token.".to_string(),
                result: None,
            }),
        );
    };

    let login = match validate_twitch_token(&access_token).await {
        Ok(l) => l.to_lowercase(),
        Err(e) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(ApiResponse {
                    ok: false,
                    message: format!("Twitch token validation failed: {}", e),
                    result: None,
                }),
            )
        }
    };

    let is_owner = is_admin_login(&login, &state);
    let role = if is_owner { "admin" } else { "streamer" };
    let chan_scope = if is_owner { "*" } else { login.as_str() };

    if !is_owner {
        let _ = state.add_or_join_channel(
            &login,
            "twitch_oauth",
            state.config.web.public_join_contribute_global,
        );
    }

    // Generate a random internal password hash since OAuth authenticated this user
    let oauth_secret = format!("twitch_oauth_{}_{}", login, access_token);
    match state
        .db
        .create_or_update_user_with_verification(&login, &oauth_secret, role, chan_scope, true, "")
    {
        Ok(acct) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Authenticated via Twitch as @{}!", acct.username),
                result: Some(acct),
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

async fn validate_twitch_token(token: &str) -> anyhow::Result<String> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio_rustls::{rustls, TlsConnector};

    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();
    let connector = TlsConnector::from(Arc::new(tls_config));

    let tcp = tokio::net::TcpStream::connect(("id.twitch.tv", 443)).await?;
    let domain = rustls::pki_types::ServerName::try_from("id.twitch.tv")?;
    let mut tls = connector.connect(domain, tcp).await?;

    let req = format!(
        "GET /oauth2/validate HTTP/1.1\r\nHost: id.twitch.tv\r\nAuthorization: OAuth {}\r\nConnection: close\r\n\r\n",
        token
    );
    tls.write_all(req.as_bytes()).await?;
    let mut raw = String::new();
    tls.read_to_string(&mut raw).await?;

    let (_, body) = raw
        .split_once("\r\n\r\n")
        .ok_or_else(|| anyhow::anyhow!("Invalid HTTP response from Twitch"))?;
    // Handle possible chunked or plain JSON body
    let json_start = body.find('{').ok_or_else(|| anyhow::anyhow!("No JSON in Twitch response"))?;
    let json_end = body.rfind('}').ok_or_else(|| anyhow::anyhow!("Malformed JSON"))?;
    let val: serde_json::Value = serde_json::from_str(&body[json_start..=json_end])?;

    if let Some(login) = val.get("login").and_then(|v| v.as_str()) {
        Ok(login.to_string())
    } else {
        let msg = val
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("Invalid OAuth token");
        Err(anyhow::anyhow!("{}", msg))
    }
}

async fn exchange_twitch_code(
    client_id: &str,
    client_secret: &str,
    code: &str,
    redirect_uri: &str,
) -> anyhow::Result<String> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio_rustls::{rustls, TlsConnector};

    if client_id.is_empty() || client_secret.is_empty() {
        return Err(anyhow::anyhow!("client_id or client_secret not configured"));
    }

    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();
    let connector = TlsConnector::from(Arc::new(tls_config));

    let tcp = tokio::net::TcpStream::connect(("id.twitch.tv", 443)).await?;
    let domain = rustls::pki_types::ServerName::try_from("id.twitch.tv")?;
    let mut tls = connector.connect(domain, tcp).await?;

    let form_body = format!(
        "client_id={}&client_secret={}&code={}&grant_type=authorization_code&redirect_uri={}",
        client_id, client_secret, code, redirect_uri
    );
    let req = format!(
        "POST /oauth2/token HTTP/1.1\r\nHost: id.twitch.tv\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        form_body.len(),
        form_body
    );
    tls.write_all(req.as_bytes()).await?;
    let mut raw = String::new();
    tls.read_to_string(&mut raw).await?;

    let (_, body) = raw
        .split_once("\r\n\r\n")
        .ok_or_else(|| anyhow::anyhow!("Invalid HTTP response"))?;
    let json_start = body.find('{').ok_or_else(|| anyhow::anyhow!("No JSON"))?;
    let json_end = body.rfind('}').ok_or_else(|| anyhow::anyhow!("Malformed JSON"))?;
    let val: serde_json::Value = serde_json::from_str(&body[json_start..=json_end])?;
    val.get("access_token")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| anyhow::anyhow!("Failed to exchange code for token"))
}

fn resolve_auth_user(headers: &HeaderMap, state: &AppState) -> Option<UserAccount> {
    let token = headers
        .get("x-auth-token")
        .or_else(|| headers.get("x-admin-token"))
        .and_then(|v| v.to_str().ok())
        .map(|s| s.trim())
        .unwrap_or("");

    if token.is_empty() {
        return None;
    }

    // Direct match against config.toml admin_token (also used by local TUI bridge)
    if !state.config.web.admin_token.is_empty() && token == state.config.web.admin_token.trim() {
        return Some(UserAccount {
            username: "admin".to_string(),
            password_hash: String::new(),
            role: "admin".to_string(),
            tier: "pro".to_string(),
            channel_name: "*".to_string(),
            api_token: token.to_string(),
            verified: true,
            claim_code: String::new(),
            created_at: 0,
        });
    }

    state.db.get_user_by_token(token).ok().flatten()
}

#[derive(Deserialize)]
struct LoginReq {
    username: String,
    password: String,
}

#[derive(Serialize)]
struct ApiResponse<T: Serialize> {
    ok: bool,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<T>,
}

fn is_admin_login(login: &str, state: &AppState) -> bool {
    let l = login.trim().to_lowercase();
    l == "admin"
        || l == "firestarman"
        || l == "anormalskipviewer"
        || l == "mockbot"
        || l.eq_ignore_ascii_case(&state.config.twitch.owner)
        || l.eq_ignore_ascii_case(&state.config.twitch.nickname)
        || l.eq_ignore_ascii_case(&state.config.twitch.mockbot_nickname)
}

async fn auth_login_handler(
    State(state): State<Arc<AppState>>,
    Json(req): Json<LoginReq>,
) -> impl IntoResponse {
    let user_clean = req.username.trim().to_lowercase();
    // Allow admin login using config.toml admin_token as password
    if is_admin_login(&user_clean, &state)
        && req.password == state.config.web.admin_token
    {
        if let Ok(acct) = state
            .db
            .create_or_update_user(&user_clean, &req.password, "admin", "*")
        {
            return (
                StatusCode::OK,
                Json(ApiResponse {
                    ok: true,
                    message: "Signed in as Administrator.".to_string(),
                    result: Some(acct),
                }),
            );
        }
    }

    match state.db.authenticate_user(&user_clean, &req.password) {
        Ok(Some(acct)) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Signed in as @{} ({})", acct.username, acct.role),
                result: Some(acct),
            }),
        ),
        _ => (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse {
                ok: false,
                message: "Invalid username or password.".to_string(),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct RegisterStreamerReq {
    channel_name: String,
    password: String,
    lines_between_messages: Option<u32>,
    time_between_messages: Option<u32>,
    brain_mode: Option<String>,
}

async fn auth_register_handler(
    State(state): State<Arc<AppState>>,
    Json(req): Json<RegisterStreamerReq>,
) -> impl IntoResponse {
    if !state.config.web.public_join_enabled {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse::<UserAccount> {
                ok: false,
                message: "Public streamer registration is currently disabled.".to_string(),
                result: None,
            }),
        );
    }

    let clean_chan = match AppState::validate_channel_name(&req.channel_name) {
        Ok(c) => c,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(ApiResponse {
                    ok: false,
                    message: e.to_string(),
                    result: None,
                }),
            )
        }
    };

    if req.password.trim().len() < 3 {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Password must be at least 3 characters.".to_string(),
                result: None,
            }),
        );
    }

    // Check if an account already exists for this username/channel
    if let Ok(existing_users) = state.db.list_users() {
        if existing_users
            .iter()
            .any(|u| u.username == clean_chan || u.channel_name == clean_chan)
        {
            return (
                StatusCode::CONFLICT,
                Json(ApiResponse {
                    ok: false,
                    message: format!(
                        "#{} already has an instance account. Please sign in instead.",
                        clean_chan
                    ),
                    result: None,
                }),
            );
        }
    }

    // Create channel instance
    let mut rec = match state.add_or_join_channel(
        &clean_chan,
        "streamer_signup",
        state.config.web.public_join_contribute_global,
    ) {
        Ok(r) => r,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(ApiResponse {
                    ok: false,
                    message: e.to_string(),
                    result: None,
                }),
            )
        }
    };

    if let Some(lines) = req.lines_between_messages {
        rec.lines_between_messages = lines.clamp(0, 5000);
    }
    if let Some(mins) = req.time_between_messages {
        rec.time_between_messages = mins.min(1440);
    }
    if let Some(mode) = req.brain_mode.as_deref() {
        let cb = format!("channel:{}", clean_chan);
        rec.brain_mix = match mode {
            "channel" => vec![BrainWeight {
                brain: cb,
                weight: 1.0,
            }],
            "global" => vec![BrainWeight {
                brain: "global".to_string(),
                weight: 1.0,
            }],
            _ => vec![
                BrainWeight {
                    brain: cb,
                    weight: 0.70,
                },
                BrainWeight {
                    brain: "global".to_string(),
                    weight: 0.30,
                },
            ],
        };
    }

    // Start with voice_enabled = false until broadcaster verifies in chat with !ansv claim <code>
    rec.voice_enabled = false;
    {
        let mut chans = state.channels.write().unwrap();
        if let Some(rt) = chans.get_mut(&clean_chan) {
            rt.record = rec.clone();
            let _ = state.db.upsert_channel(&rt.record);
        }
    }

    let claim_code = {
        use rand::Rng;
        format!("{:04}", rand::thread_rng().gen_range(1000..9999))
    };

    match state.db.create_or_update_user_with_verification(
        &clean_chan,
        req.password.trim(),
        "streamer",
        &clean_chan,
        false,
        &claim_code,
    ) {
        Ok(acct) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!(
                    "Joined #{}! Type '!ansv claim {}' in your Twitch chat as the broadcaster to verify ownership.",
                    clean_chan, claim_code
                ),
                result: Some(acct),
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

async fn auth_me_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    match resolve_auth_user(&headers, &state) {
        Some(user) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: "Authenticated".to_string(),
                result: Some(user),
            }),
        ),
        None => (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<UserAccount> {
                ok: false,
                message: "Not authenticated".to_string(),
                result: None,
            }),
        ),
    }
}

async fn status_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let mut snap = state.status_snapshot();
    // If called by a streamer, scope `channels` strictly to their own channel
    // and hide other channels' private brains!
    if let Some(user) = resolve_auth_user(&headers, &state) {
        if user.role != "admin" {
            let private_brains: std::collections::HashSet<String> = snap
                .channels
                .iter()
                .filter(|c| c.record.private_brain && c.record.channel_name != user.channel_name)
                .map(|c| format!("channel:{}", c.record.channel_name))
                .collect();
            snap.channels
                .retain(|c| c.record.channel_name == user.channel_name);
            snap.brains.retain(|b| !private_brains.contains(&b.name));
        }
    }
    Json(snap)
}

async fn events_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let user_opt = resolve_auth_user(&headers, &state);
    let events: Vec<_> = state
        .recent_events
        .read()
        .unwrap()
        .iter()
        .filter(|ev| match &user_opt {
            Some(u) if u.role != "admin" => match ev {
                crate::state::LiveEvent::ChatMessage { channel, .. }
                | crate::state::LiveEvent::BotResponse { channel, .. }
                | crate::state::LiveEvent::ChannelStatusChanged { channel, .. }
                | crate::state::LiveEvent::TtsGenerated { channel, .. }
                | crate::state::LiveEvent::TtsSkip { channel } => {
                    channel == &u.channel_name
                }
                crate::state::LiveEvent::SystemLog { .. } => false,
            },
            _ => true,
        })
        .cloned()
        .collect();
    Json(events)
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_ws(socket, state))
}

async fn handle_ws(mut socket: WebSocket, state: Arc<AppState>) {
    let initial: Vec<_> = state.recent_events.read().unwrap().iter().cloned().collect();
    for ev in initial {
        if let Ok(txt) = serde_json::to_string(&ev) {
            if socket.send(Message::Text(txt)).await.is_err() {
                return;
            }
        }
    }

    let mut rx = state.event_tx.subscribe();
    while let Ok(ev) = rx.recv().await {
        if let Ok(txt) = serde_json::to_string(&ev) {
            if socket.send(Message::Text(txt)).await.is_err() {
                break;
            }
        }
    }
}

async fn tts_voices_handler() -> impl IntoResponse {
    Json(list_available_voices())
}

#[derive(Deserialize)]
struct TtsLogsQuery {
    channel: Option<String>,
    limit: Option<usize>,
}

async fn tts_logs_handler(
    headers: HeaderMap,
    Query(q): Query<TtsLogsQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<Vec<TtsLogRow>> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let chan_filter = if user.role == "admin" {
        q.channel.as_deref()
    } else {
        Some(user.channel_name.as_str())
    };

    let logs = state
        .db
        .list_tts_logs(chan_filter, q.limit.unwrap_or(50))
        .unwrap_or_default();
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("{} clips", logs.len()),
            result: Some(logs),
        }),
    )
}

#[derive(Deserialize)]
struct TtsGenReq {
    channel: String,
    text: String,
    voice_preset: Option<String>,
}

async fn tts_generate_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<TtsGenReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    if user.role != "admin" && user.tier == "free" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Pro tier required for GPU Bark TTS.".to_string(),
                result: None,
            }),
        );
    }

    let target_chan = if user.role == "admin" {
        req.channel.trim().trim_start_matches('#').to_lowercase()
    } else {
        user.channel_name.clone()
    };

    if req.text.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Text cannot be empty.".to_string(),
                result: None,
            }),
        );
    }

    match state.queue_tts(&target_chan, req.text.trim(), req.voice_preset) {
        Ok(()) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!(
                    "Queued Bark TTS synthesis for #{}. Audio will appear in history when ready.",
                    target_chan
                ),
                result: None,
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct TtsSkipReq {
    channel: Option<String>,
}

async fn tts_skip_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<TtsSkipReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let target = if user.role == "admin" {
        req.channel
            .unwrap_or_else(|| user.channel_name.clone())
            .trim()
            .trim_start_matches('#')
            .to_lowercase()
    } else {
        user.channel_name.clone()
    };
    state.clear_tts_queue(&target);
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("Cleared TTS queue and skipped OBS audio for #{}.", target),
            result: None,
        }),
    )
}

async fn tts_test_obs_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<TtsSkipReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let target = if user.role == "admin" {
        req.channel
            .unwrap_or_else(|| user.channel_name.clone())
            .trim()
            .trim_start_matches('#')
            .to_lowercase()
    } else {
        user.channel_name.clone()
    };

    let logs = state
        .db
        .list_tts_logs(Some(&target), 1)
        .unwrap_or_default();
    if let Some(latest) = logs.first() {
        state.emit_event(crate::state::LiveEvent::TtsGenerated {
            id: latest.id,
            timestamp: chrono::Utc::now().timestamp(),
            channel: target.clone(),
            voice_preset: latest.voice_preset.clone(),
            text: latest.text_content.clone(),
            audio_url: format!("/{}", latest.file_path),
            duration_secs: latest.duration_secs,
            gen_secs: 0.0,
        });
        (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Sent test playback to OBS overlay for #{}.", target),
                result: None,
            }),
        )
    } else {
        (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Generate at least one TTS clip first to test OBS playback.".to_string(),
                result: None,
            }),
        )
    }
}

#[derive(Deserialize)]
struct SaveVoiceReq {
    log_id: i64,
    voice_name: String,
}

async fn tts_save_voice_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<SaveVoiceReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let clean_name: String = req
        .voice_name
        .trim()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        .collect();
    if clean_name.len() < 2 || clean_name.len() > 32 {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Voice name must be 2–32 alphanumeric/underscore characters.".to_string(),
                result: None,
            }),
        );
    }

    let Ok(Some(row)) = state.db.get_tts_log_by_id(req.log_id) else {
        return (
            StatusCode::NOT_FOUND,
            Json(ApiResponse {
                ok: false,
                message: "TTS log entry not found.".to_string(),
                result: None,
            }),
        );
    };

    if user.role != "admin" && row.channel_name != user.channel_name {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Permission denied.".to_string(),
                result: None,
            }),
        );
    }

    let src_npz = format!("/home/matt/ansv2/{}.npz", row.file_path);
    let dst_npz = format!("/home/matt/ansv2/voices/{}.npz", clean_name);
    if !std::path::Path::new(&src_npz).exists() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "No saved voice prompt (.npz) found for this clip (only newly generated clips have .npz data).".to_string(),
                result: None,
            }),
        );
    }

    match std::fs::copy(&src_npz, &dst_npz) {
        Ok(_) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Saved new Bark voice preset '{}'!", clean_name),
                result: None,
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: format!("Failed to save voice: {}", e),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct UploadVoiceReq {
    voice_name: String,
    hex_bytes: String,
}

async fn tts_upload_voice_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<UploadVoiceReq>,
) -> impl IntoResponse {
    let Some(_user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let clean_name: String = req
        .voice_name
        .trim()
        .trim_end_matches(".npz")
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        .collect();
    if clean_name.len() < 2 || clean_name.len() > 36 {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Voice name must be 2–36 alphanumeric characters.".to_string(),
                result: None,
            }),
        );
    }

    let hex_clean = req.hex_bytes.trim();
    if hex_clean.len() < 16 || hex_clean.len() > 16_000_000 || hex_clean.len() % 2 != 0 {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Invalid .npz file size.".to_string(),
                result: None,
            }),
        );
    }

    let mut bytes = Vec::with_capacity(hex_clean.len() / 2);
    for i in (0..hex_clean.len()).step_by(2) {
        if let Ok(b) = u8::from_str_radix(&hex_clean[i..i + 2], 16) {
            bytes.push(b);
        } else {
            return (
                StatusCode::BAD_REQUEST,
                Json(ApiResponse {
                    ok: false,
                    message: "Invalid hex encoding.".to_string(),
                    result: None,
                }),
            );
        }
    }

    // Verify ZIP/NPZ magic header PK\x03\x04
    if bytes.len() < 4 || &bytes[0..4] != b"PK\x03\x04" {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Uploaded file is not a valid .npz archive.".to_string(),
                result: None,
            }),
        );
    }

    let dst = format!("/home/matt/ansv2/voices/{}.npz", clean_name);
    match std::fs::write(&dst, &bytes) {
        Ok(()) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Uploaded Bark voice '{}.npz' ({} bytes).", clean_name, bytes.len()),
                result: None,
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct DeleteVoiceReq {
    voice_name: String,
}

async fn tts_delete_voice_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<DeleteVoiceReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin required to delete voice presets.".to_string(),
                result: None,
            }),
        );
    }

    let clean_name: String = req
        .voice_name
        .trim()
        .trim_end_matches(".npz")
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-' || *c == ' ' || *c == '.')
        .collect();
    let dst = format!("/home/matt/ansv2/voices/{}.npz", clean_name);
    if std::path::Path::new(&dst).exists() && std::fs::remove_file(&dst).is_ok() {
        (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Deleted voice preset '{}'.", clean_name),
                result: None,
            }),
        )
    } else {
        (
            StatusCode::NOT_FOUND,
            Json(ApiResponse {
                ok: false,
                message: "Custom .npz voice file not found.".to_string(),
                result: None,
            }),
        )
    }
}

async fn tts_reset_drift_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<TtsSkipReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let target = if user.role == "admin" {
        req.channel
            .unwrap_or_else(|| user.channel_name.clone())
            .trim()
            .trim_start_matches('#')
            .to_lowercase()
    } else {
        user.channel_name.clone()
    };
    let _ = state.reset_bark_drift(&target);
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("Reset Bark voice drift for #{}.", target),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct CloneAudioReq {
    voice_name: String,
    transcript: Option<String>,
    ext: Option<String>,
    hex_bytes: String,
}

async fn tts_clone_audio_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<CloneAudioReq>,
) -> impl IntoResponse {
    let Some(_user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let clean_name: String = req
        .voice_name
        .trim()
        .trim_end_matches(".npz")
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        .collect();
    if clean_name.len() < 2 || clean_name.len() > 32 {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Voice name must be 2–32 alphanumeric/underscore characters.".to_string(),
                result: None,
            }),
        );
    }

    let hex_clean = req.hex_bytes.trim();
    if hex_clean.len() < 64 || hex_clean.len() > 30_000_000 || hex_clean.len() % 2 != 0 {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Invalid audio file size.".to_string(),
                result: None,
            }),
        );
    }

    let mut bytes = Vec::with_capacity(hex_clean.len() / 2);
    for i in (0..hex_clean.len()).step_by(2) {
        if let Ok(b) = u8::from_str_radix(&hex_clean[i..i + 2], 16) {
            bytes.push(b);
        } else {
            return (
                StatusCode::BAD_REQUEST,
                Json(ApiResponse {
                    ok: false,
                    message: "Invalid hex encoding.".to_string(),
                    result: None,
                }),
            );
        }
    }

    let ext_clean: String = req
        .ext
        .unwrap_or_else(|| "wav".to_string())
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect();
    let tmp_path = format!("/tmp/ansv2_clone_{}.{}", clean_name, ext_clean);
    if let Err(e) = std::fs::write(&tmp_path, &bytes) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        );
    }

    match state.clone_audio_voice(
        tmp_path,
        clean_name.clone(),
        req.transcript.unwrap_or_default(),
    ) {
        Ok(()) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!(
                    "Cloning '{}' in background via EnCodec + Bark (~5s). Refresh voices in a moment.",
                    clean_name
                ),
                result: None,
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct PublicGenerateReq {
    brains: Option<Vec<BrainWeight>>,
    order: Option<usize>,
    temperature: Option<f64>,
    seed: Option<String>,
    count: Option<usize>,
}

#[derive(Serialize)]
struct MultiGenerateResp {
    ok: bool,
    message: String,
    result: Option<GenerationResult>,
    results: Vec<GenerationResult>,
}

async fn sandbox_generate_handler(
    State(state): State<Arc<AppState>>,
    Json(req): Json<PublicGenerateReq>,
) -> impl IntoResponse {
    let count = req.count.unwrap_or(1).clamp(1, 10);
    let mut params = GenerationParams {
        brains: req.brains.unwrap_or_else(|| {
            vec![BrainWeight {
                brain: "global".to_string(),
                weight: 1.0,
            }]
        }),
        order: req.order.unwrap_or(state.config.markov.default_order).clamp(1, 3),
        temperature: req.temperature.unwrap_or(1.0).clamp(0.25, 3.0),
        seed: req.seed.filter(|s| !s.trim().is_empty()),
        min_words: state.config.markov.min_words,
        max_words: state.config.markov.max_words,
        max_chars: 450,
        max_overlap_ratio: state.config.markov.max_overlap_ratio,
        max_overlap_total: state.config.markov.max_overlap_total,
        max_attempts: state.config.markov.max_attempts,
        recent_outputs: Vec::new(),
        blocked_words: Vec::new(),
    };

    let mut results = Vec::with_capacity(count);
    {
        let eng = state.engine.read().unwrap();
        for _ in 0..count {
            if let Some(res) = eng.generate(&params) {
                params.recent_outputs.push(res.text.clone());
                results.push(res);
            }
        }
    }

    if let Some(first) = results.first().cloned() {
        Json(MultiGenerateResp {
            ok: true,
            message: "Generated".to_string(),
            result: Some(first),
            results,
        })
    } else {
        Json(MultiGenerateResp {
            ok: false,
            message: "Could not generate a sentence from the selected brain(s).".to_string(),
            result: None,
            results: Vec::new(),
        })
    }
}

#[derive(Deserialize)]
struct ChannelUpdateReq {
    channel_name: String,
    join_enabled: bool,
    voice_enabled: bool,
    learn_enabled: bool,
    contribute_to_global: bool,
    lines_between_messages: u32,
    time_between_messages: u32,
    markov_order: usize,
    #[serde(default = "default_one")]
    temperature: f64,
    #[serde(default = "default_thirty_five")]
    context_bias_chance: u32,
    reply_mention_chance: u32,
    #[serde(default)]
    tts_enabled: bool,
    #[serde(default)]
    tts_delay_enabled: bool,
    #[serde(default = "default_vp")]
    voice_preset: String,
    brain_mix: Vec<BrainWeight>,
    #[serde(default)]
    custom_learn_brains: Vec<String>,
    trusted_users: Vec<String>,
    ignored_users: Vec<String>,
    #[serde(default)]
    blocked_words: Vec<String>,
    #[serde(default = "default_ten")]
    command_cooldown_secs: u32,
    #[serde(default)]
    only_when_live: bool,
    #[serde(default)]
    raid_greet_enabled: bool,
    #[serde(default)]
    sub_greet_enabled: bool,
    #[serde(default)]
    reward_tts_id: String,
    #[serde(default)]
    reward_markov_id: String,
    #[serde(default = "default_twenty_five")]
    bark_cursedness: u32,
    #[serde(default = "default_zero_seven")]
    bark_text_temp: f64,
    #[serde(default = "default_zero_seven")]
    bark_waveform_temp: f64,
    #[serde(default)]
    bark_drift_enabled: bool,
    #[serde(default)]
    trigger_keywords: Vec<String>,
    #[serde(default = "default_fifty")]
    trigger_chance: u32,
    #[serde(default)]
    custom_commands: Vec<crate::db::CustomCommandRule>,
    #[serde(default)]
    min_bits_tts: u32,
    #[serde(default)]
    min_bits_markov: u32,
    #[serde(default)]
    obs_key: String,
    #[serde(default)]
    obs_idle_img: String,
    #[serde(default)]
    obs_talk_img: String,
    #[serde(default)]
    private_brain: bool,
    #[serde(default)]
    bot_identity: Option<String>,
}

fn default_one() -> f64 {
    1.0
}
fn default_zero_seven() -> f64 {
    0.70
}
fn default_fifty() -> u32 {
    50
}
fn default_thirty_five() -> u32 {
    35
}
fn default_twenty_five() -> u32 {
    25
}
fn default_ten() -> u32 {
    10
}
fn default_vp() -> String {
    "v2/en_speaker_5".to_string()
}

async fn update_channel_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<ChannelUpdateReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    if user.role != "admin" && !user.verified {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: format!(
                    "Unverified channel. Type '!ansv claim {}' in #{} as broadcaster first.",
                    user.claim_code, user.channel_name
                ),
                result: None,
            }),
        );
    }

    let clean = req.channel_name.trim().trim_start_matches('#').to_lowercase();
    if user.role != "admin" && user.channel_name != clean {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "You can only modify your own channel instance.".to_string(),
                result: None,
            }),
        );
    }

    let mut join_changed: Option<bool> = None;

    {
        let mut chans = state.channels.write().unwrap();
        if let Some(rt) = chans.get_mut(&clean) {
            if rt.record.join_enabled != req.join_enabled {
                join_changed = Some(req.join_enabled);
            }
            rt.record.join_enabled = req.join_enabled;
            rt.record.voice_enabled = req.voice_enabled;
            rt.record.learn_enabled = req.learn_enabled;
            // Only admin can enable contribute_to_global if disabled
            if user.role == "admin" {
                rt.record.contribute_to_global = req.contribute_to_global;
            }
            rt.record.lines_between_messages = req.lines_between_messages;
            rt.record.time_between_messages = req.time_between_messages;
            rt.record.markov_order = req.markov_order.clamp(1, 3);
            rt.record.temperature = req.temperature.clamp(0.25, 3.0);
            rt.record.context_bias_chance = req.context_bias_chance.min(100);
            rt.record.reply_mention_chance = req.reply_mention_chance.min(100);
            rt.record.tts_enabled = req.tts_enabled;
            rt.record.tts_delay_enabled = req.tts_delay_enabled;
            rt.record.voice_preset = req.voice_preset;
            if !req.brain_mix.is_empty() {
                rt.record.brain_mix = req.brain_mix;
            }
            if user.role == "admin" {
                rt.record.custom_learn_brains = req
                    .custom_learn_brains
                    .into_iter()
                    .map(|s| s.trim().to_lowercase())
                    .filter(|s| !s.is_empty())
                    .collect();
            }
            rt.record.trusted_users = req
                .trusted_users
                .into_iter()
                .map(|s| s.to_lowercase())
                .collect();
            rt.record.ignored_users = req
                .ignored_users
                .into_iter()
                .map(|s| s.to_lowercase())
                .collect();
            rt.record.blocked_words = req
                .blocked_words
                .into_iter()
                .map(|s| s.to_lowercase())
                .collect();
            rt.record.command_cooldown_secs = req.command_cooldown_secs.min(3600);
            rt.record.only_when_live = req.only_when_live;
            rt.record.raid_greet_enabled = req.raid_greet_enabled;
            rt.record.sub_greet_enabled = req.sub_greet_enabled;
            rt.record.reward_tts_id = req.reward_tts_id.trim().to_string();
            rt.record.reward_markov_id = req.reward_markov_id.trim().to_string();
            rt.record.bark_cursedness = req.bark_cursedness.min(100);
            rt.record.bark_text_temp = req.bark_text_temp.clamp(0.2, 1.5);
            rt.record.bark_waveform_temp = req.bark_waveform_temp.clamp(0.2, 1.5);
            rt.record.bark_drift_enabled = req.bark_drift_enabled;
            rt.record.trigger_keywords = req
                .trigger_keywords
                .into_iter()
                .map(|s| s.trim().to_lowercase())
                .filter(|s| !s.is_empty())
                .collect();
            rt.record.trigger_chance = req.trigger_chance.min(100);
            rt.record.custom_commands = req
                .custom_commands
                .into_iter()
                .filter(|c| !c.cmd.trim().is_empty())
                .collect();
            rt.record.min_bits_tts = req.min_bits_tts;
            rt.record.min_bits_markov = req.min_bits_markov;
            rt.record.obs_key = req.obs_key.trim().to_string();
            rt.record.obs_idle_img = req.obs_idle_img.trim().to_string();
            rt.record.obs_talk_img = req.obs_talk_img.trim().to_string();
            rt.record.private_brain = req.private_brain;
            if user.role == "admin" {
                if let Some(ref bid) = req.bot_identity {
                    let clean_bid = if bid == "ansv" { "ansv" } else { "mockbot" }.to_string();
                    if rt.record.bot_identity != clean_bid {
                        rt.record.bot_identity = clean_bid;
                        if rt.record.join_enabled {
                            join_changed = Some(true);
                        }
                    }
                }
            }
            let _ = state.db.upsert_channel(&rt.record);
        } else {
            return (
                StatusCode::NOT_FOUND,
                Json(ApiResponse {
                    ok: false,
                    message: format!("Channel #{} not found", clean),
                    result: None,
                }),
            );
        }
    }

    if let Some(should_join) = join_changed {
        if should_join {
            let _ = state.irc_cmd_tx.send(IrcControlCmd::Join(clean.clone()));
        } else {
            let _ = state.irc_cmd_tx.send(IrcControlCmd::Part(clean.clone()));
        }
    }

    state.log_system(
        "INFO",
        format!("Updated configuration for #{} (by @{})", clean, user.username),
    );
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("Saved settings for #{}.", clean),
            result: None,
        }),
    )
}

async fn admin_delete_channel_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<JoinPartReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin required.".to_string(),
                result: None,
            }),
        );
    }
    match state.part_channel(&req.channel, true) {
        Ok(()) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Removed channel #{}.", req.channel.trim_start_matches('#')),
                result: None,
            }),
        ),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

async fn admin_backup_db_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return StatusCode::UNAUTHORIZED.into_response();
    };
    if user.role != "admin" {
        return StatusCode::FORBIDDEN.into_response();
    }
    let tmp_path = std::path::PathBuf::from("/tmp/ansv2_backup.db");
    if state.db.backup_db_snapshot(&tmp_path).is_err() {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }
    match tokio::fs::read(&tmp_path).await {
        Ok(bytes) => {
            let _ = tokio::fs::remove_file(&tmp_path).await;
            let date_str = chrono::Local::now().format("%Y%m%d-%H%M%S");
            Response::builder()
                .status(StatusCode::OK)
                .header(header::CONTENT_TYPE, "application/octet-stream")
                .header(
                    header::CONTENT_DISPOSITION,
                    format!("attachment; filename=\"ansv2-backup-{}.db\"", date_str),
                )
                .body(Body::from(bytes))
                .unwrap()
        }
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

#[derive(Deserialize)]
struct DeleteTtsLogReq {
    id: i64,
}

async fn tts_delete_log_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<DeleteTtsLogReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    if let Ok(Some(row)) = state.db.get_tts_log_by_id(req.id) {
        if user.role != "admin" && row.channel_name != user.channel_name {
            return (
                StatusCode::FORBIDDEN,
                Json(ApiResponse {
                    ok: false,
                    message: "Permission denied.".to_string(),
                    result: None,
                }),
            );
        }
    }

    match state.db.delete_tts_log(req.id) {
        Ok(Some(row)) => {
            let wav_path = format!("/home/matt/ansv2/{}", row.file_path);
            let npz_path = format!("{}.npz", wav_path);
            let _ = std::fs::remove_file(&wav_path);
            let _ = std::fs::remove_file(&npz_path);
            (
                StatusCode::OK,
                Json(ApiResponse {
                    ok: true,
                    message: format!("Deleted TTS clip #{}.", req.id),
                    result: None,
                }),
            )
        }
        _ => (
            StatusCode::NOT_FOUND,
            Json(ApiResponse {
                ok: false,
                message: "Clip not found.".to_string(),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct SetBrainVoiceReq {
    brain: String,
    voice: String,
}

async fn admin_set_brain_voice_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<SetBrainVoiceReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let target_brain = if user.role == "admin" {
        req.brain.clone()
    } else {
        format!("channel:{}", user.channel_name)
    };
    let _ = state.db.set_brain_voice(&target_brain, req.voice.trim());
    {
        let mut eng = state.engine.write().unwrap();
        if let Some(b) = eng.brains.get_mut(&target_brain) {
            b.default_voice = req.voice.trim().to_string();
        }
    }
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("Assigned voice '{}' to {}.", req.voice.trim(), target_brain),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct SpeakReq {
    channel: String,
    seed: Option<String>,
}

async fn speak_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<SpeakReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let clean = req.channel.trim().trim_start_matches('#').to_lowercase();
    if user.role != "admin" && user.channel_name != clean {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "You can only trigger speech in your own channel.".to_string(),
                result: None,
            }),
        );
    }

    match state.speak_in_channel(&clean, "web_studio", req.seed) {
        Some(res) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Sent to #{}: {}", clean, res.text),
                result: None,
            }),
        ),
        None => (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Failed to generate message for channel.".to_string(),
                result: None,
            }),
        ),
    }
}

async fn admin_irc_cmd_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(cmd): Json<IrcControlCmd>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin role required".to_string(),
                result: None,
            }),
        );
    }
    let _ = state.irc_cmd_tx.send(cmd);
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: "Forwarded IRC command".to_string(),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct AdminTrainBrainReq {
    brain: String,
    lines: Vec<String>,
}

async fn admin_train_brain_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<AdminTrainBrainReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let raw_name = req.brain.trim().to_lowercase();
    let brain_name = if user.role != "admin" {
        format!("channel:{}", user.channel_name)
    } else if raw_name == "global"
        || raw_name.starts_with("channel:")
        || raw_name.starts_with("custom:")
    {
        raw_name
    } else {
        format!("custom:{}", raw_name)
    };

    let kind = if brain_name == "global" {
        BrainKind::Global
    } else if brain_name.starts_with("channel:") {
        BrainKind::Channel
    } else {
        BrainKind::Custom
    };

    let _ = state
        .db
        .upsert_brain_meta(&brain_name, kind, "Custom trained corpus");

    let mut batch = Vec::new();
    let mut learned_count = 0usize;
    {
        let mut eng = state.engine.write().unwrap();
        eng.ensure_brain(&brain_name, kind, "Custom trained corpus");
        let targets = [brain_name.clone()];
        for line in &req.lines {
            if let Some(cleaned) = eng.learn_sentence(line, &targets) {
                batch.push((
                    brain_name.clone(),
                    user.username.clone(),
                    user.username.clone(),
                    cleaned,
                ));
                learned_count += 1;
            }
        }
    }

    if !batch.is_empty() {
        let _ = state.db.insert_corpus_batch(&batch);
    }

    state.log_system(
        "INFO",
        format!(
            "Trained {} lines into brain '{}' (by @{})",
            learned_count, brain_name, user.username
        ),
    );
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!(
                "Trained {} valid sentences into brain '{}'.",
                learned_count, brain_name
            ),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct CustomImportReq {
    brain: Option<String>,
    format: String, // "auto", "lines", "sentences", "chatlog", "json"
    raw_text: String,
}

async fn custom_import_corpus_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<CustomImportReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    if user.role != "admin" && !user.verified {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: format!(
                    "Unverified channel. Type '!ansv claim {}' in #{} first.",
                    user.claim_code, user.channel_name
                ),
                result: None,
            }),
        );
    }

    let raw_brain = req.brain.unwrap_or_default().trim().to_lowercase();
    let brain_name = if user.role != "admin" {
        format!("channel:{}", user.channel_name)
    } else if raw_brain.is_empty() {
        "global".to_string()
    } else if raw_brain == "global"
        || raw_brain.starts_with("channel:")
        || raw_brain.starts_with("custom:")
    {
        raw_brain
    } else {
        format!("custom:{}", raw_brain)
    };

    let kind = if brain_name == "global" {
        BrainKind::Global
    } else if brain_name.starts_with("channel:") {
        BrainKind::Channel
    } else {
        BrainKind::Custom
    };
    let origin_channel = brain_name
        .strip_prefix("channel:")
        .or_else(|| brain_name.strip_prefix("custom:"))
        .unwrap_or(&user.username)
        .to_string();

    let _ = state
        .db
        .upsert_brain_meta(&brain_name, kind, "Custom imported corpus");

    // Parse (author, text) pairs according to requested format
    let pairs = parse_custom_import_text(&req.raw_text, &req.format, &user.username);
    if pairs.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "No valid text lines found in input.".to_string(),
                result: None,
            }),
        );
    }

    let mut batch = Vec::new();
    let mut unique_authors = std::collections::HashSet::new();
    let mut learned_count = 0usize;

    {
        let mut eng = state.engine.write().unwrap();
        eng.ensure_brain(&brain_name, kind, "Custom imported corpus");
        let targets = [brain_name.clone()];
        for (author, raw_line) in pairs {
            if let Some(cleaned) = eng.learn_sentence(&raw_line, &targets) {
                unique_authors.insert(author.clone());
                batch.push((brain_name.clone(), origin_channel.clone(), author, cleaned));
                learned_count += 1;
            }
        }
    }

    if !batch.is_empty() {
        let _ = state.db.insert_corpus_batch(&batch);
    }

    state.log_system(
        "INFO",
        format!(
            "Imported {} sentences into '{}' (by @{})",
            learned_count, brain_name, user.username
        ),
    );
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!(
                "Imported {} sentences ({} authors) into '{}'.",
                learned_count,
                unique_authors.len(),
                brain_name
            ),
            result: None,
        }),
    )
}

fn parse_custom_import_text(
    raw: &str,
    format: &str,
    default_author: &str,
) -> Vec<(String, String)> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }

    let fmt = if format == "auto" {
        if (trimmed.starts_with('{') && trimmed.ends_with('}'))
            || (trimmed.starts_with('[') && trimmed.ends_with(']'))
        {
            "json"
        } else if trimmed
            .lines()
            .take(10)
            .filter(|l| l.contains(": "))
            .count()
            >= 4
        {
            "chatlog"
        } else {
            "lines"
        }
    } else {
        format
    };

    let mut out = Vec::new();
    match fmt {
        "json" => {
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(trimmed) {
                // Case 1: TwitchDownloaderCLI format: {"comments": [{"commenter": {"name": "..."}, "message": {"body": "..."}}]}
                if let Some(comments) = val.get("comments").and_then(|v| v.as_array()) {
                    for c in comments {
                        let author = c
                            .get("commenter")
                            .and_then(|cm| cm.get("name").or_else(|| cm.get("display_name")))
                            .and_then(|v| v.as_str())
                            .unwrap_or(default_author)
                            .to_lowercase();
                        if let Some(body) = c
                            .get("message")
                            .and_then(|m| m.get("body"))
                            .and_then(|v| v.as_str())
                        {
                            out.push((author, body.to_string()));
                        }
                    }
                } else if let Some(arr) = val.as_array() {
                    // Case 2: Array of strings or objects [{"author": "...", "message": "..."}]
                    for item in arr {
                        if let Some(s) = item.as_str() {
                            out.push((default_author.to_string(), s.to_string()));
                        } else if let Some(obj) = item.as_object() {
                            let author = obj
                                .get("author")
                                .or_else(|| obj.get("user"))
                                .or_else(|| obj.get("username"))
                                .and_then(|v| v.as_str())
                                .unwrap_or(default_author)
                                .to_lowercase();
                            if let Some(msg) = obj
                                .get("message")
                                .or_else(|| obj.get("text"))
                                .or_else(|| obj.get("content"))
                                .and_then(|v| v.as_str())
                            {
                                out.push((author, msg.to_string()));
                            }
                        }
                    }
                }
            }
        }
        "chatlog" => {
            // Supports:
            // [2026-05-10 12:34:56] username: message
            // [12:34] <username> message
            // username: message
            for line in trimmed.lines() {
                let mut l = line.trim();
                if l.is_empty() {
                    continue;
                }
                // Strip leading [timestamp] if present
                while l.starts_with('[') {
                    if let Some(idx) = l.find(']') {
                        l = l[idx + 1..].trim_start();
                    } else {
                        break;
                    }
                }
                // Strip leading #channel if present
                if l.starts_with('#') {
                    if let Some((_, rest)) = l.split_once(' ') {
                        l = rest.trim_start();
                    }
                }
                if l.starts_with('<') {
                    if let Some((user, msg)) = l[1..].split_once('>') {
                        let clean_user = user.trim().trim_start_matches(&['@', '+', '%', '~'][..]).to_lowercase();
                        out.push((clean_user, msg.trim().to_string()));
                        continue;
                    }
                }
                if let Some((user, msg)) = l.split_once(": ") {
                    let clean_user = user.trim().trim_start_matches('@').to_lowercase();
                    if !clean_user.is_empty()
                        && clean_user.len() <= 25
                        && clean_user
                            .chars()
                            .all(|c| c.is_ascii_alphanumeric() || c == '_')
                    {
                        out.push((clean_user, msg.trim().to_string()));
                        continue;
                    }
                }
                out.push((default_author.to_string(), l.to_string()));
            }
        }
        "sentences" => {
            // Split paragraphs/prose on sentence-ending punctuation (. ! ? \n)
            let mut cur = String::new();
            for ch in trimmed.chars() {
                if ch == '\n' {
                    if !cur.trim().is_empty() {
                        out.push((default_author.to_string(), cur.trim().to_string()));
                        cur.clear();
                    }
                } else {
                    cur.push(ch);
                    if matches!(ch, '.' | '!' | '?') && cur.split_whitespace().count() >= 3 {
                        out.push((default_author.to_string(), cur.trim().to_string()));
                        cur.clear();
                    }
                }
            }
            if !cur.trim().is_empty() {
                out.push((default_author.to_string(), cur.trim().to_string()));
            }
        }
        _ => {
            for line in trimmed.lines() {
                let l = line.trim();
                if !l.is_empty() {
                    out.push((default_author.to_string(), l.to_string()));
                }
            }
        }
    }
    out
}

#[derive(Deserialize)]
struct ChattersQuery {
    channel: Option<String>,
}

async fn top_chatters_handler(
    headers: HeaderMap,
    Query(q): Query<ChattersQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<Vec<(String, usize)>> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let filter = if user.role == "admin" {
        q.channel.as_deref()
    } else {
        Some(user.channel_name.as_str())
    };
    let list = state.db.top_chatters(filter, 30).unwrap_or_default();
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("{} chatters", list.len()),
            result: Some(list),
        }),
    )
}

#[derive(Deserialize)]
struct MockChatterReq {
    username: String,
    channel: Option<String>,
}

async fn mock_chatter_preview_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<MockChatterReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<GenerationResult> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let clean_chatter = req.username.trim().trim_start_matches('@').to_lowercase();
    let lines = state.db.get_author_lines(&clean_chatter, 500).unwrap_or_default();
    if lines.len() < 2 {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: format!("Not enough recorded lines for @{} yet.", clean_chatter),
                result: None,
            }),
        );
    }
    let user_brain_name = format!("user:{}", clean_chatter);
    {
        let mut eng = state.engine.write().unwrap();
        eng.ensure_brain(
            &user_brain_name,
            BrainKind::Custom,
            &format!("Chatter brain for @{}", clean_chatter),
        );
        let targets = [user_brain_name.clone()];
        for l in &lines {
            eng.learn_sanitized(l, &targets);
        }
    }
    let chan = req
        .channel
        .unwrap_or_else(|| user.channel_name.clone())
        .trim()
        .trim_start_matches('#')
        .to_lowercase();
    let params = GenerationParams {
        brains: vec![
            BrainWeight {
                brain: user_brain_name,
                weight: 0.85,
            },
            BrainWeight {
                brain: if chan.is_empty() || chan == "*" {
                    "global".to_string()
                } else {
                    format!("channel:{}", chan)
                },
                weight: 0.15,
            },
        ],
        order: 2,
        temperature: 1.0,
        seed: None,
        min_words: 2,
        max_words: 35,
        max_chars: 450,
        max_overlap_ratio: 0.80,
        max_overlap_total: 18,
        max_attempts: 35,
        recent_outputs: Vec::new(),
        blocked_words: Vec::new(),
    };
    let res_opt = {
        let eng = state.engine.read().unwrap();
        eng.generate(&params)
    };
    match res_opt {
        Some(res) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("@{}: {}", clean_chatter, res.text),
                result: Some(res),
            }),
        ),
        None => (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: format!("Could not generate sentence for @{}.", clean_chatter),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct BrainLinesQuery {
    brain: String,
    q: Option<String>,
    limit: Option<usize>,
}

async fn search_brain_lines_handler(
    headers: HeaderMap,
    Query(query): Query<BrainLinesQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<Vec<CorpusLineRow>> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let target_brain = if user.role == "admin" {
        query.brain.clone()
    } else {
        format!("channel:{}", user.channel_name)
    };

    match state.db.search_corpus_lines(
        &target_brain,
        query.q.as_deref(),
        query.limit.unwrap_or(50),
    ) {
        Ok(rows) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Found {} lines in {}", rows.len(), target_brain),
                result: Some(rows),
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

async fn export_brain_lines_handler(
    headers: HeaderMap,
    Query(query): Query<BrainLinesQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return StatusCode::UNAUTHORIZED.into_response();
    };
    let target_brain = if user.role == "admin" {
        query.brain.clone()
    } else {
        format!("channel:{}", user.channel_name)
    };
    let lines = state.db.get_brain_lines(&target_brain).unwrap_or_default();
    let body_text = lines.join("\n");
    let safe_fname = target_brain.replace(':', "_");
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"{}.txt\"", safe_fname),
        )
        .body(Body::from(body_text))
        .unwrap()
}

#[derive(Deserialize)]
struct DeleteLineReq {
    id: i64,
}

#[derive(Deserialize)]
struct DeleteBrainQueryReq {
    brain: String,
    query: String,
}

async fn delete_brain_query_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<DeleteBrainQueryReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    let target_brain = if user.role == "admin" {
        req.brain.trim().to_string()
    } else {
        format!("channel:{}", user.channel_name)
    };

    match state
        .db
        .delete_matching_query_in_brain(&target_brain, &req.query)
    {
        Ok(deleted) => {
            if deleted > 0 {
                let _ = state.rebuild_brain_from_db(&target_brain);
            }
            (
                StatusCode::OK,
                Json(ApiResponse {
                    ok: true,
                    message: format!(
                        "Deleted {} matching lines from '{}' and rebuilt brain.",
                        deleted, target_brain
                    ),
                    result: None,
                }),
            )
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

async fn delete_brain_line_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<DeleteLineReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };

    match state.db.delete_corpus_line(req.id) {
        Ok(Some(brain_name)) => {
            if user.role != "admin" && brain_name != format!("channel:{}", user.channel_name) {
                return (
                    StatusCode::FORBIDDEN,
                    Json(ApiResponse {
                        ok: false,
                        message: "Permission denied.".to_string(),
                        result: None,
                    }),
                );
            }
            let rebuilt = state.rebuild_brain_from_db(&brain_name).unwrap_or(0);
            (
                StatusCode::OK,
                Json(ApiResponse {
                    ok: true,
                    message: format!(
                        "Pruned line #{} and rebuilt '{}' ({} sentences).",
                        req.id, brain_name, rebuilt
                    ),
                    result: None,
                }),
            )
        }
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(ApiResponse {
                ok: false,
                message: "Line not found".to_string(),
                result: None,
            }),
        ),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct DeleteBrainReq {
    brain: String,
}

async fn admin_delete_brain_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<DeleteBrainReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" || req.brain == "global" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Cannot delete this brain.".to_string(),
                result: None,
            }),
        );
    }

    let deleted_rows = state.db.delete_brain(&req.brain).unwrap_or(0);
    {
        let mut eng = state.engine.write().unwrap();
        eng.brains.remove(&req.brain);
    }
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("Deleted brain '{}' ({} lines removed).", req.brain, deleted_rows),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct MergeBrainReq {
    source_brain: String,
    target_brain: String,
}

async fn admin_merge_brain_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<MergeBrainReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin required".to_string(),
                result: None,
            }),
        );
    }
    let inserted = state
        .db
        .merge_brain_corpus(&req.source_brain, &req.target_brain)
        .unwrap_or(0);
    let rebuilt = state.rebuild_brain_from_db(&req.target_brain).unwrap_or(0);
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!(
                "Merged {} new lines from '{}' into '{}' (now {} total lines).",
                inserted, req.source_brain, req.target_brain, rebuilt
            ),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct PurgeOriginReq {
    channel_origin: String,
}

async fn admin_purge_origin_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<PurgeOriginReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin required".to_string(),
                result: None,
            }),
        );
    }
    let deleted = state
        .db
        .purge_channel_from_global(&req.channel_origin)
        .unwrap_or(0);
    let rebuilt = state.rebuild_brain_from_db("global").unwrap_or(0);
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!(
                "Purged {} lines from '{}' out of global brain ({} lines remaining).",
                deleted, req.channel_origin, rebuilt
            ),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct TrimBrainReq {
    brain: String,
    keep_newest: usize,
}

async fn admin_trim_brain_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<TrimBrainReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    let target_brain = if user.role == "admin" {
        req.brain.clone()
    } else {
        format!("channel:{}", user.channel_name)
    };
    let deleted = state
        .db
        .trim_brain_to_newest(&target_brain, req.keep_newest)
        .unwrap_or(0);
    let rebuilt = state.rebuild_brain_from_db(&target_brain).unwrap_or(0);
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!(
                "Trimmed '{}' to newest {} lines ({} older lines removed).",
                target_brain, rebuilt, deleted
            ),
            result: None,
        }),
    )
}

async fn admin_list_users_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<Vec<UserAccount>> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin required".to_string(),
                result: None,
            }),
        );
    }
    let list = state.db.list_users().unwrap_or_default();
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("{} accounts", list.len()),
            result: Some(list),
        }),
    )
}

#[derive(Deserialize)]
struct UpsertUserReq {
    username: String,
    #[serde(default)]
    password: String,
    #[serde(default = "default_streamer_role")]
    role: String,
    #[serde(default = "default_pro_tier")]
    tier: String,
    #[serde(default)]
    channel_name: String,
}

fn default_streamer_role() -> String {
    "streamer".to_string()
}
fn default_pro_tier() -> String {
    "pro".to_string()
}

async fn admin_upsert_user_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<UpsertUserReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<UserAccount> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin required".to_string(),
                result: None,
            }),
        );
    }

    let clean_user = req
        .username
        .trim()
        .trim_start_matches('@')
        .trim_start_matches('#')
        .to_lowercase();
    if clean_user.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: "Username cannot be empty.".to_string(),
                result: None,
            }),
        );
    }

    let target_chan = if req.role == "admin" {
        "*".to_string()
    } else {
        let ch = req
            .channel_name
            .trim()
            .trim_start_matches('@')
            .trim_start_matches('#')
            .to_lowercase();
        if ch.is_empty() {
            clean_user.clone()
        } else {
            ch
        }
    };

    if req.role == "streamer" && !target_chan.is_empty() && target_chan != "*" {
        let _ = state.add_or_join_channel(&target_chan, "admin", false);
    }
    match state.db.create_or_update_user_full(
        &clean_user,
        &req.password,
        &req.role,
        &req.tier,
        &target_chan,
        true,
        "",
    ) {
        Ok(acct) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!(
                    "Saved user @{} ({} [{}] -> #{})",
                    acct.username, acct.role, acct.tier, acct.channel_name
                ),
                result: Some(acct),
            }),
        ),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

#[derive(Deserialize)]
struct DeleteUserReq {
    username: String,
}

async fn admin_delete_user_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<DeleteUserReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Unauthorized".to_string(),
                result: None,
            }),
        );
    };
    if user.role != "admin" {
        return (
            StatusCode::FORBIDDEN,
            Json(ApiResponse {
                ok: false,
                message: "Admin required".to_string(),
                result: None,
            }),
        );
    }
    let _ = state.db.delete_user(&req.username);
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            message: format!("Deleted user @{}", req.username),
            result: None,
        }),
    )
}

#[derive(Deserialize)]
struct JoinPartReq {
    channel: String,
}

async fn public_join_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<JoinPartReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<crate::db::ChannelRecord> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let target = if user.role == "admin" {
        req.channel.clone()
    } else {
        user.channel_name.clone()
    };
    match state.add_or_join_channel(&target, &user.role, user.role == "admin") {
        Ok(rec) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Connected bot to #{}!", rec.channel_name),
                result: Some(rec),
            }),
        ),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}

async fn public_part_handler(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(req): Json<JoinPartReq>,
) -> impl IntoResponse {
    let Some(user) = resolve_auth_user(&headers, &state) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(ApiResponse::<()> {
                ok: false,
                message: "Sign in required.".to_string(),
                result: None,
            }),
        );
    };
    let target = if user.role == "admin" {
        req.channel.clone()
    } else {
        user.channel_name.clone()
    };
    match state.part_channel(&target, false) {
        Ok(()) => (
            StatusCode::OK,
            Json(ApiResponse {
                ok: true,
                message: format!("Bot parted #{}.", target),
                result: None,
            }),
        ),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(ApiResponse {
                ok: false,
                message: e.to_string(),
                result: None,
            }),
        ),
    }
}
