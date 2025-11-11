/**
 * ANSV Bot - Streamer Dashboard
 * Simplified dashboard for streamers managing a single channel
 */

let channelName = null;
let botRunning = false;

// Initialize dashboard
document.addEventListener('DOMContentLoaded', function() {
    console.log('[Streamer Dashboard] Initializing...');

    // Get channel name from page
    const heroTitle = document.querySelector('.hero-title');
    if (heroTitle) {
        channelName = heroTitle.textContent.replace('#', '').trim();
        console.log('[Streamer Dashboard] Channel:', channelName);
    }

    // Initialize components
    initBotStatus();
    initSettingsToggles();
    initButtons();
    initTtsPopup();
    initTrustedUsers();
    loadRecentMessages();
    loadLastBuildTime();

    // Auto-refresh every 30 seconds
    setInterval(() => {
        refreshBotStatus();
        loadRecentMessages();
    }, 30000);

    console.log('[Streamer Dashboard] Initialization complete');
});

// Initialize bot status
function initBotStatus() {
    refreshBotStatus();
}

// Refresh bot status
function refreshBotStatus() {
    fetch('/bot_status')
        .then(response => response.json())
        .then(data => {
            botRunning = data.running;
            updateBotStatusUI(data.running);
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error fetching bot status:', error);
            updateBotStatusUI(false);
        });
}

// Update bot status UI
function updateBotStatusUI(running) {
    const statusValue = document.getElementById('botStatusValue');
    const startBtn = document.getElementById('startBotBtn');
    const stopBtn = document.getElementById('stopBotBtn');

    if (statusValue) {
        if (running) {
            statusValue.innerHTML = '<span class="badge bg-success"><i class="fas fa-circle me-1"></i>Online</span>';
        } else {
            statusValue.innerHTML = '<span class="badge bg-danger"><i class="fas fa-circle me-1"></i>Offline</span>';
        }
    }

    if (startBtn && stopBtn) {
        if (running) {
            startBtn.style.display = 'none';
            stopBtn.style.display = 'inline-block';
        } else {
            startBtn.style.display = 'inline-block';
            stopBtn.style.display = 'none';
        }
    }
}

// Initialize settings toggles
function initSettingsToggles() {
    const toggles = [
        { id: 'joinChannelToggle', setting: 'join_channel' },
        { id: 'autoReplyToggle', setting: 'voice_enabled' },
        { id: 'ttsToggle', setting: 'tts_enabled' }
    ];

    toggles.forEach(({ id, setting }) => {
        const toggle = document.getElementById(id);
        if (toggle) {
            toggle.addEventListener('change', () => {
                updateChannelSetting(setting, toggle.checked);
            });
        }
    });
}

// Update channel setting
function updateChannelSetting(setting, value, customMessage = null) {
    if (!channelName) {
        showNotification('Error: Channel not found', 'error');
        return;
    }

    const data = {
        channel_name: channelName,
        [setting]: value
    };

    fetch('/update-channel-settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            const message = customMessage || `Setting updated: ${setting}`;
            showNotification(message, 'success');
        } else {
            showNotification('Failed to update setting: ' + (data.message || data.error || 'Unknown error'), 'error');
        }
    })
    .catch(error => {
        console.error('[Streamer Dashboard] Error updating setting:', error);
        showNotification('Error updating setting', 'error');
    });
}

