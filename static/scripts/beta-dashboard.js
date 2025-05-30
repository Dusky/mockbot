// ====================================================================
// ANSV Bot - Beta Dashboard JavaScript
// ====================================================================

document.addEventListener('DOMContentLoaded', function() {
    console.log('[Beta Dashboard] Initializing...');
    
    initializeDashboard();
    initializeQuickActions();
    initializeTTSPlayer();
    
    console.log('[Beta Dashboard] Initialization complete');
});

// ====================================================================
// Dashboard Initialization
// ====================================================================

function initializeDashboard() {
    loadDashboardData();
    
    // Auto-refresh dashboard data every 30 seconds
    setInterval(loadDashboardData, 30000);
    
    // Setup refresh button
    const refreshBtn = document.getElementById('refreshChannelsBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            loadDashboardData().finally(() => {
                refreshBtn.innerHTML = '<i class="fas fa-sync-alt"></i>';
            });
        });
    }
}

async function loadDashboardData() {
    try {
        // Load multiple data sources in parallel
        const [channelsData, ttsStats, messageStats] = await Promise.all([
            loadChannelsData(),
            loadTTSStats(),
            loadMessageStats()
        ]);
        
        updateChannelsGrid(channelsData);
        updateTTSActivity();
        
    } catch (error) {
        console.error('[Beta Dashboard] Error loading dashboard data:', error);
        showToast('Error loading dashboard data', 'error');
    }
}

async function loadChannelsData() {
    try {
        const response = await fetch('/api/channels');
        return await response.json();
    } catch (error) {
        console.error('[Beta Dashboard] Error loading channels:', error);
        return [];
    }
}

async function loadTTSStats() {
    try {
        const response = await fetch('/api/tts-stats');
        const stats = await response.json();
        
        // Update TTS activity count
        const ttsCount = document.getElementById('ttsActivityCount');
        if (ttsCount) {
            ttsCount.textContent = stats.today || 0;
        }
        
        return stats;
    } catch (error) {
        console.error('[Beta Dashboard] Error loading TTS stats:', error);
        return { today: 0, week: 0, total: 0 };
    }
}

async function loadMessageStats() {
    try {
        const response = await fetch('/api/bot-response-stats');
        const stats = await response.json();
        
        // Update messages today count (this is a placeholder - you might want a different endpoint)
        const messagesCount = document.getElementById('messagesTodayCount');
        if (messagesCount) {
            messagesCount.textContent = stats.total_responses || 0;
        }
        
        return stats;
    } catch (error) {
        console.error('[Beta Dashboard] Error loading message stats:', error);
        return { total_responses: 0 };
    }
}

// ====================================================================
// Channels Grid
// ====================================================================

function updateChannelsGrid(channels) {
    const grid = document.getElementById('channelsGrid');
    if (!grid) return;
    
    if (channels.length === 0) {
        grid.innerHTML = `
            <div class="col-12">
                <div class="empty-state">
                    <i class="fas fa-hashtag text-muted"></i>
                    <p class="text-muted">No channels configured</p>
                    <button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addChannelModal">
                        <i class="fas fa-plus me-1"></i>Add Your First Channel
                    </button>
                </div>
            </div>
        `;
        return;
    }
    
    grid.innerHTML = channels.map(channel => createChannelCard(channel)).join('');
    
    // Add click handlers
    grid.querySelectorAll('.channel-card').forEach(card => {
        card.addEventListener('click', (e) => {
            if (e.target.closest('.channel-actions')) return;
            const channelName = card.dataset.channel;
            window.location.href = `/beta/channel/${channelName}`;
        });
    });
}

