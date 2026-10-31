use crate::markov::BrainWeight;
use crate::state::{AppState, IrcControlCmd};
use std::sync::Arc;

pub struct ChatCommandCtx<'a> {
    pub channel: &'a str,
    pub author: &'a str,
    pub is_mod: bool,
    pub is_broadcaster: bool,
    pub content: &'a str,
}

pub fn try_handle_command(state: &Arc<AppState>, ctx: &ChatCommandCtx<'_>) -> bool {
    let trimmed = ctx.content.trim();
    if !trimmed.starts_with('!') {
        return false;
    }

    let mut parts = trimmed.split_whitespace();
    let Some(first) = parts.next() else {
        return false;
    };
    let cmd_lower = first.to_lowercase();
    let prefix = state.config.twitch.command_prefix.to_lowercase();
    let mockbot_prefix = state.config.twitch.mockbot_command_prefix.to_lowercase();

    // Direct aliases: !speak [seed...] or !markov [seed...]
    if cmd_lower == "!speak" || cmd_lower == "!markov" {
        let rest: Vec<&str> = parts.collect();
        let seed = if rest.is_empty() {
            None
        } else {
            Some(rest.join(" "))
        };
        handle_speak(state, ctx, seed);
        return true;
    }

    // Direct alias: !mock <brain/channel> [seed...]
    if cmd_lower == "!mock" {
        let rest: Vec<&str> = parts.collect();
        handle_mock_persona(state, ctx, &rest);
        return true;
    }

    // Direct alias: !tts <text...>
    if cmd_lower == "!tts" {
        let rest: Vec<&str> = parts.collect();
        if !rest.is_empty() && has_manage_permission(state, ctx) {
            let text = rest.join(" ");
            let _ = state.queue_tts(ctx.channel, &text, None);
            send_reply(
                state,
                ctx.channel,
                format!("Queued Bark TTS for #{}: \"{}\"", ctx.channel, text),
            );
        }
        return true;
    }

    // Direct alias: !skip (stops currently playing OBS TTS audio and clears queue)
    if cmd_lower == "!skip" {
        if has_manage_permission(state, ctx) {
            state.clear_tts_queue(ctx.channel);
            send_reply(state, ctx.channel, "Cleared TTS queue and skipped audio.");
        }
        return true;
    }

    // Check per-channel custom Markov commands (e.g. !wisdom, !lore, !horoscope)
    let matched_custom_cmd = {
        let clean_cmd = cmd_lower.trim_start_matches('!');
        let chans = state.channels.read().unwrap();
        chans.get(ctx.channel).and_then(|rt| {
            rt.record
                .custom_commands
                .iter()
                .find(|r| r.cmd.trim().trim_start_matches('!').eq_ignore_ascii_case(clean_cmd))
                .cloned()
        })
    };
    if let Some(rule) = matched_custom_cmd {
        if has_speak_permission(state, ctx) && check_and_bump_cooldown(state, ctx) {
            let rest: Vec<&str> = parts.collect();
            let extra = rest.join(" ");
            let seed = if !extra.is_empty() {
                Some(extra)
            } else if !rule.seed.trim().is_empty() {
                Some(rule.seed.trim().to_string())
            } else {
                None
            };
            let b = rule.brain.trim();
            if b.is_empty() || b == "default" {
                let _ = state.speak_in_channel(ctx.channel, &format!("cmd:!{}", rule.cmd), seed);
            } else {
                let brains = vec![
                    BrainWeight {
                        brain: b.to_string(),
                        weight: 0.90,
                    },
                    BrainWeight {
                        brain: "global".to_string(),
                        weight: 0.10,
                    },
                ];
                let _ = state.speak_with_custom_brains_in_channel(
                    ctx.channel,
                    brains,
                    &format!("cmd:!{}", rule.cmd),
                    seed,
                );
            }
        }
        return true;
    }

    if cmd_lower != prefix
        && cmd_lower != mockbot_prefix
        && cmd_lower != "!ansv"
        && cmd_lower != "!mockbot"
    {
        return false;
    }

    let Some(sub) = parts.next() else {
        send_reply(
            state,
            ctx.channel,
            format!(
                "Commands: {} speak [seed], !mock <brain/@user> [seed], {} status, {} lines <N>, {} time <M>, {} order <1-3>, {} start/stop, {} part",
                cmd_lower, cmd_lower, cmd_lower, cmd_lower, cmd_lower, cmd_lower, cmd_lower
            ),
        );
        return true;
    };

    let sub_lower = sub.to_lowercase();
    let args: Vec<&str> = parts.collect();

    match sub_lower.as_str() {
        "speak" | "gen" | "say" => {
            let seed = if args.is_empty() {
                None
            } else {
                Some(args.join(" "))
            };
            handle_speak(state, ctx, seed);
        }
        "as" | "mock" | "persona" => {
            handle_mock_persona(state, ctx, &args);
        }
        "claim" | "verify" => {
            let is_bot_owner = ctx.author.eq_ignore_ascii_case(&state.config.twitch.owner);
            if !ctx.is_broadcaster && !is_bot_owner && !ctx.author.eq_ignore_ascii_case(ctx.channel) {
                send_reply(
                    state,
                    ctx.channel,
                    "Only the channel broadcaster can verify channel ownership.",
                );
                return true;
            }
            let Some(code) = args.first() else {
                send_reply(state, ctx.channel, "Usage: !ansv claim <code>");
                return true;
            };
            match state.db.verify_channel_claim(ctx.channel, code) {
                Ok(true) => {
                    update_channel_record(state, ctx.channel, |rec| rec.voice_enabled = true);
                    send_reply(
                        state,
                        ctx.channel,
                        format!("Verified ownership of #{}! Your web dashboard is now unlocked.", ctx.channel),
                    );
                }
                _ => {
                    send_reply(state, ctx.channel, "Invalid or expired verification code.");
                }
            }
        }
        "skip" | "shush" => {
            if !has_manage_permission(state, ctx) {
                return true;
            }
            state.emit_event(crate::state::LiveEvent::TtsSkip {
                channel: ctx.channel.to_string(),
            });
            send_reply(state, ctx.channel, "Skipped TTS audio.");
        }
        "tts" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            let Some(first_arg) = args.first() else {
                send_reply(
                    state,
                    ctx.channel,
                    "Usage: !ansv tts <on | off | delay | text to synthesize...>",
                );
                return true;
            };
            match first_arg.to_lowercase().as_str() {
                "on" | "enable" => {
                    update_channel_record(state, ctx.channel, |rec| rec.tts_enabled = true);
                    send_reply(state, ctx.channel, format!("Bark TTS enabled for #{}.", ctx.channel));
                }
                "off" | "disable" => {
                    update_channel_record(state, ctx.channel, |rec| rec.tts_enabled = false);
                    send_reply(state, ctx.channel, format!("Bark TTS disabled for #{}.", ctx.channel));
                }
                "delay" | "sync" => {
                    let mut new_delay = false;
                    update_channel_record(state, ctx.channel, |rec| {
                        rec.tts_delay_enabled = !rec.tts_delay_enabled;
                        new_delay = rec.tts_delay_enabled;
                    });
                    send_reply(
                        state,
                        ctx.channel,
                        format!(
                            "Bark TTS Delay Sync {} for #{}.",
                            if new_delay { "ENABLED" } else { "DISABLED" },
                            ctx.channel
                        ),
                    );
                }
                _ => {
                    let text = args.join(" ");
                    let _ = state.queue_tts(ctx.channel, &text, None);
                    send_reply(
                        state,
                        ctx.channel,
                        format!("Synthesizing Bark TTS for #{}: \"{}\"", ctx.channel, text),
                    );
                }
            }
        }
        "voice" | "voice_preset" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            if let Some(vp) = args.first() {
                let preset = (*vp).to_string();
                update_channel_record(state, ctx.channel, |rec| rec.voice_preset = preset.clone());
                send_reply(
                    state,
                    ctx.channel,
                    format!("Updated #{} Bark voice preset to '{}'.", ctx.channel, preset),
                );
            } else {
                let voices: Vec<String> = crate::tts::list_available_voices()
                    .into_iter()
                    .map(|v| v.code)
                    .collect();
                send_reply(
                    state,
                    ctx.channel,
                    format!("Available Bark voices: {}", voices.join(", ")),
                );
            }
        }
        "temp" | "temperature" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            if let Some(val_str) = args.first() {
                if let Ok(t) = val_str.parse::<f64>() {
                    let clamped = t.clamp(0.3, 2.5);
                    update_channel_record(state, ctx.channel, |rec| rec.temperature = clamped);
                    send_reply(
                        state,
                        ctx.channel,
                        format!(
                            "Set Markov temperature = {:.2} for #{} (<1.0 coherent, 1.0 normal, >1.0 chaos).",
                            clamped, ctx.channel
                        ),
                    );
                } else {
                    send_reply(state, ctx.channel, "Usage: !ansv temp <0.3 - 2.5>");
                }
            } else {
                send_reply(state, ctx.channel, "Usage: !ansv temp <0.3 - 2.5>");
            }
        }
        "context" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            if let Some(val_str) = args.first() {
                if let Ok(pct) = val_str.trim_end_matches('%').parse::<u32>() {
                    let clamped = pct.min(100);
                    update_channel_record(state, ctx.channel, |rec| {
                        rec.context_bias_chance = clamped
                    });
                    send_reply(
                        state,
                        ctx.channel,
                        format!(
                            "Set context topic bias chance = {}% for #{}.",
                            clamped, ctx.channel
                        ),
                    );
                } else {
                    send_reply(state, ctx.channel, "Usage: !ansv context <0-100>");
                }
            } else {
                send_reply(state, ctx.channel, "Usage: !ansv context <0-100>");
            }
        }
        "status" | "info" | "stats" => {
            handle_status(state, ctx.channel);
        }
        "start" | "enable" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "You do not have permission to manage ANSV2 in this channel.");
                return true;
            }
            update_channel_record(state, ctx.channel, |rec| rec.voice_enabled = true);
            send_reply(state, ctx.channel, format!("ANSV2 voice enabled in #{}.", ctx.channel));
        }
        "stop" | "disable" | "mute" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "You do not have permission to manage ANSV2 in this channel.");
                return true;
            }
            update_channel_record(state, ctx.channel, |rec| rec.voice_enabled = false);
            send_reply(state, ctx.channel, format!("ANSV2 voice disabled in #{}.", ctx.channel));
        }
        "lines" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            if let Some(val_str) = args.first() {
                if let Ok(n) = val_str.parse::<u32>() {
                    update_channel_record(state, ctx.channel, |rec| rec.lines_between_messages = n);
                    send_reply(
                        state,
                        ctx.channel,
                        format!("Set lines_between_messages = {} for #{}.", n, ctx.channel),
                    );
                } else {
                    send_reply(state, ctx.channel, "Usage: !ansv lines <number>");
                }
            } else {
                send_reply(state, ctx.channel, "Usage: !ansv lines <number>");
            }
        }
        "time" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            if let Some(val_str) = args.first() {
                if let Ok(m) = val_str.parse::<u32>() {
                    update_channel_record(state, ctx.channel, |rec| rec.time_between_messages = m);
                    send_reply(
                        state,
                        ctx.channel,
                        format!("Set time_between_messages = {} min for #{}.", m, ctx.channel),
                    );
                } else {
                    send_reply(state, ctx.channel, "Usage: !ansv time <minutes>");
                }
            } else {
                send_reply(state, ctx.channel, "Usage: !ansv time <minutes>");
            }
        }
        "order" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            if let Some(val_str) = args.first() {
                if let Ok(ord) = val_str.parse::<usize>() {
                    let clamped = ord.clamp(1, 3);
                    update_channel_record(state, ctx.channel, |rec| rec.markov_order = clamped);
                    send_reply(
                        state,
                        ctx.channel,
                        format!("Set Markov N-gram order = {} for #{}.", clamped, ctx.channel),
                    );
                }
            }
        }
        "brain" | "mix" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            let Some(mode) = args.first() else {
                send_reply(
                    state,
                    ctx.channel,
                    "Usage: !ansv brain <blend | channel | global | brain1:weight1,brain2:weight2>",
                );
                return true;
            };
            let chan_brain = format!("channel:{}", ctx.channel);
            let new_mix = match mode.to_lowercase().as_str() {
                "channel" | "local" => vec![BrainWeight {
                    brain: chan_brain,
                    weight: 1.0,
                }],
                "global" | "general" => vec![BrainWeight {
                    brain: "global".to_string(),
                    weight: 1.0,
                }],
                "blend" | "default" => vec![
                    BrainWeight {
                        brain: chan_brain,
                        weight: 0.70,
                    },
                    BrainWeight {
                        brain: "global".to_string(),
                        weight: 0.30,
                    },
                ],
                custom_spec => parse_mix_spec(custom_spec, &chan_brain),
            };
            if new_mix.is_empty() {
                send_reply(state, ctx.channel, "Invalid brain mix specification.");
                return true;
            }
            let summary_str: Vec<String> = new_mix
                .iter()
                .map(|bw| format!("{}={:.0}%", bw.brain, bw.weight * 100.0))
                .collect();
            update_channel_record(state, ctx.channel, |rec| rec.brain_mix = new_mix);
            send_reply(
                state,
                ctx.channel,
                format!("Updated #{} brain mix: {}", ctx.channel, summary_str.join(", ")),
            );
        }
        "trust" | "trusted" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            handle_user_list_cmd(state, ctx.channel, &args, true);
        }
        "ignore" | "ignored" => {
            if !has_manage_permission(state, ctx) {
                send_reply(state, ctx.channel, "Permission denied.");
                return true;
            }
            handle_user_list_cmd(state, ctx.channel, &args, false);
        }
        "join" => {
            // Anyone typing `!ansv join` in the bot's own channel or owner channel can invite the bot to their own channel
            // Bot owner can specify any target channel: `!ansv join <channel>`
            let is_bot_owner = ctx.author.eq_ignore_ascii_case(&state.config.twitch.owner);
            let target = if is_bot_owner && !args.is_empty() {
                args[0].trim_start_matches('#').to_lowercase()
            } else if state.config.web.public_join_enabled || is_bot_owner {
                ctx.author.to_lowercase()
            } else {
                send_reply(state, ctx.channel, "Public self-join is currently disabled.");
                return true;
            };
            let contrib = is_bot_owner || state.config.web.public_join_contribute_global;
            match state.add_or_join_channel(&target, "chat_cmd", contrib) {
                Ok(_) => send_reply(
                    state,
                    ctx.channel,
                    format!("Joined #{}! Use !ansv status or !ansv part in your chat.", target),
                ),
                Err(e) => send_reply(state, ctx.channel, format!("Could not join #{}: {}", target, e)),
            }
        }
        "part" | "leave" => {
            let is_bot_owner = ctx.author.eq_ignore_ascii_case(&state.config.twitch.owner);
            let target = if is_bot_owner && !args.is_empty() {
                args[0].trim_start_matches('#').to_lowercase()
            } else {
                ctx.channel.to_lowercase()
            };
            if !is_bot_owner && !ctx.is_broadcaster && ctx.author != target {
                send_reply(
                    state,
                    ctx.channel,
                    "Only the broadcaster or bot owner can remove ANSV2 from the channel.",
                );
                return true;
            }
            let _ = state.part_channel(&target, false);
            send_reply(state, ctx.channel, format!("Leaving #{}. Goodbye!", target));
        }
        _ => {
            send_reply(
                state,
                ctx.channel,
                "Unknown subcommand. Try: !ansv speak [seed], !ansv status, !ansv brain, !ansv lines, !ansv time, !ansv start/stop",
            );
        }
    }

    true
}

