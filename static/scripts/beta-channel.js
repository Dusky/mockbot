// ====================================================================
// ANSV Bot - Beta Channel Page JavaScript
// ====================================================================

document.addEventListener('DOMContentLoaded', function() {
    console.log('[Beta Channel] Initializing...');
    
    if (!window.channelData || !window.channelData.name) {
        console.error('[Beta Channel] Channel data not found');
        showToast('Channel data not available', 'error');
        return;
    }
    
    initializeChannelPage();
    initializeControls();
    initializeChatStream();
    initializeTTSHistory();
    initializeStats();
    initializeQuickSettings();
    
    console.log('[Beta Channel] Initialization complete for channel:', window.channelData.name);
});

// Global state
let currentMessage = null;
let chatMessages = [];
let ttsHistory = [];
let autoScroll = true;
let currentlyPlayingId = null;

// ====================================================================
// Channel Page Initialization
// ====================================================================

function initializeChannelPage() {
    const channelName = window.channelData.name;
    
    // Start auto-refresh intervals
    loadChannelData();
    
    // Auto-refresh data every 30 seconds
    setInterval(loadChannelData, 30000);
    
    // Auto-refresh chat every 5 seconds
    setInterval(loadChatMessages, 5000);
    
    // Auto-refresh TTS every 10 seconds
    setInterval(loadTTSHistory, 10000);
}

async function loadChannelData() {
    try {
        // Load stats and update connection status
        await Promise.all([
            loadChannelStats(),
            updateConnectionStatus()
        ]);
    } catch (error) {
        console.error('[Beta Channel] Error loading channel data:', error);
    }
}

// ====================================================================
// Controls
// ====================================================================

function initializeControls() {
    // TTS Toggle
    const ttsToggle = document.getElementById('ttsToggle');
    if (ttsToggle) {
        ttsToggle.addEventListener('change', handleTTSToggle);
    }
    
    // Auto-Reply Toggle
    const autoReplyToggle = document.getElementById('autoReplyToggle');
    if (autoReplyToggle) {
        autoReplyToggle.addEventListener('change', handleAutoReplyToggle);
    }
    
    // Join Channel Toggle
    const joinChannelToggle = document.getElementById('joinChannelToggle');
    if (joinChannelToggle) {
        joinChannelToggle.addEventListener('change', handleJoinChannelToggle);
    }
    
    // TTS Delay Toggle
    const ttsDelayToggle = document.getElementById('ttsDelayToggle');
    if (ttsDelayToggle) {
        ttsDelayToggle.addEventListener('change', handleTTSDelayToggle);
    }
    
    // Initialize trusted users management
    initializeTrustedUsers();
    
    // Message Generation
    const generateBtn = document.getElementById('generateMessageBtn');
    if (generateBtn) {
        generateBtn.addEventListener('click', () => generateMessage(false));
    }
    
    const generateSendBtn = document.getElementById('generateSendBtn');
    if (generateSendBtn) {
        generateSendBtn.addEventListener('click', () => generateMessage(true));
    }
    
    // Send Generated Message
    const sendGeneratedBtn = document.getElementById('sendGeneratedBtn');
    if (sendGeneratedBtn) {
        sendGeneratedBtn.addEventListener('click', sendGeneratedMessage);
    }
    
    // Model Rebuild
    const rebuildBtn = document.getElementById('rebuildModelBtn');
    if (rebuildBtn) {
        rebuildBtn.addEventListener('click', rebuildModel);
    }
}

async function handleTTSToggle() {
    const toggle = document.getElementById('ttsToggle');
    const enabled = toggle.checked;
    const channelName = window.channelData.name;
    
    try {
        const response = await betaUtils.apiRequest('/update-channel-settings', {
            method: 'POST',
            body: JSON.stringify({ 
                channel_name: channelName,
                tts_enabled: enabled
            })
        });
        
        showToast(`TTS ${enabled ? 'enabled' : 'disabled'} for #${channelName}`, 'success');
        
        // Show/hide TTS panel
        const ttsPanel = document.getElementById('ttsPanel');
        if (ttsPanel) {
            ttsPanel.style.display = enabled ? 'block' : 'none';
        }
        
    } catch (error) {
        console.error('[Beta Channel] Error toggling TTS:', error);
        toggle.checked = !enabled; // Revert
        showToast('Failed to toggle TTS', 'error');
    }
}

