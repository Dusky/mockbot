use crate::db::{ChannelRecord, TtsLogRow};
use crate::markov::{BrainWeight, GenerationResult};
use crate::state::{AppState, LiveEvent, StatusSnapshot};
use anyhow::Result;
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, BorderType, Borders, List, ListItem, Paragraph, Row, Table, Tabs, Wrap},
    Frame, Terminal,
};
use serde::Deserialize;
use std::{io, sync::Arc, time::Duration};

// Warm "chassis" palette matching mockbot.xyz WebUI
const INK: Color = Color::Rgb(244, 240, 230);
const INK_2: Color = Color::Rgb(199, 193, 180);
const MUTED: Color = Color::Rgb(133, 127, 114);
const DIM: Color = Color::Rgb(92, 86, 75);
const LINE_COLOR: Color = Color::Rgb(61, 57, 50);
const AMBER: Color = Color::Rgb(212, 145, 61);
const GREEN: Color = Color::Rgb(98, 163, 100);
const RED: Color = Color::Rgb(201, 91, 75);

#[derive(Clone, Copy, PartialEq, Eq)]
enum ActiveTab {
    Dashboard = 0,
    Channels = 1,
    BarkTts = 2,
    BrainLab = 3,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum InputMode {
    Normal,
    AddChannel,
    SeedInput,
}

#[derive(Deserialize)]
struct ApiResp<T> {
    ok: bool,
    message: String,
    result: Option<T>,
}

#[derive(Deserialize)]
struct MultiGenResp {
    ok: bool,
    message: String,
    #[serde(default)]
    results: Vec<GenerationResult>,
}

struct RemoteTuiApp {
    base_url: String,
    token: String,
    snap: StatusSnapshot,
    events: Vec<LiveEvent>,
    tts_logs: Vec<TtsLogRow>,
    tab: ActiveTab,
    input_mode: InputMode,
    input_buffer: String,
    selected_channel_idx: usize,
    selected_brain_idx: usize,
    seed_phrase: String,
    lab_samples: Vec<GenerationResult>,
    status_toast: String,
    connected_ok: bool,
}

impl RemoteTuiApp {
    fn new(base_url: String, token: String) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            token,
            snap: StatusSnapshot {
                bot_nick: "mockbot".to_string(),
                owner: "firestarman".to_string(),
                irc_connected: false,
                tts_ready: false,
                tts_busy: false,
                tts_queue_len: 0,
                public_join_enabled: true,
                uptime_secs: 0,
                total_channels: 0,
                joined_channels: 0,
                vocab_size: 0,
                brains: Vec::new(),
                channels: Vec::new(),
            },
            events: Vec::new(),
            tts_logs: Vec::new(),
            tab: ActiveTab::Dashboard,
            input_mode: InputMode::Normal,
            input_buffer: String::new(),
            selected_channel_idx: 0,
            selected_brain_idx: 0,
            seed_phrase: String::new(),
            lab_samples: Vec::new(),
            status_toast: "[1-4] View  [↑/↓] Select  [g] Speak  [t] TTS  [x] Skip TTS  [v] Auto-speak  [b] Bark  [m] Mix  [/] Seed  [q] Quit".to_string(),
            connected_ok: false,
        }
    }

    fn selected_channel(&self) -> Option<&ChannelRecord> {
        if self.snap.channels.is_empty() {
            return None;
        }
        let idx = self
            .selected_channel_idx
            .min(self.snap.channels.len().saturating_sub(1));
        Some(&self.snap.channels[idx].record)
    }

    async fn refresh(&mut self) {
        if let Some(body) = api_get(&self.base_url, "/api/status", &self.token).await {
            if let Ok(s) = serde_json::from_str::<StatusSnapshot>(&body) {
                self.snap = s;
                self.connected_ok = true;
            }
        } else {
            self.connected_ok = false;
        }

        if let Some(body) = api_get(&self.base_url, "/api/events", &self.token).await {
            if let Ok(evs) = serde_json::from_str::<Vec<LiveEvent>>(&body) {
                self.events = evs;
            }
        }

        if self.tab == ActiveTab::BarkTts {
            if let Some(body) = api_get(&self.base_url, "/api/tts/logs?limit=25", &self.token).await
            {
                if let Ok(resp) = serde_json::from_str::<ApiResp<Vec<TtsLogRow>>>(&body) {
                    if let Some(rows) = resp.result {
                        self.tts_logs = rows;
                    }
                }
            }
        }
    }

    async fn roll_remote_samples(&mut self) {
        if self.snap.brains.is_empty() {
            return;
        }
        let idx = self
            .selected_brain_idx
            .min(self.snap.brains.len().saturating_sub(1));
        let brain_name = self.snap.brains[idx].name.clone();
        let brains = if brain_name == "global" {
            vec![BrainWeight {
                brain: "global".to_string(),
                weight: 1.0,
            }]
        } else {
            vec![
                BrainWeight {
                    brain: brain_name.clone(),
                    weight: 0.80,
                },
                BrainWeight {
                    brain: "global".to_string(),
                    weight: 0.20,
                },
            ]
        };
        let seed = if self.seed_phrase.trim().is_empty() {
            None
        } else {
            Some(self.seed_phrase.trim().to_string())
        };
        let payload = serde_json::json!({
            "brains": brains,
            "order": 2,
            "temperature": 1.0,
            "seed": seed,
            "count": 5
        })
        .to_string();

        if let Some(body) =
            api_post(&self.base_url, "/api/public/generate", &self.token, &payload).await
        {
            if let Ok(resp) = serde_json::from_str::<MultiGenResp>(&body) {
                if resp.ok {
                    self.lab_samples = resp.results;
                    self.status_toast = format!(
                        "Generated {} samples from '{}'{}.",
                        self.lab_samples.len(),
                        brain_name,
                        if self.seed_phrase.is_empty() {
                            String::new()
                        } else {
                            format!(" (seed: {})", self.seed_phrase)
                        }
                    );
                } else {
                    self.status_toast = resp.message;
                }
            }
        }
    }

    async fn update_selected_channel<F>(&mut self, mutate: F)
    where
        F: FnOnce(&mut ChannelRecord) -> String,
    {
        let Some(mut rec) = self.selected_channel().cloned() else {
            return;
        };
        let desc = mutate(&mut rec);
        if let Ok(json_body) = serde_json::to_string(&rec) {
            if let Some(resp_body) =
                api_post(&self.base_url, "/api/admin/channel", &self.token, &json_body).await
            {
                if let Ok(r) = serde_json::from_str::<ApiResp<serde_json::Value>>(&resp_body) {
                    if r.ok {
                        self.status_toast = format!("#{} -> {}", rec.channel_name, desc);
                    } else {
                        self.status_toast = r.message;
                    }
                }
            }
        }
        self.refresh().await;
    }
}