fn check_and_bump_cooldown(state: &Arc<AppState>, ctx: &ChatCommandCtx<'_>) -> bool {
    if has_manage_permission(state, ctx) {
        return true;
    }
    let now = chrono::Utc::now().timestamp();
    let mut chans = state.channels.write().unwrap();
    if let Some(rt) = chans.get_mut(ctx.channel) {
        let cd = rt.record.command_cooldown_secs as i64;
        if cd > 0 && (now - rt.last_command_epoch) < cd {
            return false;
        }
        rt.last_command_epoch = now;
    }
    true
}

fn handle_speak(state: &Arc<AppState>, ctx: &ChatCommandCtx<'_>, seed: Option<String>) {
    let voice_ok = {
        let chans = state.channels.read().unwrap();
        chans
            .get(ctx.channel)
            .map(|rt| rt.record.voice_enabled)
            .unwrap_or(true)
    };
    if !voice_ok && !has_manage_permission(state, ctx) {
        return;
    }

    if !has_speak_permission(state, ctx) || !check_and_bump_cooldown(state, ctx) {
        return;
    }

    if state
        .speak_in_channel(ctx.channel, "command", seed)
        .is_none()
    {
        send_reply(
            state,
            ctx.channel,
            "Not enough corpus data yet to generate a message for this channel.",
        );
    }
}

