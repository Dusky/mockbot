use std::collections::HashMap;

#[derive(Debug, Clone)]
pub enum IrcEvent {
    Ping(String),
    Welcome,
    Reconnect,
    Join {
        nick: String,
        channel: String,
    },
    Part {
        nick: String,
        channel: String,
    },
    Privmsg {
        channel: String,
        author: String,
        display_name: String,
        color: Option<String>,
        is_mod: bool,
        is_broadcaster: bool,
        reply_parent_body: Option<String>,
        custom_reward_id: Option<String>,
        bits: u32,
        content: String,
    },
    ClearChat {
        channel: String,
        target_user: Option<String>,
    },
    ClearMsg {
        channel: String,
        author: String,
        content: String,
    },
    Raid {
        channel: String,
        raider: String,
        viewers: u32,
    },
    Sub {
        channel: String,
        subscriber: String,
        message: Option<String>,
    },
    Notice {
        channel: String,
        message: String,
    },
    Other,
}

pub fn parse_irc_line(raw_line: &str) -> IrcEvent {
    let mut rest = raw_line.trim_end_matches(&['\r', '\n'][..]);
    if rest.is_empty() {
        return IrcEvent::Other;
    }

    let mut tags: HashMap<&str, &str> = HashMap::new();
    if rest.starts_with('@') {
        if let Some((tag_part, after_tags)) = rest[1..].split_once(' ') {
            for kv in tag_part.split(';') {
                if let Some((k, v)) = kv.split_once('=') {
                    tags.insert(k, v);
                }
            }
            rest = after_tags.trim_start();
        }
    }

    if let Some(payload) = rest.strip_prefix("PING ") {
        return IrcEvent::Ping(payload.to_string());
    }

    let mut prefix: Option<&str> = None;
    if rest.starts_with(':') {
        if let Some((p, after_prefix)) = rest[1..].split_once(' ') {
            prefix = Some(p);
            rest = after_prefix.trim_start();
        }
    }

    let nick_from_prefix = prefix
        .and_then(|p| p.split('!').next())
        .unwrap_or("")
        .to_lowercase();

    let (cmd_and_params, trailing) = if let Some((before, after)) = rest.split_once(" :") {
        (before, Some(after))
    } else {
        (rest, None)
    };

    let mut parts = cmd_and_params.split_whitespace();
    let Some(cmd) = parts.next() else {
        return IrcEvent::Other;
    };

    match cmd {
        "001" => IrcEvent::Welcome,
        "RECONNECT" => IrcEvent::Reconnect,
        "JOIN" => {
            let chan = parts
                .next()
                .or(trailing)
                .unwrap_or("")
                .trim_start_matches('#')
                .to_lowercase();
            IrcEvent::Join {
                nick: nick_from_prefix,
                channel: chan,
            }
        }
        "PART" => {
            let chan = parts
                .next()
                .or(trailing)
                .unwrap_or("")
                .trim_start_matches('#')
                .to_lowercase();
            IrcEvent::Part {
                nick: nick_from_prefix,
                channel: chan,
            }
        }
        "PRIVMSG" => {
            let chan = parts
                .next()
                .unwrap_or("")
                .trim_start_matches('#')
                .to_lowercase();
            let content = trailing.unwrap_or("").to_string();
            let display_name = tags
                .get("display-name")
                .filter(|s| !s.is_empty())
                .copied()
                .unwrap_or(&nick_from_prefix)
                .to_string();
            let color = tags
                .get("color")
                .filter(|s| !s.is_empty())
                .map(|s| (*s).to_string());
            let badges = tags.get("badges").copied().unwrap_or("");
            let is_broadcaster =
                badges.contains("broadcaster/") || nick_from_prefix == chan;
            let is_mod = is_broadcaster
                || tags.get("mod") == Some(&"1")
                || badges.contains("moderator/");
            let reply_parent_body = tags
                .get("reply-parent-msg-body")
                .filter(|s| !s.is_empty())
                .map(|s| s.replace("\\s", " ").replace("\\:", ";"));
            let custom_reward_id = tags
                .get("custom-reward-id")
                .filter(|s| !s.is_empty())
                .map(|s| (*s).to_string());
            let bits = tags
                .get("bits")
                .and_then(|v| v.parse::<u32>().ok())
                .unwrap_or(0);

            IrcEvent::Privmsg {
                channel: chan,
                author: nick_from_prefix,
                display_name,
                color,
                is_mod,
                is_broadcaster,
                reply_parent_body,
                custom_reward_id,
                bits,
                content,
            }
        }
        "CLEARCHAT" => {
            let chan = parts
                .next()
                .unwrap_or("")
                .trim_start_matches('#')
                .to_lowercase();
            let target_user = trailing
                .map(|s| s.trim().to_lowercase())
                .filter(|s| !s.is_empty());
            IrcEvent::ClearChat {
                channel: chan,
                target_user,
            }
        }
        "CLEARMSG" => {
            let chan = parts
                .next()
                .unwrap_or("")
                .trim_start_matches('#')
                .to_lowercase();
            let author = tags.get("login").copied().unwrap_or("").to_lowercase();
            let content = trailing.unwrap_or("").to_string();
            IrcEvent::ClearMsg {
                channel: chan,
                author,
                content,
            }
        }
        "USERNOTICE" => {
            let chan = parts
                .next()
                .unwrap_or("")
                .trim_start_matches('#')
                .to_lowercase();
            let msg_id = tags.get("msg-id").copied().unwrap_or("");
            if msg_id == "raid" {
                let raider = tags
                    .get("msg-param-login")
                    .or_else(|| tags.get("login"))
                    .copied()
                    .unwrap_or("someone")
                    .to_lowercase();
                let viewers = tags
                    .get("msg-param-viewerCount")
                    .and_then(|v| v.parse::<u32>().ok())
                    .unwrap_or(1);
                return IrcEvent::Raid {
                    channel: chan,
                    raider,
                    viewers,
                };
            } else if matches!(msg_id, "sub" | "resub" | "subgift") {
                let subscriber = tags
                    .get("login")
                    .or_else(|| tags.get("display-name"))
                    .copied()
                    .unwrap_or("subscriber")
                    .to_lowercase();
                let message = trailing.map(|s| s.to_string()).filter(|s| !s.is_empty());
                return IrcEvent::Sub {
                    channel: chan,
                    subscriber,
                    message,
                };
            }
            IrcEvent::Other
        }
        "NOTICE" => {
            let chan = parts
                .next()
                .unwrap_or("")
                .trim_start_matches('#')
                .to_lowercase();
            let message = trailing.unwrap_or("").to_string();
            IrcEvent::Notice {
                channel: chan,
                message,
            }
        }
        _ => IrcEvent::Other,
    }
}
