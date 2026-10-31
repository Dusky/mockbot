pub mod commands;
pub mod parser;

use crate::state::{AppState, IrcControlCmd, LiveEvent};
use commands::{try_handle_command, ChatCommandCtx};
use parser::{parse_irc_line, IrcEvent};

use anyhow::Result;
use rand::Rng;
use rustls::pki_types::ServerName;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::mpsc;
use tokio_rustls::{rustls, TlsConnector};
use tracing::{error, info, warn};

const TWITCH_IRC_HOST: &str = "irc.chat.twitch.tv";
const TWITCH_IRC_PORT: u16 = 6697;

pub async fn run_twitch_loop(
    state: Arc<AppState>,
    mut cmd_rx: mpsc::UnboundedReceiver<IrcControlCmd>,
) {
    let live_poll_state = state.clone();
    tokio::spawn(async move {
        poll_live_streams_loop(live_poll_state).await;
    });

    let has_mockbot_token = !state.config.twitch.mockbot_oauth_token.trim().is_empty()
        && state.config.twitch.mockbot_oauth_token.trim() != "oauth:your_token_here";

    if has_mockbot_token {
        let (ansv_tx, ansv_rx) = mpsc::unbounded_channel::<IrcControlCmd>();
        let (mock_tx, mock_rx) = mpsc::unbounded_channel::<IrcControlCmd>();

        let s_ansv = state.clone();
        let ansv_nick = state.config.twitch.nickname.clone();
        let ansv_tok = state.config.twitch.oauth_token.clone();
        tokio::spawn(async move {
            run_bot_connection_loop(s_ansv, Some("ansv"), ansv_nick, ansv_tok, true, ansv_rx).await;
        });

        let s_mock = state.clone();
        let mock_nick = state.config.twitch.mockbot_nickname.clone();
        let mock_tok = state.config.twitch.mockbot_oauth_token.clone();
        tokio::spawn(async move {
            run_bot_connection_loop(
                s_mock,
                Some("mockbot"),
                mock_nick,
                mock_tok,
                false,
                mock_rx,
            )
            .await;
        });

        while let Some(ctrl) = cmd_rx.recv().await {
            match &ctrl {
                IrcControlCmd::Join(ch) => {
                    let clean = ch.trim_start_matches('#').to_lowercase();
                    let id = {
                        let chans = state.channels.read().unwrap();
                        chans
                            .get(&clean)
                            .map(|rt| rt.record.bot_identity.clone())
                            .unwrap_or_else(|| "mockbot".to_string())
                    };
                    if id == "ansv" {
                        let _ = mock_tx.send(IrcControlCmd::Part(clean.clone()));
                        let _ = ansv_tx.send(IrcControlCmd::Join(clean));
                    } else {
                        let _ = ansv_tx.send(IrcControlCmd::Part(clean.clone()));
                        let _ = mock_tx.send(IrcControlCmd::Join(clean));
                    }
                }
                IrcControlCmd::Part(ch) => {
                    let clean = ch.trim_start_matches('#').to_lowercase();
                    let _ = ansv_tx.send(IrcControlCmd::Part(clean.clone()));
                    let _ = mock_tx.send(IrcControlCmd::Part(clean));
                }
                IrcControlCmd::SendPrivmsg { channel, text } => {
                    let clean = channel.trim_start_matches('#').to_lowercase();
                    let id = {
                        let chans = state.channels.read().unwrap();
                        chans
                            .get(&clean)
                            .map(|rt| rt.record.bot_identity.clone())
                            .unwrap_or_else(|| "mockbot".to_string())
                    };
                    if id == "ansv" {
                        let _ = ansv_tx.send(IrcControlCmd::SendPrivmsg {
                            channel: clean,
                            text: text.clone(),
                        });
                    } else {
                        let _ = mock_tx.send(IrcControlCmd::SendPrivmsg {
                            channel: clean,
                            text: text.clone(),
                        });
                    }
                }
            }
        }
    } else {
        let ansv_nick = state.config.twitch.nickname.clone();
        let ansv_tok = state.config.twitch.oauth_token.clone();
        run_bot_connection_loop(state, None, ansv_nick, ansv_tok, true, cmd_rx).await;
    }
}