fn handle_mock_persona(state: &Arc<AppState>, ctx: &ChatCommandCtx<'_>, args: &[&str]) {
    if !has_speak_permission(state, ctx) || !check_and_bump_cooldown(state, ctx) {
        return;
    }
    let Some(raw_target) = args.first() else {
        let available: Vec<String> = {
            let eng = state.engine.read().unwrap();
            eng.list_brain_stats()
                .into_iter()
                .filter(|b| b.sentence_count >= 10)
                .map(|b| b.name)
                .collect()
        };
        send_reply(
            state,
            ctx.channel,
            format!(
                "Usage: !mock <brain or @user> [seed] | Brains: {}",
                available.join(", ")
            ),
        );
        return;
    };

    let clean_target = raw_target.trim().trim_start_matches('@').trim_start_matches('#').to_lowercase();
    let seed = if args.len() > 1 {
        Some(args[1..].join(" "))
    } else {
        None
    };

    let resolved_brain = {
        let eng = state.engine.read().unwrap();
        if eng.brains.contains_key(&clean_target) {
            Some(clean_target.clone())
        } else if eng.brains.contains_key(&format!("channel:{}", clean_target)) {
            Some(format!("channel:{}", clean_target))
        } else if eng.brains.contains_key(&format!("custom:{}", clean_target)) {
            Some(format!("custom:{}", clean_target))
        } else {
            None
        }
    };

    let Some(target_brain) = resolved_brain else {
        // Try mocking a specific chatter by username first!
        if state
            .mock_chatter_in_channel(ctx.channel, &clean_target, seed)
            .is_some()
        {
            return;
        }
        // Fallback: use clean_target as a seed word in the current channel
        let seed_str = args.join(" ");
        handle_speak(state, ctx, Some(seed_str));
        return;
    };

    if let Some(other_chan) = target_brain.strip_prefix("channel:") {
        if other_chan != ctx.channel && !ctx.author.eq_ignore_ascii_case(&state.config.twitch.owner) {
            let is_priv = {
                let chans = state.channels.read().unwrap();
                chans
                    .get(other_chan)
                    .map(|rt| rt.record.private_brain)
                    .unwrap_or(false)
            };
            if is_priv {
                send_reply(state, ctx.channel, "That channel's brain is private.");
                return;
            }
        }
    }

    let brains = vec![
        BrainWeight {
            brain: target_brain.clone(),
            weight: 0.90,
        },
        BrainWeight {
            brain: "global".to_string(),
            weight: 0.10,
        },
    ];

    if state
        .speak_with_custom_brains_in_channel(
            ctx.channel,
            brains,
            &format!("mock:{}", target_brain),
            seed,
        )
        .is_none()
    {
        send_reply(
            state,
            ctx.channel,
            format!("Could not generate a message from brain '{}'.", target_brain),
        );
    }
}

