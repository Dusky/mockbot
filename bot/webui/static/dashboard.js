// Mockbot monitor dashboard — Alpine component fed by /api/status (initial
// state) and the /ws/events websocket (live connection/chat/TTS/log events).
document.addEventListener('alpine:init', () => {
  Alpine.data('dashboard', () => ({
    conn: 'connecting',
    attempts: 0,
    status: { nick: '—', uptime: 0, tts_enabled: false, pid: null, joined_count: 0, channels: [] },
    chat: [],
    tts: [],
    log: [],

    init() {
      this.refresh();
      this.connect();
      setInterval(() => this.refresh(), 30000);          // resync channels/uptime
      setInterval(() => { if (this.status.uptime) this.status.uptime += 1; }, 1000); // local tick
    },

    async refresh() {
      try {
        const r = await fetch('/api/status', { credentials: 'same-origin' });
        if (r.ok) this.status = await r.json();
      } catch (_) {}
    },

    connect() {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${scheme}://${location.host}/ws/events`);
      ws.onopen = () => { this.conn = 'connected'; };
      ws.onclose = () => { this.conn = 'disconnected'; setTimeout(() => this.connect(), 3000); };
      ws.onmessage = (e) => { try { this.handle(JSON.parse(e.data)); } catch (_) {} };
    },

    handle(d) {
      switch (d.event) {
        case 'chat_message':
          this.chat.unshift(d); this.chat = this.chat.slice(0, 80); break;
        case 'new_tts_entry':
          d._t = new Date().toISOString();
          this.tts.unshift(d); this.tts = this.tts.slice(0, 40); break;
        case 'error_logged':
          this.pushLog(d.level, d.message, d.timestamp); break;
        case 'connection_state_changed':
          this.conn = d.state; this.attempts = d.attempts || 0;
          this.pushLog('INFO', 'connection → ' + d.state); break;
        case 'bot_status':
          if (d.nick != null) this.status.nick = d.nick;
          if (d.uptime != null) this.status.uptime = d.uptime;
          if (d.tts_enabled != null) this.status.tts_enabled = d.tts_enabled;
          break;
      }
    },

    pushLog(lv, msg, t) {
      this.log.unshift({ lv: (lv || 'INFO').toUpperCase(), msg, t: t || new Date().toISOString() });
      this.log = this.log.slice(0, 120);
    },

    // ── view helpers ──
    connClass() { return this.conn === 'connected' ? 'ok' : (this.conn === 'disconnected' ? '' : 'warn'); },
    dotClass(c) { return c.joined ? 'ok' : 'off'; },
    logClass(lv) {
      lv = (lv || '').toUpperCase();
      return lv === 'ERROR' ? 'err' : ((lv === 'WARN' || lv === 'WARNING') ? 'warn' : 'info');
    },
    fmtUptime(s) {
      s = Math.floor(s || 0);
      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
      return d > 0 ? `${d}d ${h}h` : (h > 0 ? `${h}h ${m}m` : `${m}m`);
    },
    fmtTime(iso) { try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch (_) { return ''; } },
    fmtClock(iso) { try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (_) { return ''; } },
  }));
});