pub async fn run_remote_tui(
    base_url: String,
    token: String,
    dump: Option<usize>,
) -> Result<()> {
    let mut app = RemoteTuiApp::new(base_url, token);
    app.refresh().await;
    app.roll_remote_samples().await;
    if self_needs_tts(&app) {
        if let Some(body) = api_get(&app.base_url, "/api/tts/logs?limit=25", &app.token).await {
            if let Ok(resp) = serde_json::from_str::<ApiResp<Vec<TtsLogRow>>>(&body) {
                if let Some(rows) = resp.result {
                    app.tts_logs = rows;
                }
            }
        }
    }

    if let Some(tab_num) = dump {
        app.tab = match tab_num {
            2 => ActiveTab::Channels,
            3 => ActiveTab::BarkTts,
            4 => ActiveTab::BrainLab,
            _ => ActiveTab::Dashboard,
        };
        let backend = ratatui::backend::TestBackend::new(120, 28);
        let mut terminal = Terminal::new(backend)?;
        terminal.draw(|f| draw_remote_ui(f, &app))?;
        let buf = terminal.backend().buffer();
        let mut out = String::new();
        for y in 0..buf.area.height {
            for x in 0..buf.area.width {
                out.push_str(buf[(x, y)].symbol());
            }
            out.push('\n');
        }
        print!("{}", out);
        return Ok(());
    }

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let res = remote_event_loop(&mut terminal, &mut app).await;

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    res
}

fn self_needs_tts(app: &RemoteTuiApp) -> bool {
    app.tts_logs.is_empty()
}

pub async fn run_tui(state: Arc<AppState>) -> Result<()> {
    let port = state
        .config
        .web
        .bind_addr
        .rsplit(':')
        .next()
        .and_then(|p| p.parse::<u16>().ok())
        .unwrap_or(5002);
    let url = format!("http://127.0.0.1:{}", port);
    let token = state.config.web.admin_token.clone();
    run_remote_tui(url, token, None).await
}

pub fn render_snapshot_text(state: Arc<AppState>, tab_num: usize) -> String {
    let snap = state.status_snapshot();
    let events: Vec<LiveEvent> = state.recent_events.read().unwrap().iter().cloned().collect();
    let mut app = RemoteTuiApp::new("http://127.0.0.1:5002".to_string(), String::new());
    app.snap = snap;
    app.events = events;
    app.connected_ok = true;
    app.tab = match tab_num {
        2 => ActiveTab::Channels,
        3 => ActiveTab::BarkTts,
        4 => ActiveTab::BrainLab,
        _ => ActiveTab::Dashboard,
    };
    let backend = ratatui::backend::TestBackend::new(120, 26);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal.draw(|f| draw_remote_ui(f, &app)).unwrap();
    let buf = terminal.backend().buffer();
    let mut out = String::new();
    for y in 0..buf.area.height {
        for x in 0..buf.area.width {
            out.push_str(buf[(x, y)].symbol());
        }
        out.push('\n');
    }
    out
}

