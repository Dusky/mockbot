# ANSV Bot - Development Roadmap

## High Priority

### 1. Automatic WebSocket Reconnection
**Status:** Planned
**Priority:** High
**Description:** Implement automatic reconnection with exponential backoff when Twitch WebSocket disconnects unexpectedly.

**Tasks:**
- [ ] Add WebSocket connection monitoring
- [ ] Implement reconnection logic with exponential backoff
- [ ] Add reconnection attempt logging
- [ ] Test reconnection under various failure scenarios
- [ ] Add max retry limit with notification

**Why:** Prevents bot from entering broken state where it can receive but not send messages.

---

### 2. Health Monitoring Dashboard
**Status:** Planned (stub exists in admin panel)
**Priority:** High
**Description:** Complete the admin monitoring page with real-time health metrics.

**Features to Add:**
- [ ] Bot WebSocket connection status (connected/disconnected)
- [ ] Last message sent timestamp
- [ ] Error rate tracking (errors per hour/day)
- [ ] Service uptime display
- [ ] TTS service status
- [ ] Background task health (message_request_checker, heartbeat, etc.)
- [ ] WebSocket reconnection attempts
- [ ] Real-time updates via WebSocket to admin dashboard
- [ ] Alert notifications when critical issues occur

**Technical Notes:**
- Enhance `/admin/monitoring` page
- Add API endpoint `/api/admin/health-status`
- Use SocketIO for real-time updates
- Store health metrics in database for historical tracking

---

## Medium Priority

### 3. Custom Commands Per Channel
**Status:** Planned
**Priority:** Medium
**Description:** Allow streamers to create custom `!command` responses via web UI.

**Tasks:**
- [ ] Create database schema for custom commands
- [ ] Add UI in channel settings for command management
- [ ] Implement command parser in bot
- [ ] Add support for variables (e.g., `{user}`, `{channel}`)
- [ ] Add cooldown/rate limiting per command
- [ ] Add permission levels (everyone, subscribers, mods, etc.)

---

### 4. Message Scheduling/Timers
**Status:** Planned
**Priority:** Medium
**Description:** Send generated messages automatically on a schedule (like Nightbot timers).

**Tasks:**
- [ ] Add timer configuration UI per channel
- [ ] Create background scheduler task
- [ ] Add minimum chat activity requirement (don't spam empty channels)
- [ ] Add timer enable/disable toggle
- [ ] Show next scheduled message time in UI

**Configuration Options:**
- Interval (e.g., every 10 minutes)
- Minimum messages between timers
- Active hours (don't send at 3am)
- Custom message or auto-generate

---

### 5. Model Training Scheduler
**Status:** Planned
**Priority:** Medium
**Description:** Automatically rebuild Markov models on a schedule to keep them fresh.

**Tasks:**
- [ ] Add scheduler for automatic model rebuilds
- [ ] Add "last updated" timestamp to models
- [ ] Show model age in UI
- [ ] Add option to rebuild specific channels
- [ ] Send notification when rebuild completes
- [ ] Add rebuild logs/history

**Configuration:**
- Daily/weekly rebuild schedule
- Rebuild only if N new messages since last rebuild
- Quiet hours (don't rebuild during peak streaming)

---

### 6. Backup/Restore System
**Status:** Planned
**Priority:** Medium
**Description:** Easy backup and restore of bot data, models, and configuration.

**Tasks:**
- [ ] Create backup script (models + database + config)
- [ ] Add backup UI in admin panel
- [ ] Implement scheduled automatic backups
- [ ] Add restore functionality
- [ ] Support backup to local storage
- [ ] Optional: Support backup to S3/cloud storage
- [ ] Show backup history and sizes
- [ ] Verify backup integrity

**Backup Contents:**
- All Markov model files
- SQLite databases (messages.db, users.db)
- Configuration files (settings.conf)
- Custom voices (if any)

---

## Low Priority

### 7. Message Quality Scoring
**Status:** Not Planned
**Priority:** Low
**Description:** Filter out low-quality generated messages before sending.

**Features:**
- Minimum length requirements
- Word diversity checks
- Coherence scoring
- Auto-regenerate if score too low

---

### 8. Better Analytics Exports
**Status:** Not Planned
**Priority:** Low
**Description:** Enhanced analytics with export capabilities.

**Features:**
- Export data to CSV/JSON
- Multi-channel comparison
- Custom date ranges
- Sentiment analysis over time

---

### 9. A/B Testing for Messages
**Status:** Not Planned
**Priority:** Low
**Description:** Generate multiple message options and let streamer choose.

**Features:**
- Generate 3 variations
- Show in UI for selection
- Or auto-pick based on quality score
- Track which messages perform better

---

### 10. Real-time Chat Preview
**Status:** Not Planned
**Priority:** Low
**Description:** Test mode that shows what bot would say without actually sending.

**Use Case:** Streamers can test before going live.

---

### 11. Voice Cloning for TTS
**Status:** Not Planned
**Priority:** Low
**Description:** Train custom TTS voices from streamer audio samples.

**Note:** Computationally expensive, would require significant development.

---

### 12. Markov Chain Visualizer
**Status:** Not Planned
**Priority:** Low
**Description:** Interactive graph showing word connections and probabilities.

**Features:**
- Visual graph of word chains
- "Why did you say that?" feature
- Trace message generation path

---

## Completed Features

- ✅ TTS integration with Bark
- ✅ Web dashboard with authentication
- ✅ Channel-specific settings
- ✅ Analytics page (with real data, no fake stats)
- ✅ User management system
- ✅ OAuth integration
- ✅ Multi-user support with roles
- ✅ Custom message sending (preview then send)
- ✅ !ansv command with description
- ✅ Console logging cleanup
- ✅ TTS notifications use correct port

---

**Last Updated:** 2025-12-07