async function handleAutoReplyToggle() {
    const toggle = document.getElementById('autoReplyToggle');
    const enabled = toggle.checked;
    const channelName = window.channelData.name;
    
    try {
        await betaUtils.apiRequest('/update-channel-settings', {
            method: 'POST',
            body: JSON.stringify({ 
                channel_name: channelName,
                voice_enabled: enabled
            })
        });
        
        showToast(`Voice/Bot speaking ${enabled ? 'enabled' : 'disabled'} for #${channelName}`, 'success');
        
        // Update connection status
        setTimeout(updateConnectionStatus, 1000);
        
    } catch (error) {
        console.error('[Beta Channel] Error toggling voice/bot speaking:', error);
        toggle.checked = !enabled; // Revert
        showToast('Failed to toggle voice/bot speaking', 'error');
    }
}

async function handleJoinChannelToggle() {
    const toggle = document.getElementById('joinChannelToggle');
    const enabled = toggle.checked;
    const channelName = window.channelData.name;
    
    try {
        await betaUtils.apiRequest('/update-channel-settings', {
            method: 'POST',
            body: JSON.stringify({ 
                channel_name: channelName,
                join_channel: enabled
            })
        });
        
        showToast(`Bot will ${enabled ? 'join' : 'leave'} #${channelName} on next restart`, 'success');
        
        // Update connection status
        setTimeout(updateConnectionStatus, 1000);
        
    } catch (error) {
        console.error('[Beta Channel] Error toggling join channel:', error);
        toggle.checked = !enabled; // Revert
        showToast('Failed to toggle join channel', 'error');
    }
}

async function handleTTSDelayToggle() {
    const toggle = document.getElementById('ttsDelayToggle');
    const enabled = toggle.checked;
    const channelName = window.channelData.name;
    
    try {
        const response = await betaUtils.apiRequest(`/update-channel-settings`, {
            method: 'POST',
            body: JSON.stringify({ 
                channel_name: channelName,
                tts_delay_enabled: enabled
            })
        });
        
        showToast(`TTS delay ${enabled ? 'enabled' : 'disabled'} for #${channelName}`, 'success');
        
        // Update channel data
        window.channelData.tts_delay_enabled = enabled;
        
    } catch (error) {
        console.error('[Beta Channel] Error toggling TTS delay:', error);
        toggle.checked = !enabled; // Revert
        showToast('Failed to toggle TTS delay', 'error');
    }
}