async fn remote_event_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut RemoteTuiApp,
) -> Result<()> {
    let mut last_poll = std::time::Instant::now();

    loop {
        terminal.draw(|f| draw_remote_ui(f, app))?;

        if last_poll.elapsed() >= Duration::from_millis(950) {
            app.refresh().await;
            last_poll = std::time::Instant::now();
        }

        if event::poll(Duration::from_millis(80))? {
            if let Event::Key(key) = event::read()? {
                if key.kind != KeyEventKind::Press {
                    continue;
                }

                if key.modifiers.contains(KeyModifiers::CONTROL)
                    && key.code == KeyCode::Char('c')
                {
                    return Ok(());
                }

                match app.input_mode {
                    InputMode::AddChannel => match key.code {
                        KeyCode::Esc => {
                            app.input_mode = InputMode::Normal;
                            app.input_buffer.clear();
                            app.status_toast = "Cancelled.".to_string();
                        }
                        KeyCode::Enter => {
                            let ch = app.input_buffer.trim().trim_start_matches('#').to_lowercase();
                            app.input_mode = InputMode::Normal;
                            app.input_buffer.clear();
                            if !ch.is_empty() {
                                let payload = serde_json::json!({ "channel": ch }).to_string();
                                if let Some(body) = api_post(
                                    &app.base_url,
                                    "/api/public/join",
                                    &app.token,
                                    &payload,
                                )
                                .await
                                {
                                    if let Ok(r) =
                                        serde_json::from_str::<ApiResp<serde_json::Value>>(&body)
                                    {
                                        app.status_toast = r.message;
                                    }
                                }
                                app.refresh().await;
                            }
                        }
                        KeyCode::Backspace => {
                            app.input_buffer.pop();
                        }
                        KeyCode::Char(c) => {
                            app.input_buffer.push(c);
                        }
                        _ => {}
                    },

                    InputMode::SeedInput => match key.code {
                        KeyCode::Esc => {
                            app.input_mode = InputMode::Normal;
                            app.input_buffer.clear();
                        }
                        KeyCode::Enter => {
                            app.seed_phrase = app.input_buffer.trim().to_string();
                            app.input_mode = InputMode::Normal;
                            app.input_buffer.clear();
                            app.roll_remote_samples().await;
                        }
                        KeyCode::Backspace => {
                            app.input_buffer.pop();
                        }
                        KeyCode::Char(c) => {
                            app.input_buffer.push(c);
                        }
                        _ => {}
                    },

                    InputMode::Normal => match key.code {
                        KeyCode::Char('q') => return Ok(()),
                        KeyCode::Char('1') => app.tab = ActiveTab::Dashboard,
                        KeyCode::Char('2') => app.tab = ActiveTab::Channels,
                        KeyCode::Char('3') => {
                            app.tab = ActiveTab::BarkTts;
                            app.refresh().await;
                        }
                        KeyCode::Char('4') => app.tab = ActiveTab::BrainLab,
                        KeyCode::Tab => {
                            app.tab = match app.tab {
                                ActiveTab::Dashboard => ActiveTab::Channels,
                                ActiveTab::Channels => ActiveTab::BarkTts,
                                ActiveTab::BarkTts => ActiveTab::BrainLab,
                                ActiveTab::BrainLab => ActiveTab::Dashboard,
                            };
                            if app.tab == ActiveTab::BarkTts {
                                app.refresh().await;
                            }
                        }
                        KeyCode::Up | KeyCode::Char('k') => match app.tab {
                            ActiveTab::Dashboard | ActiveTab::Channels | ActiveTab::BarkTts => {
                                app.selected_channel_idx =
                                    app.selected_channel_idx.saturating_sub(1);
                            }
                            ActiveTab::BrainLab => {
                                app.selected_brain_idx = app.selected_brain_idx.saturating_sub(1);
                                app.roll_remote_samples().await;
                            }
                        },
                        KeyCode::Down | KeyCode::Char('j') => match app.tab {
                            ActiveTab::Dashboard | ActiveTab::Channels | ActiveTab::BarkTts => {
                                if app.selected_channel_idx + 1 < app.snap.channels.len() {
                                    app.selected_channel_idx += 1;
                                }
                            }
                            ActiveTab::BrainLab => {
                                if app.selected_brain_idx + 1 < app.snap.brains.len() {
                                    app.selected_brain_idx += 1;
                                    app.roll_remote_samples().await;
                                }
                            }
                        },
                        KeyCode::Char('a') => {
                            app.input_mode = InputMode::AddChannel;
                            app.input_buffer.clear();
                        }
                        KeyCode::Char('/') => {
                            app.input_mode = InputMode::SeedInput;
                            app.input_buffer = app.seed_phrase.clone();
                        }
                        KeyCode::Char(' ') | KeyCode::Enter => {
                            if app.tab == ActiveTab::BrainLab {
                                app.roll_remote_samples().await;
                            } else if let Some(ch) = app.selected_channel().map(|r| r.channel_name.clone()) {
                                trigger_remote_speak(app, &ch).await;
                            }
                        }
                        KeyCode::Char('g') => {
                            if let Some(ch) = app.selected_channel().map(|r| r.channel_name.clone())
                            {
                                trigger_remote_speak(app, &ch).await;
                            }
                        }
                        KeyCode::Char('t') => {
                            if let Some(rec) = app.selected_channel().cloned() {
                                trigger_remote_tts(app, &rec).await;
                            }
                        }
                        KeyCode::Char('x') => {
                            if let Some(ch) = app.selected_channel().map(|r| r.channel_name.clone())
                            {
                                let payload = serde_json::json!({ "channel": ch }).to_string();
                                if let Some(body) = api_post(
                                    &app.base_url,
                                    "/api/tts/skip",
                                    &app.token,
                                    &payload,
                                )
                                .await
                                {
                                    if let Ok(r) =
                                        serde_json::from_str::<ApiResp<serde_json::Value>>(&body)
                                    {
                                        app.status_toast = r.message;
                                    }
                                }
                                app.refresh().await;
                            }
                        }
                        KeyCode::Char('v') => {
                            app.update_selected_channel(|r| {
                                r.voice_enabled = !r.voice_enabled;
                                format!(
                                    "Auto-speak {}",
                                    if r.voice_enabled { "ON" } else { "MUTED" }
                                )
                            })
                            .await;
                        }
                        KeyCode::Char('b') => {
                            app.update_selected_channel(|r| {
                                r.tts_enabled = !r.tts_enabled;
                                format!(
                                    "Bark TTS {}",
                                    if r.tts_enabled { "ENABLED" } else { "OFF" }
                                )
                            })
                            .await;
                        }
                        KeyCode::Char('l') => {
                            app.update_selected_channel(|r| {
                                r.only_when_live = !r.only_when_live;
                                format!(
                                    "Only when live: {}",
                                    if r.only_when_live { "ON" } else { "OFF" }
                                )
                            })
                            .await;
                        }
                        KeyCode::Char('i') => {
                            app.update_selected_channel(|r| {
                                r.bot_identity = if r.bot_identity == "ansv" {
                                    "mockbot".to_string()
                                } else {
                                    "ansv".to_string()
                                };
                                format!("Bot identity -> @{}", r.bot_identity)
                            })
                            .await;
                        }
                        KeyCode::Char('p') => {
                            app.update_selected_channel(|r| {
                                r.join_enabled = !r.join_enabled;
                                format!(
                                    "{}",
                                    if r.join_enabled {
                                        "Joined channel"
                                    } else {
                                        "Parted channel"
                                    }
                                )
                            })
                            .await;
                        }
                        KeyCode::Char('+') | KeyCode::Char('=') => {
                            app.update_selected_channel(|r| {
                                r.lines_between_messages =
                                    (r.lines_between_messages + 10).min(1000);
                                format!("Min lines -> {}", r.lines_between_messages)
                            })
                            .await;
                        }
                        KeyCode::Char('-') => {
                            app.update_selected_channel(|r| {
                                r.lines_between_messages =
                                    r.lines_between_messages.saturating_sub(10).max(5);
                                format!("Min lines -> {}", r.lines_between_messages)
                            })
                            .await;
                        }
                        KeyCode::Char(']') => {
                            app.update_selected_channel(|r| {
                                r.time_between_messages = (r.time_between_messages + 5).min(240);
                                format!("Min cooldown -> {}m", r.time_between_messages)
                            })
                            .await;
                        }
                        KeyCode::Char('[') => {
                            app.update_selected_channel(|r| {
                                r.time_between_messages =
                                    r.time_between_messages.saturating_sub(5);
                                format!("Min cooldown -> {}m", r.time_between_messages)
                            })
                            .await;
                        }
                        KeyCode::Char('o') => {
                            app.update_selected_channel(|r| {
                                r.markov_order = match r.markov_order {
                                    1 => 2,
                                    2 => 3,
                                    _ => 1,
                                };
                                format!("Markov order -> {}", r.markov_order)
                            })
                            .await;
                        }
                        KeyCode::Char('m') => {
                            app.update_selected_channel(|r| {
                                let cb = format!("channel:{}", r.channel_name);
                                let first_w = r
                                    .brain_mix
                                    .first()
                                    .map(|b| (b.brain.as_str(), (b.weight * 100.0).round() as i32))
                                    .unwrap_or(("", 0));
                                let (next_mix, label) = match first_w {
                                    (_, 70) => (
                                        vec![BrainWeight {
                                            brain: cb,
                                            weight: 1.0,
                                        }],
                                        "100% Channel Only",
                                    ),
                                    (b, 100) if b.starts_with("channel:") => (
                                        vec![BrainWeight {
                                            brain: "global".to_string(),
                                            weight: 1.0,
                                        }],
                                        "100% Global",
                                    ),
                                    _ => (
                                        vec![
                                            BrainWeight {
                                                brain: cb,
                                                weight: 0.70,
                                            },
                                            BrainWeight {
                                                brain: "global".to_string(),
                                                weight: 0.30,
                                            },
                                        ],
                                        "70% Channel + 30% Global",
                                    ),
                                };
                                r.brain_mix = next_mix;
                                format!("Brain mix -> {}", label)
                            })
                            .await;
                        }
                        _ => {}
                    },
                }
            }
        }
    }
}