// Initialize buttons
function initButtons() {
    // Refresh status button
    const refreshStatusBtn = document.getElementById('refreshStatusBtn');
    if (refreshStatusBtn) {
        refreshStatusBtn.addEventListener('click', () => {
            refreshStatusBtn.querySelector('i').classList.add('fa-spin');
            refreshBotStatus();
            setTimeout(() => {
                refreshStatusBtn.querySelector('i').classList.remove('fa-spin');
            }, 1000);
        });
    }

    // Start bot button
    const startBtn = document.getElementById('startBotBtn');
    if (startBtn) {
        startBtn.addEventListener('click', startBot);
    }

    // Stop bot button
    const stopBtn = document.getElementById('stopBotBtn');
    if (stopBtn) {
        stopBtn.addEventListener('click', stopBot);
    }

    // Rebuild model button
    const rebuildBtn = document.getElementById('rebuildModelBtn');
    if (rebuildBtn) {
        rebuildBtn.addEventListener('click', rebuildModel);
    }

    // Update lines between messages
    const updateLinesBtn = document.getElementById('updateLinesBtn');
    if (updateLinesBtn) {
        updateLinesBtn.addEventListener('click', updateLinesBetween);
    }

    // Update time between messages
    const updateTimeBtn = document.getElementById('updateTimeBtn');
    if (updateTimeBtn) {
        updateTimeBtn.addEventListener('click', updateTimeBetween);
    }

    // Voice preset selector
    const voicePresetSelect = document.getElementById('voicePresetSelect');
    if (voicePresetSelect) {
        voicePresetSelect.addEventListener('change', updateVoicePreset);
    }

    // Refresh messages button
    const refreshMessagesBtn = document.getElementById('refreshMessagesBtn');
    if (refreshMessagesBtn) {
        refreshMessagesBtn.addEventListener('click', () => {
            refreshMessagesBtn.querySelector('i').classList.add('fa-spin');
            loadRecentMessages();
            setTimeout(() => {
                refreshMessagesBtn.querySelector('i').classList.remove('fa-spin');
            }, 1000);
        });
    }

    // TTS play buttons (delegate to parent)
    document.addEventListener('click', (e) => {
        if (e.target.closest('.play-tts-btn')) {
            const btn = e.target.closest('.play-tts-btn');
            const file = btn.dataset.file;
            playTtsFile(file);
        }
    });
}

// Start bot
function startBot() {
    const btn = document.getElementById('startBotBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Starting...';

    fetch('/start_bot', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('Bot started successfully', 'success');
                setTimeout(refreshBotStatus, 2000);
            } else {
                showNotification('Failed to start bot: ' + (data.message || 'Unknown error'), 'error');
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error starting bot:', error);
            showNotification('Error starting bot', 'error');
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-play me-2"></i>Start Bot';
        });
}

// Stop bot
function stopBot() {
    const btn = document.getElementById('stopBotBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Stopping...';

    fetch('/stop_bot', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('Bot stopped successfully', 'success');
                setTimeout(refreshBotStatus, 2000);
            } else {
                showNotification('Failed to stop bot: ' + (data.message || 'Unknown error'), 'error');
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error stopping bot:', error);
            showNotification('Error stopping bot', 'error');
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-stop me-2"></i>Stop Bot';
        });
}

// Rebuild Markov model
function rebuildModel() {
    if (!channelName) {
        showNotification('Error: Channel not found', 'error');
        return;
    }

    const btn = document.getElementById('rebuildModelBtn');
    const progress = document.getElementById('rebuildProgress');

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Rebuilding...';
    progress.style.display = 'block';

    fetch(`/rebuild_model/${channelName}`, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('Model rebuilt successfully', 'success');
                // Reload the last build time
                loadLastBuildTime();
            } else {
                showNotification('Failed to rebuild model: ' + (data.error || data.message || 'Unknown error'), 'error');
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error rebuilding model:', error);
            showNotification('Error rebuilding model', 'error');
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-sync-alt me-2"></i>Rebuild Markov Model';
            progress.style.display = 'none';
        });
}

