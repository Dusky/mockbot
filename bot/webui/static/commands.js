// Mockbot custom-command editor. CRUD over /api/commands/<channel>.
document.addEventListener('alpine:init', () => {
  Alpine.data('commands', () => ({
    chans: [],
    channel: '',
    commands: [],
    draft: { command_name: '', response_template: '' },
    editing: null,        // command_name being edited (null = new)
    msg: '',
    err: false,

    async init() {
      try {
        const r = await fetch('/api/status', { credentials: 'same-origin' });
        if (r.ok) this.chans = (await r.json()).channels || [];
      } catch (_) {}
      if (this.chans.length) this.select(this.chans[0].channel);
    },

    async select(ch) {
      this.channel = ch; this.msg = ''; this.cancel();
      await this.load();
    },

    async load() {
      try {
        const r = await fetch('/api/commands/' + encodeURIComponent(this.channel), { credentials: 'same-origin' });
        if (r.ok) this.commands = (await r.json()).commands || [];
      } catch (_) {}
    },

    edit(c) { this.editing = c.command_name; this.draft = { command_name: c.command_name, response_template: c.response_template }; },
    newCmd() { this.editing = null; this.draft = { command_name: '', response_template: '' }; },
    cancel() { this.editing = null; this.draft = { command_name: '', response_template: '' }; },

    async save() {
      try {
        const r = await fetch('/api/commands/' + encodeURIComponent(this.channel), {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(this.draft),
        });
        if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || 'save failed'); }
        this.err = false; this.msg = 'Saved ✓'; this.cancel(); await this.load();
      } catch (e) { this.err = true; this.msg = e.message || 'save failed'; }
      setTimeout(() => this.msg = '', 4000);
    },

    async remove(c) {
      if (!confirm(`Delete "${c.command_name}"?`)) return;
      try {
        await fetch('/api/commands/' + encodeURIComponent(this.channel) + '/' + encodeURIComponent(c.command_name),
          { method: 'DELETE', credentials: 'same-origin' });
        this.err = false; this.msg = 'Deleted'; await this.load();
      } catch (e) { this.err = true; this.msg = 'delete failed'; }
      setTimeout(() => this.msg = '', 4000);
    },
  }));
});