fn handle_status(state: &Arc<AppState>, channel: &str) {
    let (chan_sentences, global_sentences) = {
        let eng = state.engine.read().unwrap();
        let cb = eng
            .brains
            .get(&format!("channel:{}", channel))
            .map(|b| b.sentence_count)
            .unwrap_or(0);
        let gb = eng
            .brains
            .get("global")
            .map(|b| b.sentence_count)
            .unwrap_or(0);
        (cb, gb)
    };

    let chans = state.channels.read().unwrap();
    if let Some(rt) = chans.get(channel) {
        let mix_desc: Vec<String> = rt
            .record
            .brain_mix
            .iter()
            .map(|b| format!("{}:{:.0}%", b.brain, b.weight * 100.0))
            .collect();
        let msg = format!(
            "#{} | Voice: {} | Order: {} | Mix: [{}] | Brain: {} lines (Global: {} lines) | Cadence: {}/{} lines, {}m",
            channel,
            if rt.record.voice_enabled { "ON" } else { "OFF" },
            rt.record.markov_order,
            mix_desc.join(", "),
            chan_sentences,
            global_sentences,
            rt.lines_since_last_spoke,
            rt.record.lines_between_messages,
            rt.record.time_between_messages
        );
        drop(chans);
        send_reply(state, channel, msg);
    }
}

