use crate::markov::{BrainWeight, GenerationParams, GenerationResult};
use crate::state::{AppState, IrcControlCmd, LiveEvent};
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
use std::{io, sync::Arc, time::Duration};

const ACCENT_RED: Color = Color::Rgb(230, 57, 70);
const VIOLET: Color = Color::Rgb(139, 92, 246);
const CYAN: Color = Color::Rgb(6, 182, 212);
const EMERALD: Color = Color::Rgb(6, 214, 160);
const AMBER: Color = Color::Rgb(245, 158, 11);
const TEXT_MAIN: Color = Color::Rgb(242, 242, 250);
const TEXT_MUTED: Color = Color::Rgb(144, 144, 176);
const TEXT_DIM: Color = Color::Rgb(90, 90, 120);
const BORDER_COLOR: Color = Color::Rgb(52, 52, 74);

#[derive(Clone, Copy, PartialEq, Eq)]
enum ActiveTab {
    Dashboard = 0,
    Channels = 1,
    BrainLab = 2,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum InputMode {
    Normal,
    AddChannel,
    SeedInput,
}

struct TuiApp {
    state: Arc<AppState>,
    tab: ActiveTab,
    input_mode: InputMode,
    input_buffer: String,
    selected_channel_idx: usize,
    selected_brain_idx: usize,
    seed_phrase: String,
    lab_samples: Vec<GenerationResult>,
    status_toast: String,
}

impl TuiApp {
    fn new(state: Arc<AppState>) -> Self {
        let mut app = Self {
            state,
            tab: ActiveTab::Dashboard,
            input_mode: InputMode::Normal,
            input_buffer: String::new(),
            selected_channel_idx: 0,
            selected_brain_idx: 0,
            seed_phrase: String::new(),
            lab_samples: Vec::new(),
            status_toast: "[1-3] Tab  [g] Speak  [s] Sample  [v] Voice  [b] TTS  [m] Mix  [o] Order  [t] Temp  [/] Seed  [q] Quit".to_string(),
        };
        app.generate_lab_samples();
        app.status_toast =
            "[1-3] Tab  [g] Speak  [s] Sample  [v] Voice  [b] TTS  [m] Mix  [o] Order  [t] Temp  [/] Seed  [q] Quit"
                .to_string();
        app
    }

    fn generate_lab_samples(&mut self) {
        let snap = self.state.status_snapshot();
        if snap.brains.is_empty() {
            return;
        }
        let idx = self.selected_brain_idx.min(snap.brains.len().saturating_sub(1));
        let brain_name = snap.brains[idx].name.clone();

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

        let mut params = GenerationParams {
            brains,
            order: self.state.config.markov.default_order,
            temperature: 1.0,
            seed,
            min_words: self.state.config.markov.min_words,
            max_words: self.state.config.markov.max_words,
            max_chars: 450,
            max_overlap_ratio: self.state.config.markov.max_overlap_ratio,
            max_overlap_total: self.state.config.markov.max_overlap_total,
            max_attempts: self.state.config.markov.max_attempts,
            recent_outputs: Vec::new(),
            blocked_words: Vec::new(),
        };

        let mut samples = Vec::new();
        let eng = self.state.engine.read().unwrap();
        for _ in 0..5 {
            if let Some(res) = eng.generate(&params) {
                params.recent_outputs.push(res.text.clone());
                samples.push(res);
            }
        }
        self.lab_samples = samples;
        self.status_toast = format!(
            "Synthesized {} candidates from '{}'{}.",
            self.lab_samples.len(),
            brain_name,
            if self.seed_phrase.is_empty() {
                "".to_string()
            } else {
                format!(" [seed: {}]", self.seed_phrase)
            }
        );
    }
}

pub async fn run_tui(state: Arc<AppState>) -> Result<()> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = TuiApp::new(state);
    let res = tui_loop(&mut terminal, &mut app).await;

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    res
}