async fn trigger_remote_speak(app: &mut RemoteTuiApp, channel: &str) {
    let seed = if app.seed_phrase.trim().is_empty() {
        None
    } else {
        Some(app.seed_phrase.trim().to_string())
    };
    let payload = serde_json::json!({
        "channel": channel,
        "seed": seed
    })
    .to_string();
    if let Some(body) = api_post(&app.base_url, "/api/admin/speak", &app.token, &payload).await {
        if let Ok(r) = serde_json::from_str::<ApiResp<GenerationResult>>(&body) {
            app.status_toast = r.message;
        }
    }
    app.refresh().await;
}

async fn trigger_remote_tts(app: &mut RemoteTuiApp, rec: &ChannelRecord) {
    let gen_payload = serde_json::json!({
        "brains": rec.brain_mix,
        "order": rec.markov_order,
        "temperature": rec.temperature,
        "count": 1
    })
    .to_string();
    let Some(gen_body) =
        api_post(&app.base_url, "/api/public/generate", &app.token, &gen_payload).await
    else {
        return;
    };
    let Ok(gen_resp) = serde_json::from_str::<MultiGenResp>(&gen_body) else {
        return;
    };
    let Some(first) = gen_resp.results.first() else {
        app.status_toast = "Could not generate Markov line for TTS.".to_string();
        return;
    };
    let tts_payload = serde_json::json!({
        "channel": rec.channel_name,
        "text": first.text,
        "voice_preset": rec.voice_preset
    })
    .to_string();
    if let Some(body) = api_post(&app.base_url, "/api/tts/generate", &app.token, &tts_payload).await
    {
        if let Ok(r) = serde_json::from_str::<ApiResp<serde_json::Value>>(&body) {
            app.status_toast = r.message;
        }
    }
    app.refresh().await;
}

