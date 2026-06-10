// Mockbot settings control surface. Schema + values come from /api/settings/<ch>
// (derived from the settings registry); saves POST only the changed fields.
document.addEventListener('alpine:init', () => {
  Alpine.data('settings', () => ({
    chans: [],
    channel: '',
    schema: [],
    values: {},
    dirty: {},
    msg: '',
    err: false,

    async init() {
      try {
        const r = await fetch('/api/status', { credentials: 'same-origin' });
        if (r.ok) this.chans = (await r.json()).channels || [];
      } catch (_) {}
      if (this.chans.length) this.select(this.chans[0].channel);
    },

    cur() { return this.chans.find(c => c.channel === this.channel) || {}; },

    async select(ch) {
      this.channel = ch; this.msg = ''; this.dirty = {};
      try {
        const r = await fetch('/api/settings/' + encodeURIComponent(ch), { credentials: 'same-origin' });
        if (r.ok) { const d = await r.json(); this.schema = d.schema; this.values = d.values; }
      } catch (_) {}
    },

    set(k, v) { this.values[k] = v; this.dirty[k] = true; },
    isBool(k) { const v = this.values[k]; return v === 1 || v === '1' || v === true; },
    toggle(k) { this.set(k, this.isBool(k) ? 0 : 1); },
    numType(f) { return (f.kind === 'int' || f.kind === 'float') ? 'number' : 'text'; },
    dirtyCount() { return Object.keys(this.dirty).length; },

    async save() {
      const payload = {};
      for (const k in this.dirty) payload[k] = String(this.values[k]);
      try {
        const r = await fetch('/api/settings/' + encodeURIComponent(this.channel), {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        const d = await r.json();
        this.dirty = {};
        const errs = Object.keys(d.errors || {});
        this.err = errs.length > 0;
        this.msg = errs.length
          ? ('Saved, but: ' + errs.map(k => `${k} — ${d.errors[k]}`).join('; '))
          : 'Saved ✓';
      } catch (e) { this.err = true; this.msg = 'Save failed'; }
      setTimeout(() => this.msg = '', 5000);
    },

    async conn(action) {
      try {
        const r = await fetch('/api/channels/' + encodeURIComponent(this.channel) + '/' + action,
          { method: 'POST', credentials: 'same-origin' });
        if (!r.ok) throw new Error();
        const c = this.cur(); if (c) c.joined = (action === 'join');
        this.err = false; this.msg = `#${this.channel} ${action}ed`;
      } catch (e) { this.err = true; this.msg = `${action} failed`; }
      setTimeout(() => this.msg = '', 4000);
    },
  }));
});