async fn run_bot_connection_loop(
    state: Arc<AppState>,
    identity_filter: Option<&'static str>,
    bot_nick: String,
    bot_token: String,
    run_cadence: bool,
    mut cmd_rx: mpsc::UnboundedReceiver<IrcControlCmd>,
) {
    let mut backoff_secs = 3u64;

    loop {
        if bot_token.is_empty() || bot_token == "oauth:your_token_here" {
            state.log_system(
                "WARN",
                format!(
                    "Twitch OAuth token for @{} is not configured in config.toml.",
                    bot_nick
                ),
            );
            tokio::time::sleep(Duration::from_secs(15)).await;
            continue;
        }

        state.log_system(
            "INFO",
            format!(
                "Connecting @{} to Twitch IRC over TLS (irc.chat.twitch.tv:6697)...",
                bot_nick
            ),
        );

        match connect_and_run(
            &state,
            identity_filter,
            &bot_nick,
            &bot_token,
            run_cadence,
            &mut cmd_rx,
        )
        .await
        {
            Ok(()) => {
                if run_cadence {
                    state.irc_connected.store(false, Ordering::Relaxed);
                }
                state.log_system(
                    "INFO",
                    format!("Twitch IRC (@{}) closed cleanly. Reconnecting...", bot_nick),
                );
                backoff_secs = 3;
            }
            Err(e) => {
                if run_cadence {
                    state.irc_connected.store(false, Ordering::Relaxed);
                }
                state.log_system(
                    "ERROR",
                    format!(
                        "Twitch IRC (@{}) error: {}. Reconnecting in {}s...",
                        bot_nick, e, backoff_secs
                    ),
                );
                error!("Twitch IRC (@{}) error: {}", bot_nick, e);
                tokio::time::sleep(Duration::from_secs(backoff_secs)).await;
                backoff_secs = (backoff_secs * 2).min(60);
            }
        }
    }
}