fn panel_block<'a>(title: impl std::fmt::Display) -> Block<'a> {
    Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Plain)
        .border_style(Style::default().fg(LINE_COLOR))
        .title(Span::styled(
            format!(" {} ", title),
            Style::default().fg(INK).add_modifier(Modifier::BOLD),
        ))
}

fn draw_remote_ui(f: &mut Frame, app: &RemoteTuiApp) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .split(f.area());

    let header_chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(62), Constraint::Percentage(38)])
        .split(chunks[0]);

    let irc_span = if !app.connected_ok {
        Span::styled(
            " ○ DISCONNECTED ",
            Style::default().fg(RED).add_modifier(Modifier::BOLD),
        )
    } else if app.snap.irc_connected {
        Span::styled(
            " ● IRC ONLINE ",
            Style::default().fg(GREEN).add_modifier(Modifier::BOLD),
        )
    } else {
        Span::styled(
            " ○ IRC OFFLINE ",
            Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
        )
    };

    let bark_span = if app.snap.tts_busy {
        Span::styled(
            format!(" ● BARK SYNTH (Q:{}) ", app.snap.tts_queue_len),
            Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
        )
    } else if app.snap.tts_ready {
        Span::styled(
            format!(" ● BARK READY (Q:{}) ", app.snap.tts_queue_len),
            Style::default().fg(GREEN),
        )
    } else {
        Span::styled(" ○ BARK LOADING ", Style::default().fg(MUTED))
    };

    let total_lines: usize = app.snap.brains.iter().map(|b| b.sentence_count).sum();

    let title_line = Line::from(vec![
        Span::styled(
            " mockbot ",
            Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
        ),
        irc_span,
        Span::styled("│", Style::default().fg(LINE_COLOR)),
        bark_span,
        Span::styled(
            format!(
                "│ Chans: {}/{} │ Corpus: {} │ Vocab: {}",
                app.snap.joined_channels,
                app.snap.total_channels,
                total_lines,
                app.snap.vocab_size
            ),
            Style::default().fg(INK_2),
        ),
    ]);
    let header_block = Paragraph::new(title_line).block(panel_block(&format!(
        "Server: {}",
        app.base_url
    )));
    f.render_widget(header_block, header_chunks[0]);

    let tab_titles = vec![
        Line::from(" 1 · Feed "),
        Line::from(" 2 · Channels "),
        Line::from(" 3 · Bark TTS "),
        Line::from(" 4 · Brains "),
    ];
    let tabs = Tabs::new(tab_titles)
        .block(panel_block("Views (Tab / 1-4)"))
        .select(app.tab as usize)
        .style(Style::default().fg(MUTED))
        .highlight_style(Style::default().fg(AMBER).add_modifier(Modifier::BOLD));
    f.render_widget(tabs, header_chunks[1]);

    match app.tab {
        ActiveTab::Dashboard => draw_dashboard_tab(f, chunks[1], app),
        ActiveTab::Channels => draw_channels_tab(f, chunks[1], app),
        ActiveTab::BarkTts => draw_bark_tab(f, chunks[1], app),
        ActiveTab::BrainLab => draw_brain_lab_tab(f, chunks[1], app),
    }

    let footer_text = match app.input_mode {
        InputMode::AddChannel => format!(
            " Join Twitch channel (Enter to confirm, Esc to cancel): #{}",
            app.input_buffer
        ),
        InputMode::SeedInput => format!(
            " Seed word/phrase (Enter to apply, Esc to cancel): {}",
            app.input_buffer
        ),
        InputMode::Normal => format!(" {}", app.status_toast),
    };
    let footer_style = if app.input_mode == InputMode::Normal {
        Style::default().fg(INK_2)
    } else {
        Style::default().fg(AMBER).add_modifier(Modifier::BOLD)
    };
    let footer = Paragraph::new(footer_text)
        .style(footer_style)
        .block(panel_block("Controls"));
    f.render_widget(footer, chunks[2]);
}