async function generateMessage(sendToChat = false) {
    const channelName = window.channelData.name;
    const btn = sendToChat ? document.getElementById('generateSendBtn') : document.getElementById('generateMessageBtn');
    
    if (!btn) return;
    
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Generating...';
    
    try {
        const response = await betaUtils.apiRequest(`/api/channel/${channelName}/generate`, {
            method: 'POST',
            body: JSON.stringify({ send_to_chat: sendToChat })
        });
        
        if (sendToChat && response.sent_to_chat) {
            showToast(`Message sent to #${channelName}!`, 'success');
            setTimeout(loadChatMessages, 1000);
        } else if (!sendToChat) {
            currentMessage = response.message;
            showGeneratedMessage(response.message);
        }
        
    } catch (error) {
        console.error('[Beta Channel] Error generating message:', error);
        showToast('Failed to generate message', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

function showGeneratedMessage(message) {
    const modal = document.getElementById('generatedMessageModal');
    const messageText = document.getElementById('generatedMessageText');
    
    if (modal && messageText) {
        messageText.textContent = message;
        const bsModal = new bootstrap.Modal(modal);
        bsModal.show();
    }
}

async function sendGeneratedMessage() {
    if (!currentMessage) return;
    
    const channelName = window.channelData.name;
    const btn = document.getElementById('sendGeneratedBtn');
    
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Sending...';
    
    try {
        await betaUtils.apiRequest(`/send_markov_message/${channelName}`, {
            method: 'POST',
            body: JSON.stringify({
                verify_running: true,
                custom_message: currentMessage
            })
        });
        
        showToast(`Message sent to #${channelName}!`, 'success');
        
        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('generatedMessageModal'));
        if (modal) modal.hide();
        
        // Refresh chat
        setTimeout(loadChatMessages, 1000);
        
    } catch (error) {
        console.error('[Beta Channel] Error sending message:', error);
        showToast('Failed to send message', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

async function rebuildModel() {
    const channelName = window.channelData.name;
    const btn = document.getElementById('rebuildModelBtn');
    
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Rebuilding...';
    
    try {
        const response = await fetch(`/rebuild-cache/${channelName}`, {
            method: 'POST'
        });
        
        if (response.ok) {
            showToast(`Model rebuilt for #${channelName}`, 'success');
            setTimeout(loadChannelStats, 2000);
        } else {
            throw new Error('Rebuild failed');
        }
        
    } catch (error) {
        console.error('[Beta Channel] Error rebuilding model:', error);
        showToast('Failed to rebuild model', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// ====================================================================
// Chat Stream
// ====================================================================

function initializeChatStream() {
    // Auto-scroll toggle
    const autoScrollToggle = document.getElementById('autoScrollToggle');
    if (autoScrollToggle) {
        autoScrollToggle.addEventListener('change', (e) => {
            autoScroll = e.target.checked;
        });
    }
    
    // Clear chat button
    const clearChatBtn = document.getElementById('clearChatBtn');
    if (clearChatBtn) {
        clearChatBtn.addEventListener('click', clearChatMessages);
    }
    
    loadChatMessages();
}

async function loadChatMessages() {
    const channelName = window.channelData.name;
    
    try {
        const response = await betaUtils.apiRequest(`/api/chat-logs?channel=${encodeURIComponent(channelName)}&per_page=50&page=1`);
        
        if (response.logs) {
            const newMessages = response.logs.filter(msg => !msg.is_bot_response);
            updateChatDisplay(newMessages);
        }
        
    } catch (error) {
        console.error('[Beta Channel] Error loading chat messages:', error);
        showChatError();
    }
}

function updateChatDisplay(messages) {
    const loading = document.getElementById('chatLoading');
    const chatContainer = document.getElementById('chatMessages');
    const emptyState = document.getElementById('chatEmpty');
    const messageCount = document.getElementById('messageCount');
    
    // Hide loading
    if (loading) loading.style.display = 'none';
    
    if (messages.length === 0) {
        if (chatContainer) chatContainer.style.display = 'none';
        if (emptyState) emptyState.style.display = 'block';
        if (messageCount) messageCount.textContent = '0';
        return;
    }
    
    // Show messages
    if (emptyState) emptyState.style.display = 'none';
    if (chatContainer) {
        chatContainer.style.display = 'block';
        chatContainer.innerHTML = messages.slice(0, 30).reverse().map(createChatMessage).join('');
        
        if (autoScroll) {
            const chatStream = document.getElementById('chatStream');
            if (chatStream) {
                chatStream.scrollTop = chatStream.scrollHeight;
            }
        }
    }
    
    if (messageCount) {
        messageCount.textContent = messages.length;
    }
    
    chatMessages = messages;
}

function createChatMessage(message) {
    const timestamp = new Date(message.timestamp).toLocaleTimeString();
    const username = message.username || 'Anonymous';
    const avatarLetter = username.charAt(0).toUpperCase();
    
    return `
        <div class="chat-message">
            <div class="chat-avatar">${avatarLetter}</div>
            <div class="chat-content">
                <div class="chat-header">
                    <span class="chat-username">${betaUtils.escapeHtml(username)}</span>
                    <span class="chat-timestamp">${timestamp}</span>
                </div>
                <p class="chat-text">${betaUtils.escapeHtml(message.message)}</p>
            </div>
        </div>
    `;
}

function clearChatMessages() {
    const chatContainer = document.getElementById('chatMessages');
    const emptyState = document.getElementById('chatEmpty');
    const messageCount = document.getElementById('messageCount');
    
    if (chatContainer) {
        chatContainer.innerHTML = '';
        chatContainer.style.display = 'none';
    }
    
    if (emptyState) {
        emptyState.style.display = 'block';
    }
    
    if (messageCount) {
        messageCount.textContent = '0';
    }
    
    chatMessages = [];
    showToast('Chat messages cleared', 'info');
}

function showChatError() {
    const loading = document.getElementById('chatLoading');
    const chatContainer = document.getElementById('chatMessages');
    
    if (loading) loading.style.display = 'none';
    if (chatContainer) {
        chatContainer.style.display = 'block';
        chatContainer.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-exclamation-triangle text-danger"></i>
                <p class="text-danger">Error loading chat messages</p>
                <button class="btn btn-outline-primary btn-sm" onclick="loadChatMessages()">
                    <i class="fas fa-sync-alt me-1"></i>Retry
                </button>
            </div>
        `;
    }
}

// ====================================================================
// TTS History
// ====================================================================

function initializeTTSHistory() {
    // Voice filter
    const voiceFilter = document.getElementById('voiceFilter');
    if (voiceFilter) {
        voiceFilter.addEventListener('change', filterTTSHistory);
    }
    
    // Clear TTS button
    const clearTtsBtn = document.getElementById('clearTtsBtn');
    if (clearTtsBtn) {
        clearTtsBtn.addEventListener('click', clearTTSHistory);
    }
    
    // TTS form
    const ttsTextarea = document.getElementById('ttsTextarea');
    const charCount = document.getElementById('charCount');
    const generateTtsBtn = document.getElementById('generateTtsBtn');
    
    if (ttsTextarea && charCount) {
        ttsTextarea.addEventListener('input', () => {
            const count = ttsTextarea.value.length;
            charCount.textContent = `${count}/500`;
            
            if (count > 450) {
                charCount.className = 'text-warning';
            } else if (count > 500) {
                charCount.className = 'text-danger';
            } else {
                charCount.className = 'text-muted';
            }
        });
    }
    
    if (generateTtsBtn) {
        generateTtsBtn.addEventListener('click', generateTTS);
    }
    
    loadTTSHistory();
}

async function loadTTSHistory() {
    const channelName = window.channelData.name;
    
    try {
        const response = await betaUtils.apiRequest(`/api/tts-logs?channel_filter=${encodeURIComponent(channelName)}&per_page=20&page=1&sort_by=timestamp&sort_order=desc`);
        
        if (response.logs) {
            updateTTSDisplay(response.logs);
            updateVoiceFilter(response.logs);
        }
        
    } catch (error) {
        console.error('[Beta Channel] Error loading TTS history:', error);
        showTTSError();
    }
}

function updateTTSDisplay(entries) {
    const loading = document.getElementById('ttsLoading');
    const ttsList = document.getElementById('ttsList');
    const emptyState = document.getElementById('ttsEmpty');
    
    // Hide loading
    if (loading) loading.style.display = 'none';
    
    if (entries.length === 0) {
        if (ttsList) ttsList.style.display = 'none';
        if (emptyState) emptyState.style.display = 'block';
        return;
    }
    
    // Show TTS list
    if (emptyState) emptyState.style.display = 'none';
    if (ttsList) {
        ttsList.style.display = 'block';
        ttsList.innerHTML = entries.map(createTTSItem).join('');
        
        // Add play button handlers
        ttsList.querySelectorAll('.tts-play-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const ttsId = parseInt(btn.dataset.ttsId);
                const filePath = btn.dataset.filePath;
                toggleTTSPlayback(ttsId, filePath);
            });
        });
    }
    
    ttsHistory = entries;
}

function createTTSItem(entry) {
    const timestamp = betaUtils.formatTimestamp(entry.timestamp);
    const voicePreset = entry.voice_preset || 'Unknown';
    const isPlaying = currentlyPlayingId === entry.id;
    
    return `
        <div class="tts-item ${isPlaying ? 'playing' : ''}">
            <div class="tts-content">
                <p class="tts-text">${betaUtils.escapeHtml(entry.message)}</p>
                <div class="tts-meta">
                    <span><i class="fas fa-microphone me-1"></i>${betaUtils.escapeHtml(voicePreset)}</span>
                    <span><i class="fas fa-clock me-1"></i>${timestamp}</span>
                </div>
            </div>
            <div class="tts-controls">
                ${entry.file_path ? `
                    <button class="tts-play-btn ${isPlaying ? 'playing' : ''}" 
                            data-tts-id="${entry.id}" 
                            data-file-path="${entry.file_path}"
                            title="${isPlaying ? 'Pause' : 'Play'} TTS">
                        <i class="fas ${isPlaying ? 'fa-pause' : 'fa-play'}"></i>
                    </button>
                ` : ''}
            </div>
        </div>
    `;
}

function updateVoiceFilter(entries) {
    const voiceFilter = document.getElementById('voiceFilter');
    if (!voiceFilter) return;
    
    const voices = [...new Set(entries.map(entry => entry.voice_preset).filter(Boolean))];
    const currentValue = voiceFilter.value;
    
    voiceFilter.innerHTML = '<option value="">All Voices</option>';
    voices.forEach(voice => {
        const option = document.createElement('option');
        option.value = voice;
        option.textContent = voice;
        if (voice === currentValue) option.selected = true;
        voiceFilter.appendChild(option);
    });
}

function filterTTSHistory() {
    const filter = document.getElementById('voiceFilter')?.value;
    const filteredEntries = filter ? 
        ttsHistory.filter(entry => entry.voice_preset === filter) : 
        ttsHistory;
    
    const ttsList = document.getElementById('ttsList');
    if (ttsList) {
        ttsList.innerHTML = filteredEntries.map(createTTSItem).join('');
        
        // Re-add event listeners
        ttsList.querySelectorAll('.tts-play-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const ttsId = parseInt(btn.dataset.ttsId);
                const filePath = btn.dataset.filePath;
                toggleTTSPlayback(ttsId, filePath);
            });
        });
    }
}

function toggleTTSPlayback(ttsId, filePath) {
    const player = document.getElementById('ttsAudioPlayer');
    if (!player || !filePath) return;
    
    // If this TTS is currently playing, pause it
    if (currentlyPlayingId === ttsId && !player.paused) {
        player.pause();
        return;
    }
    
    // Otherwise, play this TTS
    const audioUrl = `/static/${filePath}`;
    player.src = audioUrl;
    currentlyPlayingId = ttsId;
    
    player.play().then(() => {
        updatePlayingStates();
    }).catch(error => {
        console.error('[Beta Channel] TTS playback failed:', error);
        showToast('TTS playback failed', 'warning');
        currentlyPlayingId = null;
        updatePlayingStates();
    });
    
    // Handle audio end
    player.onended = () => {
        currentlyPlayingId = null;
        updatePlayingStates();
    };
}

function updatePlayingStates() {
    document.querySelectorAll('.tts-item').forEach(item => {
        const btn = item.querySelector('.tts-play-btn');
        if (!btn) return;
        
        const ttsId = parseInt(btn.dataset.ttsId);
        const icon = btn.querySelector('i');
        
        if (ttsId === currentlyPlayingId) {
            item.classList.add('playing');
            btn.classList.add('playing');
            if (icon) icon.className = 'fas fa-pause';
            btn.title = 'Pause TTS';
        } else {
            item.classList.remove('playing');
            btn.classList.remove('playing');
            if (icon) icon.className = 'fas fa-play';
            btn.title = 'Play TTS';
        }
    });
}

async function generateTTS() {
    const channelName = window.channelData.name;
    const textarea = document.getElementById('ttsTextarea');
    const btn = document.getElementById('generateTtsBtn');
    
    if (!textarea || !btn) return;
    
    const text = textarea.value.trim();
    if (!text) {
        showToast('Please enter text for TTS generation', 'warning');
        return;
    }
    
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Generating...';
    
    try {
        console.log('[Beta Channel] Sending TTS request:', { channel: channelName, text: text.substring(0, 50) + '...' });
        
        const response = await betaUtils.apiRequest(`/api/channel/${channelName}/tts`, {
            method: 'POST',
            body: JSON.stringify({ text })
        });
        
        console.log('[Beta Channel] TTS response:', response);
        showToast('TTS generated successfully!', 'success');
        textarea.value = '';
        document.getElementById('charCount').textContent = '0/500';
        
        // Refresh TTS history
        setTimeout(loadTTSHistory, 1000);
        
    } catch (error) {
        console.error('[Beta Channel] Error generating TTS:', error);
        
        // More detailed error reporting
        if (error.message) {
            showToast(`Failed to generate TTS: ${error.message}`, 'error');
        } else {
            showToast('Failed to generate TTS - check console for details', 'error');
        }
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

function clearTTSHistory() {
    if (!confirm('Clear TTS history display? This will not delete the actual files.')) return;
    
    const ttsList = document.getElementById('ttsList');
    const emptyState = document.getElementById('ttsEmpty');
    
    if (ttsList) {
        ttsList.innerHTML = '';
        ttsList.style.display = 'none';
    }
    
    if (emptyState) {
        emptyState.style.display = 'block';
    }
    
    // Stop any playing audio
    const player = document.getElementById('ttsAudioPlayer');
    if (player) {
        player.pause();
        player.src = '';
    }
    
    currentlyPlayingId = null;
    ttsHistory = [];
    showToast('TTS history display cleared', 'info');
}

function showTTSError() {
    const loading = document.getElementById('ttsLoading');
    const ttsList = document.getElementById('ttsList');
    
    if (loading) loading.style.display = 'none';
    if (ttsList) {
        ttsList.style.display = 'block';
        ttsList.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-exclamation-triangle text-danger"></i>
                <p class="text-danger">Error loading TTS history</p>
                <button class="btn btn-outline-primary btn-sm" onclick="loadTTSHistory()">
                    <i class="fas fa-sync-alt me-1"></i>Retry
                </button>
            </div>
        `;
    }
}

// ====================================================================
// Stats
// ====================================================================

function initializeStats() {
    loadChannelStats();
}

async function loadChannelStats() {
    const channelName = window.channelData.name;
    
    try {
        const response = await betaUtils.apiRequest(`/api/channel/${channelName}/stats`);
        updateStatsDisplay(response);
        updateModelInfo(response.model_info);
    } catch (error) {
        console.error('[Beta Channel] Error loading channel stats:', error);
        showStatsError();
    }
}

function updateStatsDisplay(data) {
    const elements = {
        totalMessages: document.getElementById('totalMessages'),
        todayMessages: document.getElementById('todayMessages'),
        ttsCount: document.getElementById('ttsCount'),
        botResponses: document.getElementById('botResponses')
    };
    
    if (elements.totalMessages) {
        elements.totalMessages.textContent = (data.total_messages || 0).toLocaleString();
    }
    
    if (elements.todayMessages) {
        elements.todayMessages.textContent = (data.today_messages || 0).toLocaleString();
    }
    
    if (elements.ttsCount) {
        elements.ttsCount.textContent = (data.tts_count || 0).toLocaleString();
    }
    
    if (elements.botResponses) {
        elements.botResponses.textContent = (data.bot_responses || 0).toLocaleString();
    }
}

function updateModelInfo(modelInfo) {
    const modelInfoEl = document.getElementById('modelInfo');
    if (!modelInfoEl) return;
    
    if (modelInfo) {
        modelInfoEl.innerHTML = `
            <div class="model-details">
                <div class="detail-item">
                    <span class="detail-label">Model Name:</span>
                    <span class="detail-value"><code>${modelInfo.name || 'Unknown'}</code></span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Lines Processed:</span>
                    <span class="detail-value">${(modelInfo.line_count || 0).toLocaleString()}</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">File Size:</span>
                    <span class="detail-value">${modelInfo.cache_size_str || 'Unknown'}</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Last Updated:</span>
                    <span class="detail-value">${modelInfo.last_modified ? new Date(modelInfo.last_modified * 1000).toLocaleString() : 'Unknown'}</span>
                </div>
            </div>
        `;
    } else {
        modelInfoEl.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-exclamation-triangle text-warning"></i>
                <p class="text-muted">No model information available</p>
                <small class="text-muted">Try rebuilding the model to generate statistics</small>
            </div>
        `;
    }
}

function showStatsError() {
    const modelInfoEl = document.getElementById('modelInfo');
    if (modelInfoEl) {
        modelInfoEl.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-exclamation-triangle text-danger"></i>
                <p class="text-danger">Error loading model information</p>
            </div>
        `;
    }
}

// ====================================================================
// Quick Settings
// ====================================================================

function initializeQuickSettings() {
    const saveLinesBtn = document.getElementById('saveLinesBtn');
    const saveTimeBtn = document.getElementById('saveTimeBtn');
    
    if (saveLinesBtn) {
        saveLinesBtn.addEventListener('click', saveLinesSetting);
    }
    
    if (saveTimeBtn) {
        saveTimeBtn.addEventListener('click', saveTimeSetting);
    }
}

async function saveLinesSetting() {
    const input = document.getElementById('linesBetweenInput');
    const btn = document.getElementById('saveLinesBtn');
    
    if (!input || !btn) return;
    
    const value = parseInt(input.value);
    if (isNaN(value) || value < 1 || value > 1000) {
        showToast('Lines between messages must be between 1 and 1000', 'warning');
        return;
    }
    
    await saveSetting('lines_between_messages', value, btn);
}

async function saveTimeSetting() {
    const input = document.getElementById('timeBetweenInput');
    const btn = document.getElementById('saveTimeBtn');
    
    if (!input || !btn) return;
    
    const value = parseInt(input.value);
    if (isNaN(value) || value < 0 || value > 3600) {
        showToast('Time between messages must be between 0 and 3600 seconds', 'warning');
        return;
    }
    
    await saveSetting('time_between_messages', value, btn);
}

async function saveSetting(settingName, value, btn) {
    const channelName = window.channelData.name;
    const originalText = btn.innerHTML;
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    
    try {
        await betaUtils.apiRequest('/update-channel-settings', {
            method: 'POST',
            body: JSON.stringify({
                channel_name: channelName,
                [settingName]: value
            })
        });
        
        showToast(`${settingName.replace('_', ' ')} updated successfully`, 'success');
        
    } catch (error) {
        console.error('[Beta Channel] Error updating setting:', error);
        showToast('Failed to update setting', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// ====================================================================
// Connection Status
// ====================================================================

async function updateConnectionStatus() {
    const channelName = window.channelData.name;
    const statusEl = document.getElementById('channelStatus');
    
    if (!statusEl) return;
    
    try {
        const response = await betaUtils.apiRequest('/api/bot-status');
        
        let isConnected = false;
        if (response.running && response.joined_channels) {
            isConnected = response.joined_channels.some(ch => 
                ch.toLowerCase().replace('#', '') === channelName.toLowerCase()
            );
        }
        
        const icon = statusEl.querySelector('i');
        
        if (isConnected) {
            statusEl.className = 'status-indicator status-online';
            statusEl.innerHTML = '<i class="fas fa-circle"></i> Connected';
        } else if (response.running) {
            statusEl.className = 'status-indicator status-offline';
            statusEl.innerHTML = '<i class="fas fa-circle"></i> Disconnected';
        } else {
            statusEl.className = 'status-indicator status-offline';
            statusEl.innerHTML = '<i class="fas fa-circle"></i> Bot Offline';
        }
        
    } catch (error) {
        console.error('[Beta Channel] Error updating connection status:', error);
    }
}

// ====================================================================
// Trusted Users Management
// ====================================================================

async function addTrustedUser() {
    const input = document.getElementById('newTrustedUserInput');
    const username = input.value.trim();
    
    if (!username) {
        showToast('Please enter a username', 'warning');
        return;
    }
    
    // Basic username validation
    if (!/^[a-zA-Z0-9_-]+$/.test(username)) {
        showToast('Username can only contain letters, numbers, underscores, and hyphens', 'error');
        return;
    }
    
    const channelName = window.channelData.name;
    
    try {
        const response = await betaUtils.apiRequest('/api/channel/trusted-users', {
            method: 'POST',
            body: JSON.stringify({
                channel_name: channelName,
                username: username,
                action: 'add'
            })
        });
        
        // Add the user to the UI
        addTrustedUserToUI(username);
        input.value = '';
        showToast(`Added ${username} as trusted user`, 'success');
        
    } catch (error) {
        console.error('[Beta Channel] Error adding trusted user:', error);
        showToast(`Failed to add trusted user: ${error.message}`, 'error');
    }
}

async function removeTrustedUser(username) {
    const channelName = window.channelData.name;
    
    try {
        const response = await betaUtils.apiRequest('/api/channel/trusted-users', {
            method: 'POST',
            body: JSON.stringify({
                channel_name: channelName,
                username: username,
                action: 'remove'
            })
        });
        
        // Remove the user from the UI
        removeTrustedUserFromUI(username);
        showToast(`Removed ${username} from trusted users`, 'success');
        
    } catch (error) {
        console.error('[Beta Channel] Error removing trusted user:', error);
        showToast(`Failed to remove trusted user: ${error.message}`, 'error');
    }
}

function addTrustedUserToUI(username) {
    const usersList = document.getElementById('trustedUsersList');
    const noUsersText = document.getElementById('noTrustedUsers');
    
    if (noUsersText) {
        noUsersText.style.display = 'none';
    }
    
    const badge = document.createElement('span');
    badge.className = 'badge bg-primary me-1 mb-1 trusted-user-badge';
    badge.dataset.username = username;
    badge.innerHTML = `
        ${username}
        <button type="button" class="btn-close btn-close-white ms-1" 
                onclick="removeTrustedUser('${username}')" 
                style="font-size: 0.6em;"></button>
    `;
    
    usersList.appendChild(badge);
}

function removeTrustedUserFromUI(username) {
    const badge = document.querySelector(`[data-username="${username}"]`);
    if (badge) {
        badge.remove();
    }
    
    // Check if no users remain
    const remainingUsers = document.querySelectorAll('.trusted-user-badge');
    if (remainingUsers.length === 0) {
        const usersList = document.getElementById('trustedUsersList');
        usersList.innerHTML = '<span class="text-muted" id="noTrustedUsers">No trusted users</span>';
    }
}

function initializeTrustedUsers() {
    // Add trusted user button
    const addBtn = document.getElementById('addTrustedUserBtn');
    if (addBtn) {
        addBtn.addEventListener('click', addTrustedUser);
    }
    
    // Enter key on input
    const input = document.getElementById('newTrustedUserInput');
    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                addTrustedUser();
            }
        });
    }
}

// Make functions available globally for inline onclick handlers
window.removeTrustedUser = removeTrustedUser;
window.loadChatMessages = loadChatMessages;
window.loadTTSHistory = loadTTSHistory;