async fn connect_and_run(
    state: &Arc<AppState>,
    identity_filter: Option<&'static str>,
    bot_nick: &str,
    bot_token: &str,
    run_cadence: bool,
    cmd_rx: &mut mpsc::UnboundedReceiver<IrcControlCmd>,
) -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = rustls::ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();
    let connector = TlsConnector::from(Arc::new(tls_config));

    let tcp = TcpStream::connect((TWITCH_IRC_HOST, TWITCH_IRC_PORT)).await?;
    let domain = ServerName::try_from(TWITCH_IRC_HOST)?;
    let tls_stream = connector.connect(domain, tcp).await?;

    let (read_half, mut write_half) = tokio::io::split(tls_stream);
    let mut reader = BufReader::new(read_half).lines();

    let mut token = bot_token.trim().to_string();
    if !token.starts_with("oauth:") {
        token = format!("oauth:{}", token);
    }
    let nick = bot_nick.trim().to_lowercase();

    write_half
        .write_all(b"CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership\r\n")
        .await?;
    write_half
        .write_all(format!("PASS {}\r\nNICK {}\r\n", token, nick).as_bytes())
        .await?;
    write_half.flush().await?;

    let mut cadence_interval = tokio::time::interval(Duration::from_secs(30));
    let mut ping_interval = tokio::time::interval(Duration::from_secs(180));
    ping_interval.tick().await; // skip immediate tick

    loop {
        tokio::select! {
            line_res = reader.next_line() => {
                let Some(line) = line_res? else {
                    return Ok(());
                };
                match parse_irc_line(&line) {
                    IrcEvent::Ping(payload) => {
                        write_half
                            .write_all(format!("PONG {}\r\n", payload).as_bytes())
                            .await?;
                        write_half.flush().await?;
                    }
                    IrcEvent::Welcome => {
                        state.irc_connected.store(true, Ordering::Relaxed);
                        state.log_system("INFO", format!("Authenticated with Twitch IRC as @{}", nick));
                        info!("Authenticated with Twitch IRC as @{}", nick);

                        // Join all configured join_enabled channels matching this bot identity
                        let to_join: Vec<String> = {
                            let chans = state.channels.read().unwrap();
                            chans
                                .values()
                                .filter(|rt| {
                                    rt.record.join_enabled
                                        && match identity_filter {
                                            Some(id) => rt.record.bot_identity == id,
                                            None => true,
                                        }
                                })
                                .map(|rt| rt.record.channel_name.clone())
                                .collect()
                        };

                        for ch in to_join {
                            write_half
                                .write_all(format!("JOIN #{}\r\n", ch).as_bytes())
                                .await?;
                            write_half.flush().await?;
                            tokio::time::sleep(Duration::from_millis(120)).await;
                        }
                    }
                    IrcEvent::Reconnect => {
                        state.log_system("WARN", "Twitch requested IRC RECONNECT");
                        return Ok(());
                    }
                    IrcEvent::Join { nick: jnick, channel } => {
                        if jnick == nick {
                            {
                                let mut chans = state.channels.write().unwrap();
                                if let Some(rt) = chans.get_mut(&channel) {
                                    rt.currently_joined = true;
                                }
                            }
                            state.emit_event(LiveEvent::ChannelStatusChanged {
                                channel: channel.clone(),
                                joined: true,
                            });
                            state.log_system("INFO", format!("Joined Twitch channel #{}", channel));
                        }
                    }
                    IrcEvent::Part { nick: pnick, channel } => {
                        if pnick == nick {
                            {
                                let mut chans = state.channels.write().unwrap();
                                if let Some(rt) = chans.get_mut(&channel) {
                                    rt.currently_joined = false;
                                }
                            }
                            state.emit_event(LiveEvent::ChannelStatusChanged {
                                channel: channel.clone(),
                                joined: false,
                            });
                        }
                    }
                    IrcEvent::Notice { channel, message } => {
                        state.log_system("WARN", format!("Notice [{}]: {}", channel, message));
                        if message.to_lowercase().contains("login authentication failed") {
                            warn!("Twitch login authentication failed; check oauth_token in config.toml");
                            tokio::time::sleep(Duration::from_secs(30)).await;
                            return Ok(());
                        }
                    }
                    IrcEvent::Raid {
                        channel,
                        raider,
                        viewers,
                    } => {
                        state.log_system(
                            "INFO",
                            format!("Raid in #{}: @{} with {} viewers", channel, raider, viewers),
                        );
                        let greet = {
                            let chans = state.channels.read().unwrap();
                            chans
                                .get(&channel)
                                .map(|rt| rt.record.voice_enabled && rt.record.raid_greet_enabled)
                                .unwrap_or(false)
                        };
                        if greet {
                            let _ = state.speak_in_channel(
                                &channel,
                                &format!("raid:@{}", raider),
                                Some(raider),
                            );
                        }
                    }
                    IrcEvent::Sub {
                        channel,
                        subscriber,
                        message,
                    } => {
                        state.log_system(
                            "INFO",
                            format!("Sub in #{}: @{}", channel, subscriber),
                        );
                        let greet = {
                            let chans = state.channels.read().unwrap();
                            chans
                                .get(&channel)
                                .map(|rt| rt.record.voice_enabled && rt.record.sub_greet_enabled)
                                .unwrap_or(false)
                        };
                        if greet {
                            let seed = message.or(Some(subscriber.clone()));
                            let _ = state.speak_in_channel(
                                &channel,
                                &format!("sub:@{}", subscriber),
                                seed,
                            );
                        }
                    }
                    IrcEvent::ClearChat {
                        channel,
                        target_user,
                    } => {
                        if let Some(banned_user) = target_user {
                            if let Ok(purged) = state
                                .db
                                .purge_author_lines_in_channel(&channel, &banned_user)
                            {
                                if purged > 0 {
                                    let chan_brain = format!("channel:{}", channel);
                                    let _ = state.rebuild_brain_from_db(&chan_brain);
                                    let _ = state.rebuild_brain_from_db("global");
                                    state.log_system(
                                        "WARN",
                                        format!(
                                            "Auto-purged {} lines from banned/timed-out user @{} in #{}",
                                            purged, banned_user, channel
                                        ),
                                    );
                                }
                            }
                        }
                    }
                    IrcEvent::ClearMsg {
                        channel,
                        author,
                        content,
                    } => {
                        let cleaned = {
                            let eng = state.engine.read().unwrap();
                            eng.filter.sanitize_for_training(&content).unwrap_or(content)
                        };
                        if let Ok(purged) = state
                            .db
                            .purge_matching_line_in_channel(&channel, &cleaned)
                        {
                            if purged > 0 {
                                let chan_brain = format!("channel:{}", channel);
                                let _ = state.rebuild_brain_from_db(&chan_brain);
                                let _ = state.rebuild_brain_from_db("global");
                                state.log_system(
                                    "WARN",
                                    format!(
                                        "Auto-purged deleted message by @{} in #{}",
                                        author, channel
                                    ),
                                );
                            }
                        }
                    }
                    IrcEvent::Privmsg {
                        channel,
                        author,
                        display_name: _,
                        color,
                        is_mod,
                        is_broadcaster,
                        reply_parent_body,
                        custom_reward_id,
                        bits,
                        content,
                    } => {
                        handle_incoming_privmsg(
                            state,
                            &channel,
                            &author,
                            color,
                            is_mod,
                            is_broadcaster,
                            reply_parent_body,
                            custom_reward_id,
                            bits,
                            &content,
                        );
                    }
                    IrcEvent::Other => {}
                }
            }

            Some(ctrl) = cmd_rx.recv() => {
                match ctrl {
                    IrcControlCmd::Join(ch) => {
                        let clean = ch.trim_start_matches('#').to_lowercase();
                        write_half
                            .write_all(format!("JOIN #{}\r\n", clean).as_bytes())
                            .await?;
                        write_half.flush().await?;
                    }
                    IrcControlCmd::Part(ch) => {
                        let clean = ch.trim_start_matches('#').to_lowercase();
                        write_half
                            .write_all(format!("PART #{}\r\n", clean).as_bytes())
                            .await?;
                        write_half.flush().await?;
                    }
                    IrcControlCmd::SendPrivmsg { channel, text } => {
                        let clean = channel.trim_start_matches('#').to_lowercase();
                        let safe_line: String = text.chars().filter(|c| *c != '\r' && *c != '\n').collect();
                        if !safe_line.is_empty() {
                            // Try Helix Send Chat Message API first (grants the official [🤖] Chat Bot Badge when App Access Token + user:bot / channel:bot is configured)
                            let sent_via_helix = try_send_helix_chat_message(state, &nick, &clean, &safe_line).await;
                            if !sent_via_helix {
                                write_half
                                    .write_all(format!("PRIVMSG #{} :{}\r\n", clean, safe_line).as_bytes())
                                    .await?;
                                write_half.flush().await?;
                            }
                        }
                    }
                }
            }

            _ = cadence_interval.tick(), if run_cadence => {
                check_time_based_triggers(state);
            }

            _ = ping_interval.tick() => {
                write_half.write_all(b"PING :tmi.twitch.tv\r\n").await?;
                write_half.flush().await?;
            }
        }
    }
}