function createChannelCard(channel) {
    const isActive = channel.join_channel && channel.currently_connected;
    const statusClass = isActive ? 'status-connected' : 'status-disconnected';
    const statusText = isActive ? 'Active' : (channel.join_channel ? 'Connecting...' : 'Disabled');
    
    return `
        <div class="channel-card" data-channel="${channel.name}">
            <div class="channel-header">
                <div class="channel-info">
                    <h3 class="channel-name">#${channel.name}</h3>
                    <div class="channel-status">
                        <span class="status-badge ${statusClass}">
                            <i class="fas fa-circle"></i>
                            ${statusText}
                        </span>
                    </div>
                </div>
                <div class="channel-actions">
                    <a href="/beta/channel/${channel.name}" class="btn btn-outline-primary btn-sm" onclick="event.stopPropagation();">
                        <i class="fas fa-external-link-alt"></i>
                    </a>
                </div>
            </div>
            
            <div class="channel-stats">
                <div class="stat-item">
                    <i class="fas fa-volume-up ${channel.tts_enabled ? 'text-success' : 'text-muted'}"></i>
                    <span>TTS: ${channel.tts_enabled ? 'On' : 'Off'}</span>
                </div>
                <div class="stat-item">
                    <i class="fas fa-robot ${channel.voice_enabled ? 'text-info' : 'text-muted'}"></i>
                    <span>Auto-Reply: ${channel.voice_enabled ? 'On' : 'Off'}</span>
                </div>
                <div class="stat-item">
                    <i class="fas fa-clock text-warning"></i>
                    <span>${channel.lines_between_messages || 100} lines</span>
                </div>
                <div class="stat-item">
                    <i class="fas fa-comments text-primary"></i>
                    <span>${(channel.messages_sent || 0).toLocaleString()} messages</span>
                </div>
            </div>
        </div>
    `;
}

// ====================================================================
// TTS Activity
// ====================================================================