// Load last build time for model
function loadLastBuildTime() {
    if (!channelName) return;

    const timeEl = document.getElementById('lastTrainedTime');
    if (!timeEl) return;

    fetch(`/api/channel/${channelName}/last_build`)
        .then(response => response.json())
        .then(data => {
            if (data.success && data.timestamp) {
                const buildDate = new Date(data.timestamp * 1000); // Convert Unix timestamp to JS Date
                const now = new Date();
                const diffSeconds = Math.floor((now - buildDate) / 1000);

                let timeStr;
                if (diffSeconds < 60) {
                    timeStr = 'Just now';
                } else if (diffSeconds < 3600) {
                    const mins = Math.floor(diffSeconds / 60);
                    timeStr = `${mins} minute${mins !== 1 ? 's' : ''} ago`;
                } else if (diffSeconds < 86400) {
                    const hours = Math.floor(diffSeconds / 3600);
                    timeStr = `${hours} hour${hours !== 1 ? 's' : ''} ago`;
                } else if (diffSeconds < 604800) {
                    const days = Math.floor(diffSeconds / 86400);
                    timeStr = `${days} day${days !== 1 ? 's' : ''} ago`;
                } else {
                    // Show full date if older than a week
                    timeStr = buildDate.toLocaleDateString() + ' ' + buildDate.toLocaleTimeString();
                }

                timeEl.textContent = timeStr;

                // Add duration info if available
                if (data.duration) {
                    timeEl.title = `Took ${data.duration.toFixed(2)}s to build`;
                }
            } else {
                timeEl.textContent = 'Never';
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error loading last build time:', error);
            timeEl.textContent = 'Unknown';
        });
}

// Update lines between messages
function updateLinesBetween() {
    if (!channelName) {
        showNotification('Error: Channel not found', 'error');
        return;
    }

    const input = document.getElementById('linesBetweenInput');
    const value = parseInt(input.value);

    if (isNaN(value) || value < 1 || value > 1000) {
        showNotification('Please enter a value between 1 and 1000', 'error');
        return;
    }

    updateChannelSetting('lines_between_messages', value);
}

function updateTimeBetween() {
    if (!channelName) {
        showNotification('Error: Channel not found', 'error');
        return;
    }

    const input = document.getElementById('timeBetweenInput');
    const value = parseInt(input.value);

    if (isNaN(value) || value < 0 || value > 60) {
        showNotification('Please enter a value between 0 and 60 minutes', 'error');
        return;
    }

    updateChannelSetting('time_between_messages', value);
}

function updateVoicePreset() {
    if (!channelName) {
        showNotification('Error: Channel not found', 'error');
        return;
    }

    const select = document.getElementById('voicePresetSelect');
    const value = select.value;

    if (!value) {
        showNotification('Please select a voice', 'error');
        return;
    }

    const voiceNumber = value.split('_').pop();
    updateChannelSetting('voice_preset', value, `Voice updated to Voice ${voiceNumber}`);
}

// Load recent bot messages
function loadRecentMessages() {
    if (!channelName) return;

    const feed = document.getElementById('botMessagesFeed');

    fetch(`/api/channel/${channelName}/recent_messages`)
        .then(response => response.json())
        .then(data => {
            if (data.success && data.messages && data.messages.length > 0) {
                renderMessages(data.messages);
            } else {
                feed.innerHTML = `
                    <div class="empty-state py-4 text-center">
                        <i class="fas fa-comment-slash fa-2x text-muted mb-2"></i>
                        <p class="text-muted mb-0">No recent bot messages</p>
                    </div>
                `;
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error loading messages:', error);
            feed.innerHTML = `
                <div class="empty-state py-4 text-center text-danger">
                    <i class="fas fa-exclamation-triangle fa-2x mb-2"></i>
                    <p class="mb-0">Error loading messages</p>
                </div>
            `;
        });
}

// Render messages
function renderMessages(messages) {
    const feed = document.getElementById('botMessagesFeed');

    const html = messages.map(msg => `
        <div class="message-item">
            <div class="message-text">${escapeHtml(msg.message)}</div>
            <div class="message-meta">
                <span><i class="fas fa-clock me-1"></i>${formatTimestamp(msg.timestamp)}</span>
            </div>
        </div>
    `).join('');

    feed.innerHTML = html;
}

// Initialize TTS popup functionality
function initTtsPopup() {
    const btn = document.getElementById('openTtsPopupBtn');
    if (btn) {
        btn.addEventListener('click', openTtsPopup);
    }
}

// Open TTS popup window for stream capture
function openTtsPopup() {
    const width = 400;
    const height = 300;
    const left = (screen.width - width) / 2;
    const top = (screen.height - height) / 2;

    const popup = window.open(
        '/tts-popup',
        'ANSV_TTS_Popup',
        `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=no,status=no,menubar=no,toolbar=no,location=no`
    );

    if (!popup) {
        showNotification('Please allow popups for this site to open TTS stream window', 'warning');
    } else {
        showNotification('TTS stream window opened', 'success');
    }
}

// Play TTS file
function playTtsFile(file) {
    const player = document.getElementById('ttsAudioPlayer');
    if (!player) return;

    player.src = `/tts/${file}`;
    player.play()
        .then(() => {
            console.log('[Streamer Dashboard] Playing TTS:', file);
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error playing TTS:', error);
            showNotification('Error playing TTS file', 'error');
        });
}

// Show notification
function showNotification(message, type = 'info') {
    // Use existing notification system if available
    if (typeof window.showToast === 'function') {
        window.showToast(message, type);
    } else {
        console.log(`[${type.toUpperCase()}] ${message}`);
        alert(message);
    }
}

// Format timestamp
function formatTimestamp(timestamp) {
    if (!timestamp) return 'Unknown';

    try {
        const date = new Date(timestamp);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000); // seconds

        if (diff < 60) return 'Just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return date.toLocaleDateString();
    } catch (e) {
        return timestamp;
    }
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ===== TRUSTED USERS MANAGEMENT =====

// Initialize trusted users functionality
function initTrustedUsers() {
    loadTrustedUsers();

    const addBtn = document.getElementById('addTrustedUserBtn');
    const input = document.getElementById('newTrustedUserInput');

    if (addBtn) {
        addBtn.addEventListener('click', addTrustedUser);
    }

    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                addTrustedUser();
            }
        });
    }
}

