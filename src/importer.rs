use crate::db::{ChannelRecord, Database};
use crate::markov::{BrainKind, BrainWeight, MarkovEngine};
use anyhow::Result;
use rusqlite::Connection;
use serde_json::Value;
use std::collections::HashSet;
use std::fs;
use std::path::Path;
use tracing::info;

#[derive(Debug, Default)]
pub struct ImportSummary {
    pub channels_imported: usize,
    pub brains_created: usize,
    pub lines_imported: usize,
}

pub fn import_legacy_directory(
    base_dir: &Path,
    db: &Database,
    engine: &mut MarkovEngine,
) -> Result<ImportSummary> {
    let mut summary = ImportSummary::default();
    let mut seen_per_brain: HashSet<(String, String)> = HashSet::new();
    let mut batch: Vec<(String, String, String, String)> = Vec::with_capacity(8192);

    // 1. Import channel_configs and messages from legacy messages.db if present
    let legacy_db_path = base_dir.join("messages.db");
    if legacy_db_path.exists() {
        if let Ok(legacy_conn) = Connection::open(&legacy_db_path) {
            if let Ok(mut stmt) = legacy_conn.prepare(
                "SELECT channel_name, voice_enabled, join_channel, owner, trusted_users, ignored_users,
                        use_general_model, lines_between_messages, time_between_messages
                 FROM channel_configs",
            ) {
                if let Ok(rows) = stmt.query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, i32>(1).unwrap_or(1) != 0,
                        r.get::<_, i32>(2).unwrap_or(1) != 0,
                        r.get::<_, Option<String>>(3)?.unwrap_or_default(),
                        r.get::<_, Option<String>>(4)?.unwrap_or_default(),
                        r.get::<_, Option<String>>(5)?.unwrap_or_default(),
                        r.get::<_, i32>(6).unwrap_or(1) != 0,
                        r.get::<_, i64>(7).unwrap_or(50).max(0) as u32,
                        r.get::<_, i64>(8).unwrap_or(15).max(0) as u32,
                    ))
                }) {
                    for row in rows.flatten() {
                        let (
                            raw_name,
                            voice_enabled,
                            join_channel,
                            owner,
                            trusted_csv,
                            ignored_csv,
                            use_general,
                            lines_between,
                            time_between,
                        ) = row;
                        let clean = raw_name.trim().trim_start_matches('#').to_lowercase();
                        if clean.is_empty() {
                            continue;
                        }
                        let mut rec = ChannelRecord::default_for(&clean, true, "imported");
                        rec.voice_enabled = voice_enabled;
                        rec.join_enabled = join_channel;
                        if !owner.trim().is_empty() {
                            rec.owner = owner.trim().to_lowercase();
                        }
                        rec.trusted_users = trusted_csv
                            .split(',')
                            .map(|s| s.trim().to_lowercase())
                            .filter(|s| !s.is_empty())
                            .collect();
                        rec.ignored_users = ignored_csv
                            .split(',')
                            .map(|s| s.trim().to_lowercase())
                            .filter(|s| !s.is_empty())
                            .collect();
                        rec.lines_between_messages = lines_between;
                        rec.time_between_messages = time_between;
                        // Give channels a smart blended brain mix by default
                        rec.brain_mix = if use_general {
                            vec![
                                BrainWeight {
                                    brain: format!("channel:{}", clean),
                                    weight: 0.65,
                                },
                                BrainWeight {
                                    brain: "global".to_string(),
                                    weight: 0.35,
                                },
                            ]
                        } else {
                            vec![
                                BrainWeight {
                                    brain: format!("channel:{}", clean),
                                    weight: 0.90,
                                },
                                BrainWeight {
                                    brain: "global".to_string(),
                                    weight: 0.10,
                                },
                            ]
                        };
                        db.upsert_channel(&rec)?;
                        summary.channels_imported += 1;
                    }
                }
            }

            // Also import non-bot messages from legacy messages table
            if let Ok(mut stmt) = legacy_conn.prepare(
                "SELECT channel, author_name, message FROM messages WHERE is_bot_response = 0",
            ) {
                if let Ok(rows) = stmt.query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                    ))
                }) {
                    for row in rows.flatten() {
                        let (chan, author, msg) = row;
                        let clean_chan = chan.trim().trim_start_matches('#').to_lowercase();
                        if let Some(cleaned) = engine.filter.sanitize_for_training(&msg) {
                            let chan_brain = format!("channel:{}", clean_chan);
                            for b in [&chan_brain, "global"] {
                                if seen_per_brain.insert((b.to_string(), cleaned.clone())) {
                                    batch.push((
                                        b.to_string(),
                                        clean_chan.clone(),
                                        author.clone(),
                                        cleaned.clone(),
                                    ));
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 2. Import logs/*.txt (Clean line-by-line Twitch chat logs!)
    let mut channels_with_raw_logs: HashSet<String> = HashSet::new();
    let logs_dir = base_dir.join("logs");
    if logs_dir.is_dir() {
        if let Ok(entries) = fs::read_dir(&logs_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) != Some("txt") {
                    continue;
                }
                let stem = path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_lowercase();
                if stem.is_empty() || stem == "general_markov" {
                    continue;
                }

                channels_with_raw_logs.insert(stem.clone());
                let brain_name = format!("channel:{}", stem);
                db.upsert_brain_meta(
                    &brain_name,
                    BrainKind::Channel,
                    &format!("Imported corpus from {}.txt", stem),
                )?;
                engine.ensure_brain(&brain_name, BrainKind::Channel, "Imported channel corpus");
                summary.brains_created += 1;

                if let Ok(content) = fs::read_to_string(&path) {
                    for line in content.lines() {
                        if let Some(cleaned) = engine.filter.sanitize_for_training(line) {
                            for target in [brain_name.as_str(), "global"] {
                                if seen_per_brain.insert((target.to_string(), cleaned.clone())) {
                                    batch.push((
                                        target.to_string(),
                                        stem.clone(),
                                        "imported".to_string(),
                                        cleaned.clone(),
                                    ));
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 3. Import cache/*_model.json ONLY for channels that don't have raw logs/*.txt
    // (because markovify.Text glued unpunctuated chat lines together when building *_model.json)
    let cache_dir = base_dir.join("cache");
    if cache_dir.is_dir() {
        if let Ok(entries) = fs::read_dir(&cache_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                let fname = path
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_string();
                if !fname.ends_with("_model.json") {
                    continue;
                }
                let model_stem = fname.trim_end_matches("_model.json").to_lowercase();
                if model_stem.is_empty()
                    || model_stem == "general_markov"
                    || model_stem == "general"
                    || channels_with_raw_logs.contains(&model_stem)
                {
                    continue;
                }

                let brain_name = format!("channel:{}", model_stem);
                db.upsert_brain_meta(
                    &brain_name,
                    BrainKind::Channel,
                    &format!("Imported from {}", fname),
                )?;
                engine.ensure_brain(
                    &brain_name,
                    BrainKind::Channel,
                    &format!("Imported from {}", fname),
                );
                summary.brains_created += 1;

                if let Ok(raw_json) = fs::read_to_string(&path) {
                    if let Ok(val) = serde_json::from_str::<Value>(&raw_json) {
                        if let Some(sentences) =
                            val.get("parsed_sentences").and_then(|v| v.as_array())
                        {
                            for s_arr in sentences {
                                if let Some(words_arr) = s_arr.as_array() {
                                    let words: Vec<&str> =
                                        words_arr.iter().filter_map(|w| w.as_str()).collect();
                                    // Skip markovify run-on concatenations > 28 words
                                    if words.len() > 28 {
                                        continue;
                                    }
                                    let joined = words.join(" ");
                                    if let Some(cleaned) =
                                        engine.filter.sanitize_for_training(&joined)
                                    {
                                        if seen_per_brain
                                            .insert((brain_name.clone(), cleaned.clone()))
                                        {
                                            batch.push((
                                                brain_name.clone(),
                                                model_stem.clone(),
                                                "markovify_cache".to_string(),
                                                cleaned.clone(),
                                            ));
                                        }
                                        if seen_per_brain
                                            .insert(("global".to_string(), cleaned.clone()))
                                        {
                                            batch.push((
                                                "global".to_string(),
                                                model_stem.clone(),
                                                "markovify_cache".to_string(),
                                                cleaned,
                                            ));
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Flush batch to SQLite and feed into in-memory MarkovEngine
    db.upsert_brain_meta("global", BrainKind::Global, "Global multi-channel brain")?;
    if !batch.is_empty() {
        let inserted = db.insert_corpus_batch(&batch)?;
        for (brain_name, _origin, _author, content) in &batch {
            let targets = [brain_name.clone()];
            engine.learn_sanitized(content, &targets);
        }
        summary.lines_imported = inserted;
    }

    info!(
        "Legacy import from {:?} complete: {} channels, {} brains, {} corpus lines",
        base_dir, summary.channels_imported, summary.brains_created, summary.lines_imported
    );

    Ok(summary)
}