async function updateTTSActivity() {
    try {
        const response = await fetch('/api/recent-tts');
        const ttsEntries = await response.json();
        
        const feed = document.getElementById('recentTtsFeed');
        if (!feed) return;
        
        if (ttsEntries.length === 0) {
            feed.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-volume-mute text-muted"></i>
                    <p class="text-muted mb-0">No recent TTS activity</p>
                </div>
            `;
            return;
        }
        
        feed.innerHTML = ttsEntries.slice(0, 5).map(entry => createTTSActivityItem(entry)).join('');
        
        // Add play button handlers
        feed.querySelectorAll('.play-tts-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const filePath = btn.dataset.file;
                playTTS(filePath);
            });
        });
        
    } catch (error) {
        console.error('[Beta Dashboard] Error updating TTS activity:', error);
    }
}

function createTTSActivityItem(entry) {
    const truncatedMessage = entry.message.length > 50 
        ? entry.message.substring(0, 50) + '...' 
        : entry.message;
    
    const timeAgo = betaUtils.formatTimestamp(entry.timestamp);
    
    return `
        <div class="activity-item">
            <div class="activity-icon">
                <i class="fas fa-volume-up"></i>
            </div>
            <div class="activity-content">
                <p class="activity-text">${betaUtils.escapeHtml(truncatedMessage)}</p>
                <small class="activity-time">${timeAgo} • ${entry.channel || 'Unknown'}</small>
            </div>
            ${entry.file_path ? `
                <div class="activity-actions">
                    <button class="btn btn-sm btn-outline-primary play-tts-btn" data-file="${entry.file_path}">
                        <i class="fas fa-play"></i>
                    </button>
                </div>
            ` : ''}
        </div>
    `;
}

// ====================================================================
// Quick Actions
// ====================================================================

function initializeQuickActions() {
    // Main quick actions button - scroll to quick actions panel
    const quickActionsBtn = document.getElementById('quickActionsBtn');
    if (quickActionsBtn) {
        quickActionsBtn.addEventListener('click', () => {
            const quickActionsPanel = document.querySelector('.quick-actions').closest('.dashboard-panel');
            if (quickActionsPanel) {
                quickActionsPanel.scrollIntoView({ 
                    behavior: 'smooth', 
                    block: 'start' 
                });
                // Add a subtle highlight effect
                quickActionsPanel.style.boxShadow = '0 0 20px rgba(0, 123, 255, 0.3)';
                setTimeout(() => {
                    quickActionsPanel.style.boxShadow = '';
                }, 2000);
            }
        });
    }
    
    // Rebuild all models
    const rebuildAllBtn = document.getElementById('rebuildAllBtn');
    if (rebuildAllBtn) {
        rebuildAllBtn.addEventListener('click', rebuildAllModels);
    }
    
    // Bot control
    const botControlBtn = document.getElementById('botControlBtn');
    if (botControlBtn) {
        botControlBtn.addEventListener('click', toggleBot);
        updateBotControlButton();
    }
}

async function rebuildAllModels() {
    const btn = document.getElementById('rebuildAllBtn');
    if (!btn) return;
    
    const originalContent = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>Rebuilding...</span>';
    
    try {
        const response = await fetch('/rebuild-all-caches', {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast('All models rebuilt successfully', 'success');
        } else {
            showToast('Failed to rebuild models: ' + (result.message || 'Unknown error'), 'error');
        }
        
    } catch (error) {
        console.error('[Beta Dashboard] Error rebuilding models:', error);
        showToast('Error rebuilding models', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalContent;
    }
}

async function toggleBot() {
    const btn = document.getElementById('botControlBtn');
    if (!btn) return;
    
    const isRunning = btn.dataset.running === 'true';
    const action = isRunning ? 'stop' : 'start';
    
    const originalContent = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i><span>${action === 'start' ? 'Starting' : 'Stopping'}...</span>`;
    
    try {
        const response = await fetch(`/${action}_bot`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast(`Bot ${action}ed successfully`, 'success');
            // Update status after a delay
            setTimeout(() => {
                betaUtils.updateBotStatus();
                updateBotControlButton();
            }, 2000);
        } else {
            showToast(`Failed to ${action} bot: ` + (result.message || 'Unknown error'), 'error');
        }
        
    } catch (error) {
        console.error(`[Beta Dashboard] Error ${action}ing bot:`, error);
        showToast(`Error ${action}ing bot`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalContent;
    }
}

async function updateBotControlButton() {
    try {
        const response = await fetch('/api/bot-status');
        const status = await response.json();
        
        const btn = document.getElementById('botControlBtn');
        const text = document.getElementById('botControlText');
        
        if (btn && text) {
            if (status.running) {
                btn.dataset.running = 'true';
                btn.className = 'action-btn text-danger';
                text.textContent = 'Stop Bot';
                btn.querySelector('i').className = 'fas fa-stop';
            } else {
                btn.dataset.running = 'false';
                btn.className = 'action-btn text-success';
                text.textContent = 'Start Bot';
                btn.querySelector('i').className = 'fas fa-play';
            }
        }
        
    } catch (error) {
        console.error('[Beta Dashboard] Error updating bot control button:', error);
    }
}

// ====================================================================
// TTS Player
// ====================================================================

function initializeTTSPlayer() {
    // Initialize hidden audio player
    const player = document.getElementById('ttsAudioPlayer');
    if (player) {
        player.addEventListener('ended', () => {
            // Reset all play buttons when audio ends
            document.querySelectorAll('.play-tts-btn').forEach(btn => {
                btn.innerHTML = '<i class="fas fa-play"></i>';
                btn.classList.remove('playing');
            });
        });
    }
}

function playTTS(filePath) {
    if (!filePath) return;
    
    const player = document.getElementById('ttsAudioPlayer');
    if (!player) return;
    
    // Reset all play buttons
    document.querySelectorAll('.play-tts-btn').forEach(btn => {
        btn.innerHTML = '<i class="fas fa-play"></i>';
        btn.classList.remove('playing');
    });
    
    // Update the clicked button
    const clickedBtn = document.querySelector(`[data-file="${filePath}"]`);
    if (clickedBtn) {
        clickedBtn.innerHTML = '<i class="fas fa-pause"></i>';
        clickedBtn.classList.add('playing');
    }
    
    // Play the audio
    const audioUrl = `/static/${filePath}`;
    player.src = audioUrl;
    player.play().catch(error => {
        console.error('[Beta Dashboard] Error playing TTS:', error);
        showToast('Error playing TTS audio', 'error');
        
        // Reset button on error
        if (clickedBtn) {
            clickedBtn.innerHTML = '<i class="fas fa-play"></i>';
            clickedBtn.classList.remove('playing');
        }
    });
}