async fn tui_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut TuiApp,
) -> Result<()> {
    loop {
        terminal.draw(|f| draw_ui(f, app))?;

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind != KeyEventKind::Press {
                    continue;
                }

                if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
                    return Ok(());
                }

                match app.input_mode {
                    InputMode::AddChannel => match key.code {
                        KeyCode::Esc => {
                            app.input_mode = InputMode::Normal;
                            app.input_buffer.clear();
                        }
                        KeyCode::Enter => {
                            let target = app.input_buffer.trim().to_string();
                            app.input_buffer.clear();
                            app.input_mode = InputMode::Normal;
                            if !target.is_empty() {
                                match app.state.add_or_join_channel(&target, "tui", true) {
                                    Ok(rec) => {
                                        app.status_toast =
                                            format!("Deployed & joined #{}", rec.channel_name);
                                    }
                                    Err(e) => {
                                        app.status_toast = format!("Error: {}", e);
                                    }
                                }
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
                            app.input_buffer.clear();
                            app.input_mode = InputMode::Normal;
                            app.generate_lab_samples();
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
                        KeyCode::Char('3') => app.tab = ActiveTab::BrainLab,
                        KeyCode::Tab => {
                            app.tab = match app.tab {
                                ActiveTab::Dashboard => ActiveTab::Channels,
                                ActiveTab::Channels => ActiveTab::BrainLab,
                                ActiveTab::BrainLab => ActiveTab::Dashboard,
                            };
                        }
                        KeyCode::Up | KeyCode::Char('k') => {
                            if app.tab == ActiveTab::BrainLab {
                                app.selected_brain_idx = app.selected_brain_idx.saturating_sub(1);
                            } else {
                                app.selected_channel_idx =
                                    app.selected_channel_idx.saturating_sub(1);
                            }
                        }
                        KeyCode::Down | KeyCode::Char('j') => {
                            let snap = app.state.status_snapshot();
                            if app.tab == ActiveTab::BrainLab {
                                if app.selected_brain_idx + 1 < snap.brains.len() {
                                    app.selected_brain_idx += 1;
                                }
                            } else if app.selected_channel_idx + 1 < snap.channels.len() {
                                app.selected_channel_idx += 1;
                            }
                        }
                        KeyCode::Char('a') => {
                            app.input_mode = InputMode::AddChannel;
                            app.input_buffer.clear();
                        }
                        KeyCode::Char('/') => {
                            app.input_mode = InputMode::SeedInput;
                            app.input_buffer = app.seed_phrase.clone();
                        }
                        KeyCode::Char(' ') | KeyCode::Enter if app.tab == ActiveTab::BrainLab => {
                            app.generate_lab_samples();
                        }
                        KeyCode::Char('g') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let seed = if app.seed_phrase.is_empty() {
                                    None
                                } else {
                                    Some(app.seed_phrase.clone())
                                };
                                if let Some(res) = app.state.speak_in_channel(&chan, "tui", seed) {
                                    app.status_toast =
                                        format!("Sent to #{}: {}", chan, res.text);
                                } else {
                                    app.status_toast =
                                        format!("Could not generate sentence for #{}", chan);
                                }
                            }
                        }
                        KeyCode::Char('s') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let seed = if app.seed_phrase.is_empty() {
                                    None
                                } else {
                                    Some(app.seed_phrase.clone())
                                };
                                if let Some(res) = app.state.generate_for_channel(&chan, seed) {
                                    app.state.log_system(
                                        "SAMPLE",
                                        format!(
                                            "[#{} preview] {} (brains: {})",
                                            chan,
                                            res.text,
                                            res.brains_used.join("+")
                                        ),
                                    );
                                    app.status_toast =
                                        format!("Sample for #{}: {}", chan, res.text);
                                }
                            }
                        }
                        KeyCode::Char('v') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut new_val = true;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.voice_enabled = !rt.record.voice_enabled;
                                        new_val = rt.record.voice_enabled;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast = format!(
                                    "Voice {} for #{}",
                                    if new_val { "on" } else { "muted" },
                                    chan
                                );
                            }
                        }
                        KeyCode::Char('b') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut new_val = true;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.tts_enabled = !rt.record.tts_enabled;
                                        new_val = rt.record.tts_enabled;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast = format!(
                                    "Bark TTS {} for #{}",
                                    if new_val { "on" } else { "off" },
                                    chan
                                );
                            }
                        }
                        KeyCode::Char('l') if app.tab == ActiveTab::Channels => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut new_val = true;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.learn_enabled = !rt.record.learn_enabled;
                                        new_val = rt.record.learn_enabled;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast = format!(
                                    "Learning {} for #{}",
                                    if new_val { "ENABLED" } else { "DISABLED" },
                                    chan
                                );
                            }
                        }
                        KeyCode::Char('c') if app.tab == ActiveTab::Channels => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut new_val = true;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.contribute_to_global =
                                            !rt.record.contribute_to_global;
                                        new_val = rt.record.contribute_to_global;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast = format!(
                                    "Global brain contribution {} for #{}",
                                    if new_val { "ENABLED" } else { "DISABLED" },
                                    chan
                                );
                            }
                        }
                        KeyCode::Char('p') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut should_join = true;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.join_enabled = !rt.record.join_enabled;
                                        should_join = rt.record.join_enabled;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                if should_join {
                                    let _ = app
                                        .state
                                        .irc_cmd_tx
                                        .send(IrcControlCmd::Join(chan.clone()));
                                    app.status_toast = format!("Joining #{}", chan);
                                } else {
                                    let _ = app
                                        .state
                                        .irc_cmd_tx
                                        .send(IrcControlCmd::Part(chan.clone()));
                                    app.status_toast = format!("Parting #{}", chan);
                                }
                            }
                        }
                        KeyCode::Char('+') | KeyCode::Char('=') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut val = 0;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.lines_between_messages =
                                            rt.record.lines_between_messages.saturating_add(5);
                                        val = rt.record.lines_between_messages;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast =
                                    format!("Set #{} lines_between_messages = {}", chan, val);
                            }
                        }
                        KeyCode::Char('-') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut val = 0;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.lines_between_messages =
                                            rt.record.lines_between_messages.saturating_sub(5);
                                        val = rt.record.lines_between_messages;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast =
                                    format!("Set #{} lines_between_messages = {}", chan, val);
                            }
                        }
                        KeyCode::Char('o') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut ord = 2;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        rt.record.markov_order = match rt.record.markov_order {
                                            1 => 2,
                                            2 => 3,
                                            _ => 1,
                                        };
                                        ord = rt.record.markov_order;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast =
                                    format!("Cycled #{} Markov N-gram order to {}", chan, ord);
                            }
                        }
                        KeyCode::Char('t') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let mut temp = 1.0;
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        let cur = (rt.record.temperature * 10.0).round() as i32;
                                        rt.record.temperature = match cur {
                                            10 => 1.3,
                                            13 => 1.6,
                                            16 => 0.7,
                                            _ => 1.0,
                                        };
                                        temp = rt.record.temperature;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast =
                                    format!("Cycled #{} Markov temperature to {:.1}", chan, temp);
                            }
                        }
                        KeyCode::Char('m') => {
                            if let Some(chan) = selected_channel_name(app) {
                                let chan_brain = format!("channel:{}", chan);
                                let mut desc = "";
                                {
                                    let mut chans = app.state.channels.write().unwrap();
                                    if let Some(rt) = chans.get_mut(&chan) {
                                        let cur_first_w = rt
                                            .record
                                            .brain_mix
                                            .first()
                                            .map(|b| (b.brain.as_str(), (b.weight * 100.0).round() as i32))
                                            .unwrap_or(("", 70));
                                        let (next_mix, label) = match cur_first_w {
                                            (_, 70) => (
                                                vec![
                                                    BrainWeight {
                                                        brain: chan_brain.clone(),
                                                        weight: 0.90,
                                                    },
                                                    BrainWeight {
                                                        brain: "global".to_string(),
                                                        weight: 0.10,
                                                    },
                                                ],
                                                "90% Channel / 10% Global",
                                            ),
                                            (_, 90) => (
                                                vec![BrainWeight {
                                                    brain: chan_brain.clone(),
                                                    weight: 1.0,
                                                }],
                                                "100% Channel Only",
                                            ),
                                            (b, 100) if b.starts_with("channel:") => (
                                                vec![BrainWeight {
                                                    brain: "global".to_string(),
                                                    weight: 1.0,
                                                }],
                                                "100% Global Only",
                                            ),
                                            ("global", 100) => (
                                                vec![
                                                    BrainWeight {
                                                        brain: chan_brain.clone(),
                                                        weight: 0.50,
                                                    },
                                                    BrainWeight {
                                                        brain: "global".to_string(),
                                                        weight: 0.50,
                                                    },
                                                ],
                                                "50% Channel / 50% Global",
                                            ),
                                            _ => (
                                                vec![
                                                    BrainWeight {
                                                        brain: chan_brain.clone(),
                                                        weight: 0.70,
                                                    },
                                                    BrainWeight {
                                                        brain: "global".to_string(),
                                                        weight: 0.30,
                                                    },
                                                ],
                                                "70% Channel / 30% Global",
                                            ),
                                        };
                                        rt.record.brain_mix = next_mix;
                                        desc = label;
                                        let _ = app.state.db.upsert_channel(&rt.record);
                                    }
                                }
                                app.status_toast =
                                    format!("Updated #{} brain mix -> {}", chan, desc);
                            }
                        }
                        _ => {}
                    },
                }
            }
        }
    }
}