fn handle_incoming_privmsg(
    state: &Arc<AppState>,
    channel: &str,
    author: &str,
    color: Option<String>,
    is_mod: bool,
    is_broadcaster: bool,
    reply_parent_body: Option<String>,
    custom_reward_id: Option<String>,
    bits: u32,
    content: &str,
) {
    let bot_nick = state.config.twitch.nickname.to_lowercase();
    let mockbot_nick = state.config.twitch.mockbot_nickname.to_lowercase();
    if author.eq_ignore_ascii_case(&bot_nick) || author.eq_ignore_ascii_case(&mockbot_nick) {
        return;
    }

    // Ignore default bots and per-channel ignored users
    if state
        .config
        .twitch
        .default_ignored_bots
        .iter()
        .any(|b| b.eq_ignore_ascii_case(author))
    {
        return;
    }

    let (
        is_ignored,
        is_trusted,
        blocked_words,
        target_brains,
        voice_enabled,
        only_when_live,
        is_stream_live,
        lines_between,
        time_between,
        last_spoke_epoch,
        reply_mention_chance,
        context_bias_chance,
        reward_tts_id,
        reward_markov_id,
        min_bits_tts,
        min_bits_markov,
        trigger_keywords,
        trigger_chance,
    ) = {
        let chans = state.channels.read().unwrap();
        if let Some(rt) = chans.get(channel) {
            let ig = rt
                .record
                .ignored_users
                .iter()
                .any(|u| u.eq_ignore_ascii_case(author));
            let tr = rt
                .record
                .trusted_users
                .iter()
                .any(|u| u.eq_ignore_ascii_case(author));
            (
                ig,
                tr,
                rt.record.blocked_words.clone(),
                rt.record.learning_target_brains(),
                rt.record.voice_enabled,
                rt.record.only_when_live,
                rt.is_stream_live,
                rt.record.lines_between_messages,
                rt.record.time_between_messages,
                rt.last_spoke_epoch,
                rt.record.reply_mention_chance,
                rt.record.context_bias_chance,
                rt.record.reward_tts_id.clone(),
                rt.record.reward_markov_id.clone(),
                rt.record.min_bits_tts,
                rt.record.min_bits_markov,
                rt.record.trigger_keywords.clone(),
                rt.record.trigger_chance,
            )
        } else {
            (
                false,
                false,
                Vec::new(),
                vec![format!("channel:{}", channel), "global".to_string()],
                true,
                true,
                false,
                100,
                15,
                0,
                100,
                35,
                String::new(),
                String::new(),
                0,
                0,
                Vec::new(),
                50,
            )
        }
    };

    if is_ignored || (!is_trusted && author.to_lowercase().ends_with("bot")) {
        return;
    }

    // Handle Bits / Cheer triggers (@bits=...)
    if bits > 0 {
        state.log_system(
            "INFO",
            format!("Cheer of {} bits in #{} by @{}: {}", bits, channel, author, content),
        );
        if min_bits_tts > 0 && bits >= min_bits_tts {
            let _ = state.queue_tts(channel, content, None);
        }
        if min_bits_markov > 0 && bits >= min_bits_markov {
            let _ = state.speak_in_channel(channel, &format!("bits:{}", bits), Some(content.to_string()));
            return;
        }
    }

    // Handle Twitch Channel Point Redemptions (@custom-reward-id=...)
    if let Some(ref rid) = custom_reward_id {
        state.log_system(
            "INFO",
            format!(
                "Channel Point reward in #{} by @{} [id: {}]: {}",
                channel, author, rid, content
            ),
        );
        let matches_rule = |rule: &str, id: &str| -> bool {
            let r = rule.trim();
            r == "*"
                || r.split(',')
                    .any(|part| part.trim().eq_ignore_ascii_case(id))
        };
        if !reward_tts_id.is_empty() && matches_rule(&reward_tts_id, rid) {
            let _ = state.queue_tts(channel, content, None);
        }
        if !reward_markov_id.is_empty() && matches_rule(&reward_markov_id, rid) {
            let _ = state.speak_in_channel(channel, "channel_points", Some(content.to_string()));
            return;
        }
    }

    // Check if it's a command (!ansv, !speak, !markov)
    let cmd_ctx = ChatCommandCtx {
        channel,
        author,
        is_mod,
        is_broadcaster,
        content,
    };
    if try_handle_command(state, &cmd_ctx) {
        return;
    }

    // Strip @bot_nick mention before training so the bot doesn't learn its own username as spam
    let mention_tag = format!("@{}", bot_nick);
    let is_mention = content.to_lowercase().contains(&bot_nick);
    let clean_for_learn = content
        .split_whitespace()
        .filter(|w| {
            let lw = w.trim_matches(|c: char| !c.is_alphanumeric() && c != '@').to_lowercase();
            lw != bot_nick && lw != mention_tag
        })
        .collect::<Vec<&str>>()
        .join(" ");

    let mut learned = false;
    let has_blocked_word = !blocked_words.is_empty() && {
        let lower_msg = clean_for_learn.to_lowercase();
        blocked_words
            .iter()
            .any(|bw| !bw.is_empty() && lower_msg.contains(bw))
    };
    if !has_blocked_word && !target_brains.is_empty() && !clean_for_learn.is_empty() {
        let learned_opt = {
            let mut eng = state.engine.write().unwrap();
            eng.learn_sentence(&clean_for_learn, &target_brains)
        };
        if let Some(cleaned_line) = learned_opt {
            learned = true;
            for bname in &target_brains {
                let _ = state
                    .db
                    .insert_corpus_line(bname, channel, author, &cleaned_line);
            }
        }
    }

    let now = chrono::Utc::now().timestamp();
    let (current_lines, context_seed_word) = {
        let mut chans = state.channels.write().unwrap();
        if let Some(rt) = chans.get_mut(channel) {
            rt.lines_since_last_spoke += 1;
            rt.total_messages_seen += 1;
            rt.last_activity_epoch = now;

            // Extract interesting topic words (length >= 4) into rolling context window
            for w in clean_for_learn.split_whitespace() {
                let clean_w = w.trim_matches(|c: char| !c.is_alphanumeric());
                if clean_w.len() >= 4 && !is_stopword(clean_w) {
                    if rt.recent_keywords.len() >= 32 {
                        rt.recent_keywords.pop_front();
                    }
                    rt.recent_keywords.push_back(clean_w.to_string());
                }
            }

            let pick = if !rt.recent_keywords.is_empty() {
                let idx = rand::thread_rng().gen_range(0..rt.recent_keywords.len());
                rt.recent_keywords.get(idx).cloned()
            } else {
                None
            };
            (rt.lines_since_last_spoke, pick)
        } else {
            (1, None)
        }
    };

    state.emit_event(LiveEvent::ChatMessage {
        timestamp: now,
        channel: channel.to_string(),
        author: author.to_string(),
        color,
        is_mod,
        is_broadcaster,
        content: content.to_string(),
        learned,
    });

    if !voice_enabled || (only_when_live && !is_stream_live) {
        return;
    }

    // Trigger 1: Mention reply (if user mentions @bot_nick)
    if is_mention && reply_mention_chance > 0 {
        let roll = rand::thread_rng().gen_range(0..100);
        if roll < reply_mention_chance {
            // Prefer seeding from the parent message if the user replied to a message!
            let seed = reply_parent_body.or_else(|| {
                if clean_for_learn.is_empty() {
                    None
                } else {
                    Some(clean_for_learn)
                }
            });
            let _ = state.speak_in_channel(channel, "mention", seed);
            return;
        }
    }

    // Trigger 1b: Custom keyword/phrase trigger
    if !trigger_keywords.is_empty() && trigger_chance > 0 {
        let lower_msg = clean_for_learn.to_lowercase();
        if let Some(matched_kw) = trigger_keywords
            .iter()
            .find(|kw| !kw.is_empty() && lower_msg.contains(kw.as_str()))
        {
            let roll = rand::thread_rng().gen_range(0..100);
            if roll < trigger_chance {
                let _ = state.speak_in_channel(
                    channel,
                    "keyword",
                    Some(matched_kw.clone()),
                );
                return;
            }
        }
    }

    // Trigger 2: Line counter threshold + minimum time cooldown (AND gate)
    let min_cooldown_ok =
        time_between == 0 || (now - last_spoke_epoch) >= (time_between as i64 * 60);
    if lines_between > 0 && current_lines >= lines_between && min_cooldown_ok {
        let roll = rand::thread_rng().gen_range(0..100);
        let seed = if roll < context_bias_chance {
            context_seed_word.or_else(|| {
                if clean_for_learn.is_empty() {
                    None
                } else {
                    Some(clean_for_learn)
                }
            })
        } else {
            None
        };
        let _ = state.speak_in_channel(channel, "auto_lines", seed);
    }
}