fn handle_user_list_cmd(state: &Arc<AppState>, channel: &str, args: &[&str], is_trusted: bool) {
    let label = if is_trusted { "trusted" } else { "ignored" };
    if args.len() < 2 {
        let chans = state.channels.read().unwrap();
        if let Some(rt) = chans.get(channel) {
            let list = if is_trusted {
                &rt.record.trusted_users
            } else {
                &rt.record.ignored_users
            };
            let joined = if list.is_empty() {
                "none".to_string()
            } else {
                list.join(", ")
            };
            drop(chans);
            send_reply(
                state,
                channel,
                format!("Current {} users in #{}: {} | Usage: !ansv {} <add|remove> <user>", label, channel, joined, label),
            );
        }
        return;
    }

    let action = args[0].to_lowercase();
    let user = args[1].trim_start_matches('@').to_lowercase();
    update_channel_record(state, channel, |rec| {
        let list = if is_trusted {
            &mut rec.trusted_users
        } else {
            &mut rec.ignored_users
        };
        if action == "add" {
            if !list.contains(&user) {
                list.push(user.clone());
            }
        } else if action == "remove" || action == "del" {
            list.retain(|u| u != &user);
        }
    });
    send_reply(
        state,
        channel,
        format!("Updated {} list for #{}: {} {}", label, channel, action, user),
    );
}