fn selected_channel_name(app: &TuiApp) -> Option<String> {
    let snap = app.state.status_snapshot();
    if snap.channels.is_empty() {
        return None;
    }
    let idx = app
        .selected_channel_idx
        .min(snap.channels.len().saturating_sub(1));
    Some(snap.channels[idx].record.channel_name.clone())
}

fn studio_block(title: &str) -> Block<'_> {
    Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(BORDER_COLOR))
        .title(Span::styled(
            format!(" {} ", title),
            Style::default()
                .fg(TEXT_MAIN)
                .add_modifier(Modifier::BOLD),
        ))
}

fn draw_ui(f: &mut Frame, app: &TuiApp) {
    let snap = app.state.status_snapshot();
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
        .constraints([Constraint::Percentage(58), Constraint::Percentage(42)])
        .split(chunks[0]);

    let irc_span = if snap.irc_connected {
        Span::styled(
            " ● IRC ONLINE ",
            Style::default().fg(EMERALD).add_modifier(Modifier::BOLD),
        )
    } else {
        Span::styled(
            " ○ SANDBOX ",
            Style::default().fg(AMBER).add_modifier(Modifier::BOLD),
        )
    };

    let global_lines = snap
        .brains
        .iter()
        .find(|b| b.name == "global")
        .map(|b| b.sentence_count)
        .unwrap_or(0);

    let title_line = Line::from(vec![
        Span::styled(
            " ANSV",
            Style::default()
                .fg(TEXT_MAIN)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            "2 ",
            Style::default()
                .fg(ACCENT_RED)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(format!("@{} ", snap.bot_nick), Style::default().fg(TEXT_MUTED)),
        irc_span,
        Span::styled(
            format!(
                " │ Chans: {}/{} │ Global: {} │ Vocab: {} │ :5002",
                snap.joined_channels, snap.total_channels, global_lines, snap.vocab_size
            ),
            Style::default().fg(TEXT_MUTED),
        ),
    ]);
    let header_block = Paragraph::new(title_line).block(studio_block("Status"));
    f.render_widget(header_block, header_chunks[0]);

    let tab_titles = vec![
        Line::from(" 1 · Chat "),
        Line::from(" 2 · Channels "),
        Line::from(" 3 · Brains "),
    ];
    let tabs = Tabs::new(tab_titles)
        .block(studio_block("Tabs"))
        .select(app.tab as usize)
        .style(Style::default().fg(TEXT_MUTED))
        .highlight_style(
            Style::default()
                .fg(ACCENT_RED)
                .add_modifier(Modifier::BOLD),
        );
    f.render_widget(tabs, header_chunks[1]);

    match app.tab {
        ActiveTab::Dashboard => draw_dashboard_tab(f, chunks[1], app, &snap),
        ActiveTab::Channels => draw_channels_tab(f, chunks[1], app, &snap),
        ActiveTab::BrainLab => draw_brain_lab_tab(f, chunks[1], app, &snap),
    }

    let footer_text = match app.input_mode {
        InputMode::AddChannel => format!(
            " Add channel (Enter to confirm, Esc to cancel): #{}",
            app.input_buffer
        ),
        InputMode::SeedInput => format!(
            " Seed word (Enter to apply, Esc to cancel): {}",
            app.input_buffer
        ),
        InputMode::Normal => format!(" {}", app.status_toast),
    };
    let footer_style = if app.input_mode == InputMode::Normal {
        Style::default().fg(TEXT_MUTED)
    } else {
        Style::default()
            .fg(ACCENT_RED)
            .add_modifier(Modifier::BOLD)
    };
    let footer = Paragraph::new(footer_text)
        .style(footer_style)
        .block(studio_block("Keys"));
    f.render_widget(footer, chunks[2]);
}

fn draw_dashboard_tab(
    f: &mut Frame,
    area: Rect,
    app: &TuiApp,
    snap: &crate::state::StatusSnapshot,
) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(36), Constraint::Percentage(64)])
        .split(area);

    let sel_idx = app
        .selected_channel_idx
        .min(snap.channels.len().saturating_sub(1));
    let items: Vec<ListItem> = snap
        .channels
        .iter()
        .enumerate()
        .map(|(i, c)| {
            let r = &c.record;
            let status_dot = if c.currently_joined { "●" } else { "○" };
            let dot_color = if c.currently_joined { EMERALD } else { TEXT_DIM };
            let mix_short: Vec<String> = r
                .brain_mix
                .iter()
                .map(|b| {
                    let short_b = b.brain.strip_prefix("channel:").unwrap_or(&b.brain);
                    format!("{}:{:.0}%", short_b, b.weight * 100.0)
                })
                .collect();
            let prefix = if i == sel_idx { "▸ " } else { "  " };
            let line = Line::from(vec![
                Span::styled(
                    prefix,
                    Style::default()
                        .fg(ACCENT_RED)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(format!("{} ", status_dot), Style::default().fg(dot_color)),
                Span::styled(
                    format!("#{:<14}", r.channel_name),
                    Style::default()
                        .fg(if i == sel_idx { ACCENT_RED } else { TEXT_MAIN })
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    format!(
                        " {:>2}/{:<2}L · O{} · {}",
                        c.lines_since_last_spoke,
                        r.lines_between_messages,
                        r.markov_order,
                        if r.voice_enabled { "ON " } else { "OFF" }
                    ),
                    Style::default().fg(TEXT_MUTED),
                ),
                Span::styled(
                    format!(" [{}]", mix_short.join("+")),
                    Style::default().fg(VIOLET),
                ),
            ]);
            ListItem::new(line)
        })
        .collect();

    let chan_list = List::new(items).block(studio_block(
        "Channels (↑/↓ select · [g] speak · [s] sample · [a] add)",
    ));
    f.render_widget(chan_list, cols[0]);

    let events = app.state.recent_events.read().unwrap();
    let max_lines = (cols[1].height.saturating_sub(2)) as usize;
    let skip = events.len().saturating_sub(max_lines);
    let feed_items: Vec<ListItem> = events
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
                    Span::styled(format!("{} ", ts), Style::default().fg(TEXT_DIM)),
                    Span::styled(format!("#{} ", channel), Style::default().fg(CYAN)),
                    Span::styled(
                        format!("{}: ", author),
                        Style::default()
                            .fg(TEXT_MAIN)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(content.clone(), Style::default().fg(TEXT_MUTED)),
                    if *learned {
                        Span::styled(" [+corpus]", Style::default().fg(TEXT_DIM))
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
                    Span::styled(format!("{} ", ts), Style::default().fg(TEXT_DIM)),
                    Span::styled(format!("#{} ", channel), Style::default().fg(CYAN)),
                    Span::styled(
                        format!("ANSV2 ({}) ", trigger),
                        Style::default()
                            .fg(ACCENT_RED)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(
                        text.clone(),
                        Style::default()
                            .fg(TEXT_MAIN)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(
                        format!(" [{}]", brains_used.join("+")),
                        Style::default().fg(VIOLET),
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
                    "ERROR" => ACCENT_RED,
                    "WARN" => AMBER,
                    "SAMPLE" => VIOLET,
                    _ => CYAN,
                };
                ListItem::new(Line::from(vec![
                    Span::styled(format!("{} ", ts), Style::default().fg(TEXT_DIM)),
                    Span::styled(
                        format!("[{}] ", level),
                        Style::default().fg(lvl_color).add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(message.clone(), Style::default().fg(TEXT_MUTED)),
                ]))
            }
            LiveEvent::ChannelStatusChanged { channel, joined } => ListItem::new(Line::from(
                Span::styled(
                    format!(
                        "── #{} {} ──",
                        channel,
                        if *joined { "CONNECTED" } else { "PARTED" }
                    ),
                    Style::default().fg(TEXT_DIM),
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
                    Span::styled(format!("{} ", ts), Style::default().fg(TEXT_DIM)),
                    Span::styled(format!("#{} ", channel), Style::default().fg(CYAN)),
                    Span::styled(
                        format!("BARK [{}] ", voice_preset),
                        Style::default().fg(VIOLET).add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(text.clone(), Style::default().fg(TEXT_MAIN)),
                    Span::styled(
                        format!(" ({:.1}s)", duration_secs),
                        Style::default().fg(TEXT_DIM),
                    ),
                ]))
            }
            LiveEvent::TtsSkip { channel } => ListItem::new(Line::from(Span::styled(
                format!("── #{} TTS SKIPPED ──", channel),
                Style::default().fg(AMBER),
            ))),
        })
        .collect();

    let feed_widget = List::new(feed_items).block(studio_block("Chat Log"));
    f.render_widget(feed_widget, cols[1]);
}

fn draw_channels_tab(
    f: &mut Frame,
    area: Rect,
    app: &TuiApp,
    snap: &crate::state::StatusSnapshot,
) {
    let sel_idx = app
        .selected_channel_idx
        .min(snap.channels.len().saturating_sub(1));

    let rows: Vec<Row> = snap
        .channels
        .iter()
        .enumerate()
        .map(|(i, c)| {
            let r = &c.record;
            let mix_str: Vec<String> = r
                .brain_mix
                .iter()
                .map(|b| format!("{}={:.0}%", b.brain, b.weight * 100.0))
                .collect();
            let style = if i == sel_idx {
                Style::default()
                    .fg(ACCENT_RED)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(TEXT_MAIN)
            };
            Row::new(vec![
                format!("{}{}", if i == sel_idx { "▸ #" } else { "  #" }, r.channel_name),
                if c.currently_joined {
                    "Joined".to_string()
                } else if r.join_enabled {
                    "Pending".to_string()
                } else {
                    "Parted".to_string()
                },
                if r.voice_enabled { "Active" } else { "Muted" }.to_string(),
                if r.learn_enabled { "Yes" } else { "No" }.to_string(),
                if r.contribute_to_global { "Yes" } else { "No" }.to_string(),
                format!("{}/{}", c.lines_since_last_spoke, r.lines_between_messages),
                format!("{}m", r.time_between_messages),
                format!("O{} · T={:.1}", r.markov_order, r.temperature),
                mix_str.join(", "),
                r.source.clone(),
            ])
            .style(style)
        })
        .collect();

    let table = Table::new(
        rows,
        [
            Constraint::Length(18),
            Constraint::Length(8),
            Constraint::Length(7),
            Constraint::Length(6),
            Constraint::Length(8),
            Constraint::Length(8),
            Constraint::Length(6),
            Constraint::Length(12),
            Constraint::Fill(1),
            Constraint::Length(9),
        ],
    )
    .header(
        Row::new(vec![
            "Channel",
            "State",
            "Voice",
            "Learn",
            "->Global",
            "Lines",
            "Timer",
            "Order / Temp",
            "Weighted Brain Mix",
            "Source",
        ])
        .style(
            Style::default()
                .fg(TEXT_MUTED)
                .add_modifier(Modifier::BOLD),
        ),
    )
    .block(studio_block(
        "Channels ([v] Voice · [b] TTS · [l] Learn · [c] ->Global · [p] Join/Part · [+/-] Lines · [o] Order · [t] Temp · [m] Mix)",
    ));

    f.render_widget(table, area);
}

fn draw_brain_lab_tab(
    f: &mut Frame,
    area: Rect,
    app: &TuiApp,
    snap: &crate::state::StatusSnapshot,
) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(52), Constraint::Percentage(48)])
        .split(area);

    let sel_idx = app
        .selected_brain_idx
        .min(snap.brains.len().saturating_sub(1));

    let rows: Vec<Row> = snap
        .brains
        .iter()
        .enumerate()
        .map(|(i, b)| {
            let style = if i == sel_idx {
                Style::default()
                    .fg(ACCENT_RED)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(TEXT_MAIN)
            };
            let short_name = b
                .name
                .strip_prefix("channel:")
                .or_else(|| b.name.strip_prefix("custom:"))
                .unwrap_or(&b.name);
            Row::new(vec![
                format!("{}{}", if i == sel_idx { "▸ " } else { "  " }, short_name),
                format!("{:?}", b.kind),
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
            Constraint::Length(18),
            Constraint::Length(8),
            Constraint::Length(7),
            Constraint::Length(8),
            Constraint::Length(7),
            Constraint::Fill(1),
        ],
    )
    .header(
        Row::new(vec![
            "Brain",
            "Kind",
            "Lines",
            "Tokens",
            "Vocab",
            "O1/O2/O3",
        ])
        .style(
            Style::default()
                .fg(TEXT_MUTED)
                .add_modifier(Modifier::BOLD),
        ),
    )
    .block(studio_block(
        "Brains (↑/↓ select · [Space/Enter] roll 5x)",
    ));
    f.render_widget(table, cols[0]);

    let mut lines: Vec<Line> = Vec::new();
    let selected_brain = snap
        .brains
        .get(sel_idx)
        .map(|b| b.name.as_str())
        .unwrap_or("global");

    lines.push(Line::from(vec![
        Span::styled(" Brain: ", Style::default().fg(TEXT_MUTED)),
        Span::styled(
            selected_brain,
            Style::default().fg(CYAN).add_modifier(Modifier::BOLD),
        ),
        Span::styled("   │   Seed ([/]): ", Style::default().fg(TEXT_MUTED)),
        Span::styled(
            if app.seed_phrase.is_empty() {
                "none"
            } else {
                &app.seed_phrase
            },
            Style::default().fg(ACCENT_RED).add_modifier(Modifier::BOLD),
        ),
    ]));
    lines.push(Line::from(""));

    for (idx, sample) in app.lab_samples.iter().enumerate() {
        lines.push(Line::from(vec![
            Span::styled(
                format!(" {:02} │ ", idx + 1),
                Style::default()
                    .fg(ACCENT_RED)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                sample.text.clone(),
                Style::default()
                    .fg(TEXT_MAIN)
                    .add_modifier(Modifier::BOLD),
            ),
        ]));
        lines.push(Line::from(Span::styled(
            format!(
                "      brains: [{}] · words: {} · tries: {} · seeded: {}",
                sample.brains_used.join("+"),
                sample.word_count,
                sample.attempts_used,
                sample.seeded
            ),
            Style::default().fg(TEXT_DIM),
        )));
        lines.push(Line::from(""));
    }

    let lab_widget = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .block(studio_block(
            "Sandbox ([Space/Enter] Roll 5x · [/] Seed)",
        ));
    f.render_widget(lab_widget, cols[1]);
}

pub fn render_snapshot_text(state: Arc<AppState>, tab_num: usize) -> String {
    use ratatui::backend::TestBackend;
    let backend = TestBackend::new(116, 22);
    let mut terminal = Terminal::new(backend).unwrap();
    let mut app = TuiApp::new(state);
    app.tab = match tab_num {
        2 => ActiveTab::Channels,
        3 => ActiveTab::BrainLab,
        _ => ActiveTab::Dashboard,
    };
    terminal.draw(|f| draw_ui(f, &app)).unwrap();
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

fn format_ts(epoch: i64) -> String {
    use chrono::{Local, TimeZone};
    Local
        .timestamp_opt(epoch, 0)
        .single()
        .map(|dt| dt.format("%H:%M:%S").to_string())
        .unwrap_or_else(|| "00:00:00".to_string())
}