// Load trusted users list
function loadTrustedUsers() {
    if (!channelName) return;

    const listEl = document.getElementById('trustedUsersList');

    fetch(`/api/channel/${channelName}/trusted_users`)
        .then(response => response.json())
        .then(data => {
            if (data.success && data.trusted_users) {
                renderTrustedUsers(data.trusted_users);
            } else {
                listEl.innerHTML = `
                    <div class="text-center py-2 text-muted">
                        <p class="mb-0">No trusted members yet</p>
                    </div>
                `;
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error loading trusted users:', error);
            listEl.innerHTML = `
                <div class="text-center py-2 text-danger">
                    <i class="fas fa-exclamation-triangle mb-1"></i>
                    <p class="mb-0">Error loading trusted members</p>
                </div>
            `;
        });
}

// Render trusted users list
function renderTrustedUsers(users) {
    const listEl = document.getElementById('trustedUsersList');

    if (!users || users.length === 0) {
        listEl.innerHTML = `
            <div class="text-center py-2 text-muted">
                <p class="mb-0">No trusted members yet</p>
            </div>
        `;
        return;
    }

    const html = users.map(username => `
        <div class="trusted-user-badge">
            <i class="fas fa-user-shield text-primary"></i>
            <span class="username">${escapeHtml(username)}</span>
            <button class="remove-btn" onclick="removeTrustedUser('${escapeHtml(username)}')" title="Remove">
                <i class="fas fa-times"></i>
            </button>
        </div>
    `).join('');

    listEl.innerHTML = html;
}

// Add trusted user
function addTrustedUser() {
    if (!channelName) {
        showNotification('Error: Channel not found', 'error');
        return;
    }

    const input = document.getElementById('newTrustedUserInput');
    const username = input.value.trim().toLowerCase();

    if (!username) {
        showNotification('Please enter a username', 'warning');
        return;
    }

    // Basic validation
    if (!/^[a-z0-9_]+$/.test(username)) {
        showNotification('Username can only contain letters, numbers, and underscores', 'error');
        return;
    }

    // Get current list
    fetch(`/api/channel/${channelName}/trusted_users`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.error || 'Failed to get current trusted users');
            }

            const currentUsers = data.trusted_users || [];

            // Check if already in list
            if (currentUsers.includes(username)) {
                showNotification('User is already in the trusted list', 'warning');
                return;
            }

            // Add to list
            const updatedUsers = [...currentUsers, username];

            // Update on server
            return fetch(`/api/channel/${channelName}/trusted_users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ trusted_users: updatedUsers })
            });
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                input.value = '';
                renderTrustedUsers(data.trusted_users);
                showNotification(`Added ${username} to trusted members`, 'success');
            } else {
                throw new Error(data.error || 'Failed to add user');
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error adding trusted user:', error);
            showNotification('Error adding trusted member: ' + error.message, 'error');
        });
}

// Remove trusted user
function removeTrustedUser(username) {
    if (!channelName) {
        showNotification('Error: Channel not found', 'error');
        return;
    }

    if (!confirm(`Remove ${username} from trusted members?`)) {
        return;
    }

    // Get current list
    fetch(`/api/channel/${channelName}/trusted_users`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.error || 'Failed to get current trusted users');
            }

            // Remove from list
            const updatedUsers = (data.trusted_users || []).filter(u => u !== username);

            // Update on server
            return fetch(`/api/channel/${channelName}/trusted_users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ trusted_users: updatedUsers })
            });
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                renderTrustedUsers(data.trusted_users);
                showNotification(`Removed ${username} from trusted members`, 'success');
            } else {
                throw new Error(data.error || 'Failed to remove user');
            }
        })
        .catch(error => {
            console.error('[Streamer Dashboard] Error removing trusted user:', error);
            showNotification('Error removing trusted member: ' + error.message, 'error');
        });
}