fn is_stopword(word: &str) -> bool {
    matches!(
        word.to_lowercase().as_str(),
        "that" | "this" | "with" | "have" | "from" | "they" | "what" | "when" | "where"
            | "which" | "there" | "their" | "about" | "would" | "could" | "should"
            | "just" | "like" | "some" | "more" | "very" | "only" | "also" | "even"
            | "really" | "think" | "know" | "going" | "were" | "been" | "into" | "your"
    )
}

fn check_time_based_triggers(state: &Arc<AppState>) {
    let now = chrono::Utc::now().timestamp();
    let due_channels: Vec<String> = {
        let chans = state.channels.read().unwrap();
        chans
            .values()
            .filter(|rt| {
                if !rt.currently_joined
                    || !rt.record.voice_enabled
                    || (rt.record.only_when_live && !rt.is_stream_live)
                    || rt.record.time_between_messages == 0
                {
                    return false;
                }
                let time_ok =
                    (now - rt.last_spoke_epoch) >= (rt.record.time_between_messages as i64 * 60);
                if !time_ok {
                    return false;
                }
                // Require full `lines_between_messages` if > 0, or at least 25 lines if lines_between_messages == 0
                let min_lines = if rt.record.lines_between_messages > 0 {
                    rt.record.lines_between_messages
                } else {
                    25
                };
                rt.lines_since_last_spoke >= min_lines
            })
            .map(|rt| rt.record.channel_name.clone())
            .collect()
    };

    for ch in due_channels {
        let _ = state.speak_in_channel(&ch, "auto_time", None);
    }
}