fn draw_dashboard_tab(f: &mut Frame, area: Rect, app: &RemoteTuiApp) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(area);

    let sel_idx = app
        .selected_channel_idx
        .min(app.snap.channels.len().saturating_sub(1));

    let items: Vec<ListItem> = app
        .snap
        .channels
        .iter()
        .enumerate()
        .map(|(i, c)| {
            let r = &c.record;
            let status_dot = if c.currently_joined { "●" } else { "○" };
            let dot_color = if c.currently_joined { GREEN } else { DIM };
            let bot_tag = if r.bot_identity == "ansv" {
                "ansv"
            } else {
                "mock"
            };
            let live_badge = if c.is_stream_live { "LIVE" } else { "idle" };
            let live_color = if c.is_stream_live { GREEN } else { DIM };
            let prefix = if i == sel_idx { "▸ " } else { "  " };
            let line = Line::from(vec![
                Span::styled(
                    prefix,
                    Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
                ),
                Span::styled(format!("{} ", status_dot), Style::default().fg(dot_color)),
                Span::styled(
                    format!("#{:<14}", r.channel_name),
                    Style::default()
                        .fg(if i == sel_idx { AMBER } else { INK })
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(format!("[{}] ", bot_tag), Style::default().fg(MUTED)),
                Span::styled(format!("{:<4} ", live_badge), Style::default().fg(live_color)),
                Span::styled(
                    format!(
                        "{:>3}/{:<3}L · {}m · {}",
                        c.lines_since_last_spoke,
                        r.lines_between_messages,
                        r.time_between_messages,
                        if r.voice_enabled { "ON " } else { "OFF" }
                    ),
                    Style::default().fg(INK_2),
                ),
                if r.tts_enabled {
                    Span::styled(" [TTS]", Style::default().fg(AMBER))
                } else {
                    Span::raw("")
                },
            ]);
            ListItem::new(line)
        })
        .collect();

    let chan_list = List::new(items).block(panel_block(
        "Channels (↑/↓ select · [g] speak · [t] TTS · [a] join)",
    ));
    f.render_widget(chan_list, cols[0]);

    let right_rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(8), Constraint::Length(6)])
        .split(cols[1]);

    let max_lines = (right_rows[0].height.saturating_sub(2)) as usize;
    let skip = app.events.len().saturating_sub(max_lines);
    let feed_items: Vec<ListItem> = app
        .events
        .iter()
        .skip(skip)
        .map(|ev| match ev {
            LiveEvent::ChatMessage {
                timestamp,
                channel,
                author,
                content,
                learned,
                ..
            } => {
                let ts = format_ts(*timestamp);
                ListItem::new(Line::from(vec![
                    Span::styled(format!("{} ", ts), Style::default().fg(DIM)),
                    Span::styled(format!("#{} ", channel), Style::default().fg(AMBER)),
                    Span::styled(
                        format!("{}: ", author),
                        Style::default().fg(INK).add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(content.clone(), Style::default().fg(INK_2)),
                    if *learned {
                        Span::styled(" ·", Style::default().fg(GREEN))
                    } else {
                        Span::raw("")
                    },
                ]))
            }
            LiveEvent::BotResponse {
                timestamp,
                channel,
                trigger,
                text,
                brains_used,
                ..
            } => {
                let ts = format_ts(*timestamp);
                ListItem::new(Line::from(vec![
                    Span::styled(format!("{} ", ts), Style::default().fg(DIM)),
                    Span::styled(format!("#{} ", channel), Style::default().fg(AMBER)),
                    Span::styled(
                        format!("BOT ({}) ", trigger),
                        Style::default().fg(GREEN).add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(
                        text.clone(),
                        Style::default().fg(INK).add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(
                        format!(" [{}]", brains_used.join("+")),
                        Style::default().fg(MUTED),
                    ),
                ]))
            }
            LiveEvent::SystemLog {
                timestamp,
                level,
                message,
            } => {
                let ts = format_ts(*timestamp);
                let lvl_color = match level.as_str() {
                    "ERROR" => RED,
                    "WARN" => AMBER,
                    _ => MUTED,
                };
                ListItem::new(Line::from(vec![
                    Span::styled(format!("{} ", ts), Style::default().fg(DIM)),
                    Span::styled(
                        format!("[{}] ", level),
                        Style::default().fg(lvl_color).add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(message.clone(), Style::default().fg(INK_2)),
                ]))
            }
            LiveEvent::ChannelStatusChanged { channel, joined } => ListItem::new(Line::from(
                Span::styled(
                    format!(
                        "── #{} {} ──",
                        channel,
                        if *joined { "JOINED" } else { "PARTED" }
                    ),
                    Style::default().fg(DIM),
                ),
            )),
            LiveEvent::TtsGenerated {
                timestamp,
                channel,
                voice_preset,
                text,
                duration_secs,
                ..
            } => {
                let ts = format_ts(*timestamp);
                ListItem::new(Line::from(vec![
                    Span::styled(format!("{} ", ts), Style::default().fg(DIM)),
                    Span::styled(format!("#{} ", channel), Style::default().fg(AMBER)),
                    Span::styled(
                        format!("BARK [{}] ", voice_preset),
                        Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(text.clone(), Style::default().fg(INK)),
                    Span::styled(format!(" ({:.1}s)", duration_secs), Style::default().fg(MUTED)),
                ]))
            }
            LiveEvent::TtsSkip { channel } => ListItem::new(Line::from(Span::styled(
                format!("── #{} TTS QUEUE CLEARED & SKIPPED ──", channel),
                Style::default().fg(AMBER),
            ))),
        })
        .collect();

    let feed_widget = List::new(feed_items).block(panel_block("Live Chat & Activity Log"));
    f.render_widget(feed_widget, right_rows[0]);

    // Selected Channel Inspector
    let inspector_lines = if let Some(c) = app.snap.channels.get(sel_idx) {
        let r = &c.record;
        let mix_str: Vec<String> = r
            .brain_mix
            .iter()
            .map(|b| format!("{}:{:.0}%", b.brain, b.weight * 100.0))
            .collect();
        let bot_handle = if r.bot_identity == "ansv" {
            "@anormalskipviewer (!ansv)"
        } else {
            "@mockbot (!mockbot)"
        };
        vec![
            Line::from(vec![
                Span::styled(" Channel: ", Style::default().fg(MUTED)),
                Span::styled(
                    format!("#{} ", r.channel_name),
                    Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
                ),
                Span::styled(" │ Bot: ", Style::default().fg(MUTED)),
                Span::styled(bot_handle, Style::default().fg(INK)),
                Span::styled(" │ Stream: ", Style::default().fg(MUTED)),
                Span::styled(
                    if c.is_stream_live { "LIVE" } else { "Offline" },
                    Style::default().fg(if c.is_stream_live { GREEN } else { MUTED }),
                ),
            ]),
            Line::from(vec![
                Span::styled(" Cadence: ", Style::default().fg(MUTED)),
                Span::styled(
                    format!(
                        "{} lines AND {}m cooldown (Live-only: {})",
                        r.lines_between_messages,
                        r.time_between_messages,
                        if r.only_when_live { "yes" } else { "no" }
                    ),
                    Style::default().fg(INK_2),
                ),
                Span::styled(" │ Bark Voice: ", Style::default().fg(MUTED)),
                Span::styled(&r.voice_preset, Style::default().fg(AMBER)),
            ]),
            Line::from(vec![
                Span::styled(" Brain Mix: ", Style::default().fg(MUTED)),
                Span::styled(mix_str.join(" + "), Style::default().fg(INK_2)),
                Span::styled(
                    format!(" │ Order: {} · Temp: {:.1}", r.markov_order, r.temperature),
                    Style::default().fg(MUTED),
                ),
            ]),
        ]
    } else {
        vec![Line::from(" No channel selected.")]
    };

    let inspector = Paragraph::new(inspector_lines).block(panel_block(
        "Selected Channel ([v] Auto-speak · [b] Bark TTS · [l] Live-only · [i] Bot Account · [+/-] Lines)",
    ));
    f.render_widget(inspector, right_rows[1]);
}

fn draw_channels_tab(f: &mut Frame, area: Rect, app: &RemoteTuiApp) {
    let sel_idx = app
        .selected_channel_idx
        .min(app.snap.channels.len().saturating_sub(1));

    let rows: Vec<Row> = app
        .snap
        .channels
        .iter()
        .enumerate()
        .map(|(i, c)| {
            let r = &c.record;
            let mix_str: Vec<String> = r
                .brain_mix
                .iter()
                .map(|b| format!("{}:{:.0}%", b.brain, b.weight * 100.0))
                .collect();
            let style = if i == sel_idx {
                Style::default().fg(AMBER).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(INK)
            };
            Row::new(vec![
                format!("{}{}", if i == sel_idx { "▸ #" } else { "  #" }, r.channel_name),
                if r.bot_identity == "ansv" {
                    "@ansv".to_string()
                } else {
                    "@mockbot".to_string()
                },
                if c.is_stream_live {
                    "LIVE".to_string()
                } else {
                    "offline".to_string()
                },
                if r.voice_enabled { "ON" } else { "Muted" }.to_string(),
                if r.only_when_live { "Yes" } else { "No" }.to_string(),
                if r.tts_enabled { "ON" } else { "Off" }.to_string(),
                format!("{}/{}", c.lines_since_last_spoke, r.lines_between_messages),
                format!("{}m", r.time_between_messages),
                format!("O{} · {:.1}", r.markov_order, r.temperature),
                mix_str.join(", "),
            ])
            .style(style)
        })
        .collect();

    let table = Table::new(
        rows,
        [
            Constraint::Length(18),
            Constraint::Length(9),
            Constraint::Length(8),
            Constraint::Length(7),
            Constraint::Length(6),
            Constraint::Length(6),
            Constraint::Length(9),
            Constraint::Length(6),
            Constraint::Length(10),
            Constraint::Fill(1),
        ],
    )
    .header(
        Row::new(vec![
            "Channel",
            "Bot",
            "Stream",
            "Speak",
            "Live?",
            "TTS",
            "Lines",
            "Timer",
            "Ord/Tmp",
            "Brain Mix",
        ])
        .style(Style::default().fg(MUTED).add_modifier(Modifier::BOLD)),
    )
    .block(panel_block(
        "Channels ([v] Speak · [l] Live? · [b] TTS · [i] Bot · [+/-] Lines · [[/]] Timer · [o] Order · [m] Mix · [p] Join/Part)",
    ));

    f.render_widget(table, area);
}

fn draw_bark_tab(f: &mut Frame, area: Rect, app: &RemoteTuiApp) {
    let rows: Vec<Row> = app
        .tts_logs
        .iter()
        .map(|row| {
            Row::new(vec![
                format_ts(row.created_at),
                format!("#{}", row.channel_name),
                row.voice_preset.clone(),
                format!("{:.1}s", row.duration_secs),
                row.text_content.clone(),
            ])
            .style(Style::default().fg(INK_2))
        })
        .collect();

    let table = Table::new(
        rows,
        [
            Constraint::Length(10),
            Constraint::Length(16),
            Constraint::Length(22),
            Constraint::Length(8),
            Constraint::Fill(1),
        ],
    )
    .header(
        Row::new(vec!["Time", "Channel", "Voice Preset", "Length", "Spoken Text"])
            .style(Style::default().fg(MUTED).add_modifier(Modifier::BOLD)),
    )
    .block(panel_block(&format!(
        "Bark TTS History ([t] Generate TTS for #{} · [x] Clear Queue & Skip OBS Audio)",
        app.selected_channel()
            .map(|r| r.channel_name.as_str())
            .unwrap_or("channel")
    )));

    f.render_widget(table, area);
}

fn draw_brain_lab_tab(f: &mut Frame, area: Rect, app: &RemoteTuiApp) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(area);

    let sel_idx = app
        .selected_brain_idx
        .min(app.snap.brains.len().saturating_sub(1));

    let rows: Vec<Row> = app
        .snap
        .brains
        .iter()
        .enumerate()
        .map(|(i, b)| {
            let style = if i == sel_idx {
                Style::default().fg(AMBER).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(INK)
            };
            Row::new(vec![
                format!("{}{}", if i == sel_idx { "▸ " } else { "  " }, b.name),
                format!("{}", b.sentence_count),
                format!("{}", b.total_tokens),
                format!("{}", b.unique_tokens),
                format!("{}/{}/{}", b.states_order_1, b.states_order_2, b.states_order_3),
            ])
            .style(style)
        })
        .collect();

    let table = Table::new(
        rows,
        [
            Constraint::Length(24),
            Constraint::Length(8),
            Constraint::Length(9),
            Constraint::Length(8),
            Constraint::Fill(1),
        ],
    )
    .header(
        Row::new(vec!["Brain", "Lines", "Tokens", "Vocab", "O1/O2/O3 States"])
            .style(Style::default().fg(MUTED).add_modifier(Modifier::BOLD)),
    )
    .block(panel_block("Brains (↑/↓ select · [Space/Enter] roll 5x)"));
    f.render_widget(table, cols[0]);

    let mut lines: Vec<Line> = Vec::new();
    let selected_brain = app
        .snap
        .brains
        .get(sel_idx)
        .map(|b| b.name.as_str())
        .unwrap_or("global");

    lines.push(Line::from(vec![
        Span::styled(" Target Brain: ", Style::default().fg(MUTED)),
        Span::styled(
            selected_brain,
            Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
        ),
        Span::styled("   │   Seed ([/]): ", Style::default().fg(MUTED)),
        Span::styled(
            if app.seed_phrase.is_empty() {
                "none"
            } else {
                &app.seed_phrase
            },
            Style::default().fg(INK).add_modifier(Modifier::BOLD),
        ),
    ]));
    lines.push(Line::from(""));

    for (idx, sample) in app.lab_samples.iter().enumerate() {
        lines.push(Line::from(vec![
            Span::styled(
                format!(" {:02} │ ", idx + 1),
                Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                sample.text.clone(),
                Style::default().fg(INK).add_modifier(Modifier::BOLD),
            ),
        ]));
        lines.push(Line::from(Span::styled(
            format!(
                "      brains: [{}] · {} words · {} attempts",
                sample.brains_used.join("+"),
                sample.word_count,
                sample.attempts_used
            ),
            Style::default().fg(MUTED),
        )));
        lines.push(Line::from(""));
    }

    let lab_widget = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .block(panel_block("Sandbox ([Space/Enter] Roll 5x · [/] Seed)"));
    f.render_widget(lab_widget, cols[1]);
}

async fn api_get(base_url: &str, path: &str, token: &str) -> Option<String> {
    let full = format!("{}{}", base_url, path);
    let out = tokio::process::Command::new("curl")
        .arg("-s")
        .arg("-m")
        .arg("4")
        .arg("-H")
        .arg(format!("X-Admin-Token: {}", token))
        .arg("-H")
        .arg(format!("X-Auth-Token: {}", token))
        .arg(&full)
        .output()
        .await
        .ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).to_string())
}

async fn api_post(base_url: &str, path: &str, token: &str, json_body: &str) -> Option<String> {
    let full = format!("{}{}", base_url, path);
    let out = tokio::process::Command::new("curl")
        .arg("-s")
        .arg("-m")
        .arg("6")
        .arg("-X")
        .arg("POST")
        .arg("-H")
        .arg("Content-Type: application/json")
        .arg("-H")
        .arg(format!("X-Admin-Token: {}", token))
        .arg("-H")
        .arg(format!("X-Auth-Token: {}", token))
        .arg("-d")
        .arg(json_body)
        .arg(&full)
        .output()
        .await
        .ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).to_string())
}

fn format_ts(epoch: i64) -> String {
    use chrono::{Local, TimeZone};
    Local
        .timestamp_opt(epoch, 0)
        .single()
        .map(|dt| dt.format("%H:%M:%S").to_string())
        .unwrap_or_else(|| "00:00:00".to_string())
}