fn parse_mix_spec(spec: &str, default_chan_brain: &str) -> Vec<BrainWeight> {
    let mut out = Vec::new();
    for item in spec.split(',') {
        let (raw_name, raw_w) = if let Some((k, v)) = item.split_once('=') {
            (k.trim(), v.trim())
        } else if let Some((k, v)) = item.split_once(':') {
            // Handle e.g. channel:50 or global:50
            if k == "channel" || k == "global" {
                (k.trim(), v.trim())
            } else {
                continue;
            }
        } else {
            continue;
        };

        let brain_name = match raw_name {
            "channel" | "local" => default_chan_brain.to_string(),
            "global" | "general" => "global".to_string(),
            other => {
                if other.starts_with("channel:") || other.starts_with("custom:") {
                    other.to_string()
                } else {
                    format!("custom:{}", other)
                }
            }
        };

        if let Ok(mut w) = raw_w.trim_end_matches('%').parse::<f64>() {
            if w > 1.0 {
                w /= 100.0;
            }
            if w > 0.0 {
                out.push(BrainWeight {
                    brain: brain_name,
                    weight: w,
                });
            }
        }
    }
    out
}

fn has_manage_permission(state: &Arc<AppState>, ctx: &ChatCommandCtx<'_>) -> bool {
    if ctx.is_broadcaster || ctx.is_mod {
        return true;
    }
    if ctx.author.eq_ignore_ascii_case(&state.config.twitch.owner) {
        return true;
    }
    let chans = state.channels.read().unwrap();
    if let Some(rt) = chans.get(ctx.channel) {
        if rt.record.owner.eq_ignore_ascii_case(ctx.author) {
            return true;
        }
        if rt
            .record
            .trusted_users
            .iter()
            .any(|u| u.eq_ignore_ascii_case(ctx.author))
        {
            return true;
        }
    }
    false
}

fn has_speak_permission(state: &Arc<AppState>, ctx: &ChatCommandCtx<'_>) -> bool {
    // Allow anyone to use !speak / !ansv speak unless trusted_users is explicitly restricted?
    // In Mockbot/ansv-bot, mods/broadcaster/owner/trusted could always use !ansv speak;
    // let's allow mods, broadcaster, owner, trusted users, AND regular chatters if trusted_users is empty!
    if has_manage_permission(state, ctx) {
        return true;
    }
    let chans = state.channels.read().unwrap();
    if let Some(rt) = chans.get(ctx.channel) {
        return rt.record.trusted_users.is_empty();
    }
    true
}

fn update_channel_record<F>(state: &Arc<AppState>, channel: &str, f: F)
where
    F: FnOnce(&mut crate::db::ChannelRecord),
{
    let mut chans = state.channels.write().unwrap();
    if let Some(rt) = chans.get_mut(channel) {
        f(&mut rt.record);
        let _ = state.db.upsert_channel(&rt.record);
    }
}

fn send_reply(state: &Arc<AppState>, channel: &str, text: impl Into<String>) {
    let _ = state.irc_cmd_tx.send(IrcControlCmd::SendPrivmsg {
        channel: channel.to_string(),
        text: text.into(),
    });
}