#[derive(Default)]
struct TwitchChannelInfo {
    display_name: String,
    avatar_url: String,
    bio: String,
    followers: u32,
    is_live: bool,
    stream_title: String,
    stream_game: String,
    stream_viewers: u32,
}

async fn poll_live_streams_loop(state: Arc<AppState>) {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let tls_config = Arc::new(
        rustls::ClientConfig::builder()
            .with_root_certificates(root_store)
            .with_no_client_auth(),
    );

    loop {
        let channels: Vec<String> = {
            let chans = state.channels.read().unwrap();
            chans.keys().cloned().collect()
        };

        for ch in channels {
            if let Ok(info) = fetch_twitch_channel_info(&ch, &tls_config).await {
                let mut chans = state.channels.write().unwrap();
                if let Some(rt) = chans.get_mut(&ch) {
                    rt.is_stream_live = info.is_live;
                    if !info.display_name.is_empty() {
                        rt.display_name = info.display_name;
                    }
                    if !info.avatar_url.is_empty() {
                        rt.avatar_url = info.avatar_url;
                    }
                    rt.bio = info.bio;
                    rt.followers = info.followers;
                    rt.stream_title = info.stream_title;
                    rt.stream_game = info.stream_game;
                    rt.stream_viewers = info.stream_viewers;
                }
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }

        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}

async fn fetch_twitch_channel_info(
    channel: &str,
    tls_config: &Arc<rustls::ClientConfig>,
) -> Result<TwitchChannelInfo> {
    use tokio::io::AsyncReadExt;
    let connector = TlsConnector::from(tls_config.clone());
    let tcp = TcpStream::connect(("gql.twitch.tv", 443)).await?;
    let domain = ServerName::try_from("gql.twitch.tv")?;
    let mut tls = connector.connect(domain, tcp).await?;

    let body = format!(
        r#"{{"query":"query {{ user(login: \"{}\") {{ displayName profileImageURL(width: 150) description followers {{ totalCount }} stream {{ id title viewersCount game {{ name }} }} }} }}"}}"#,
        channel
    );
    let req = format!(
        "POST /gql HTTP/1.1\r\nHost: gql.twitch.tv\r\nClient-Id: kimne78kx3ncx6brgo4mv6wki5h1ko\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    tls.write_all(req.as_bytes()).await?;
    let mut resp = String::new();
    tls.read_to_string(&mut resp).await?;
    let json_str = resp.split_once("\r\n\r\n").map(|(_, b)| b).unwrap_or(&resp);
    let val: serde_json::Value = serde_json::from_str(json_str).unwrap_or_default();
    let user = &val["data"]["user"];
    if user.is_null() {
        return Ok(TwitchChannelInfo::default());
    }
    let stream = &user["stream"];
    let is_live = !stream.is_null() && stream["id"].is_string();

    Ok(TwitchChannelInfo {
        display_name: user["displayName"].as_str().unwrap_or(channel).to_string(),
        avatar_url: user["profileImageURL"].as_str().unwrap_or("").to_string(),
        bio: user["description"].as_str().unwrap_or("").to_string(),
        followers: user["followers"]["totalCount"].as_u64().unwrap_or(0) as u32,
        is_live,
        stream_title: stream["title"].as_str().unwrap_or("").to_string(),
        stream_game: stream["game"]["name"].as_str().unwrap_or("").to_string(),
        stream_viewers: stream["viewersCount"].as_u64().unwrap_or(0) as u32,
    })
}

static APP_TOKEN_CACHE: std::sync::OnceLock<std::sync::Mutex<(String, i64)>> =
    std::sync::OnceLock::new();
static USER_ID_CACHE: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<String, String>>> =
    std::sync::OnceLock::new();

async fn try_send_helix_chat_message(
    state: &Arc<AppState>,
    bot_nick: &str,
    channel: &str,
    message: &str,
) -> bool {
    let client_id = state.config.twitch.client_id.trim().to_string();
    let client_secret = state.config.twitch.client_secret.trim().to_string();
    if client_id.is_empty()
        || client_secret.is_empty()
        || client_secret == "PASTE_SECRET_HERE_IF_NEEDED"
    {
        return false;
    }

    let sender_id = if bot_nick.eq_ignore_ascii_case("anormalskipviewer") {
        "769880332".to_string()
    } else if bot_nick.eq_ignore_ascii_case("mockbot") {
        "49887813".to_string()
    } else {
        match resolve_twitch_user_id(&client_id, &client_secret, bot_nick).await {
            Some(id) => id,
            None => return false,
        }
    };

    let Some(broadcaster_id) =
        resolve_twitch_user_id(&client_id, &client_secret, channel).await
    else {
        return false;
    };

    let Some(app_token) = get_twitch_app_access_token(&client_id, &client_secret).await else {
        return false;
    };

    let payload = serde_json::json!({
        "broadcaster_id": broadcaster_id,
        "sender_id": sender_id,
        "message": message,
    })
    .to_string();

    let out = tokio::process::Command::new("curl")
        .arg("-s")
        .arg("-m")
        .arg("4")
        .arg("-X")
        .arg("POST")
        .arg("https://api.twitch.tv/helix/chat/messages")
        .arg("-H")
        .arg(format!("Authorization: Bearer {}", app_token))
        .arg("-H")
        .arg(format!("Client-Id: {}", client_id))
        .arg("-H")
        .arg("Content-Type: application/json")
        .arg("-d")
        .arg(&payload)
        .output()
        .await;

    let Ok(resp) = out else {
        return false;
    };
    let text = String::from_utf8_lossy(&resp.stdout);
    text.contains(r#""is_sent":true"#)
}

async fn get_twitch_app_access_token(client_id: &str, client_secret: &str) -> Option<String> {
    let now = chrono::Utc::now().timestamp();
    let cache = APP_TOKEN_CACHE.get_or_init(|| std::sync::Mutex::new((String::new(), 0)));
    {
        let guard = cache.lock().unwrap();
        if !guard.0.is_empty() && guard.1 > now + 120 {
            return Some(guard.0.clone());
        }
    }

    let out = tokio::process::Command::new("curl")
        .arg("-s")
        .arg("-m")
        .arg("5")
        .arg("-X")
        .arg("POST")
        .arg("https://id.twitch.tv/oauth2/token")
        .arg("-d")
        .arg(format!(
            "client_id={}&client_secret={}&grant_type=client_credentials",
            client_id, client_secret
        ))
        .output()
        .await
        .ok()?;

    let val: serde_json::Value = serde_json::from_slice(&out.stdout).ok()?;
    let token = val.get("access_token")?.as_str()?.to_string();
    let expires_in = val.get("expires_in").and_then(|v| v.as_i64()).unwrap_or(3600);
    {
        let mut guard = cache.lock().unwrap();
        *guard = (token.clone(), now + expires_in);
    }
    Some(token)
}

async fn resolve_twitch_user_id(
    client_id: &str,
    client_secret: &str,
    login: &str,
) -> Option<String> {
    let clean = login.trim().trim_start_matches('#').to_lowercase();
    let cache =
        USER_ID_CACHE.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()));
    {
        let guard = cache.lock().unwrap();
        if let Some(id) = guard.get(&clean) {
            return Some(id.clone());
        }
    }

    let app_token = get_twitch_app_access_token(client_id, client_secret).await?;
    let out = tokio::process::Command::new("curl")
        .arg("-s")
        .arg("-m")
        .arg("4")
        .arg(format!("https://api.twitch.tv/helix/users?login={}", clean))
        .arg("-H")
        .arg(format!("Authorization: Bearer {}", app_token))
        .arg("-H")
        .arg(format!("Client-Id: {}", client_id))
        .output()
        .await
        .ok()?;

    let val: serde_json::Value = serde_json::from_slice(&out.stdout).ok()?;
    let id = val
        .get("data")?
        .as_array()?
        .first()?
        .get("id")?
        .as_str()?
        .to_string();
    {
        let mut guard = cache.lock().unwrap();
        guard.insert(clean, id.clone());
    }
    Some(id)
}

