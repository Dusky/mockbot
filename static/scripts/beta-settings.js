// Beta Settings Page JavaScript
document.addEventListener('DOMContentLoaded', function() {
    initializeSettingsPage();
});

// Toast notification function
function showToast(message, type = 'info') {
    // Use the global notification system if available
    if (window.notificationSystem && window.notificationSystem.showToast) {
        window.notificationSystem.showToast(message, type);
    } else if (window.showToast) {
        window.showToast(message, type);
    } else {
        // Fallback to console log
        console.log(`[${type.toUpperCase()}] ${message}`);
        
        // For errors, also use alert as fallback
        if (type === 'error') {
            alert(`Error: ${message}`);
        }
    }
}

function initializeSettingsPage() {
    setupTabSwitching();
    setupChannelSettings();
    setupBotControls();
    setupTTSSettings();
    setupAdvancedSettings();
    loadCurrentSettings();
    updateBotStatus(); // Add bot status checking
}

function setupTabSwitching() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');
    
    tabButtons.forEach(button => {
        button.addEventListener('click', function() {
            const targetTab = this.dataset.tab;
            
            // Update active button
            tabButtons.forEach(btn => btn.classList.remove('active'));
            this.classList.add('active');
            
            // Update active pane
            tabPanes.forEach(pane => {
                if (pane.id === targetTab) {
                    pane.classList.add('active');
                } else {
                    pane.classList.remove('active');
                }
            });
        });
    });
}

function setupChannelSettings() {
    // Channel filter
    const channelFilter = document.getElementById('channelFilter');
    if (channelFilter) {
        channelFilter.addEventListener('input', function() {
            filterChannels(this.value);
        });
    }

    // Select all channels checkbox
    const selectAllCheckbox = document.getElementById('selectAllChannels');
    if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener('change', function() {
            toggleAllChannels();
        });
    }

    // Individual channel checkboxes
    document.querySelectorAll('.channel-select').forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            updateSelectAllState();
        });
    });

    // Add new channel modal
    const saveChannelBtn = document.getElementById('saveChannelBtn');
    
    if (saveChannelBtn) {
        saveChannelBtn.addEventListener('click', function() {
            addNewChannel();
        });
    }
}

function setupBotControls() {
    // Bot start/stop/restart buttons
    const startBtn = document.querySelector('[data-action="start-bot"]');
    const stopBtn = document.querySelector('[data-action="stop-bot"]');
    const restartBtn = document.querySelector('[data-action="restart-bot"]');

    if (startBtn) {
        startBtn.addEventListener('click', () => controlBot('start'));
    }
    if (stopBtn) {
        stopBtn.addEventListener('click', () => controlBot('stop'));
    }
    if (restartBtn) {
        restartBtn.addEventListener('click', () => controlBot('restart'));
    }

    // Bot configuration settings
    document.querySelectorAll('.bot-setting').forEach(input => {
        input.addEventListener('change', function() {
            const setting = this.dataset.setting;
            const value = this.type === 'checkbox' ? this.checked : this.value;
            updateBotSetting(setting, value);
        });
    });
}

function setupTTSSettings() {
    // TTS provider selection
    const ttsProvider = document.getElementById('ttsProvider');
    if (ttsProvider) {
        ttsProvider.addEventListener('change', function() {
            updateTTSSetting('provider', this.value);
            toggleProviderSettings(this.value);
        });
    }

    // TTS settings inputs
    document.querySelectorAll('.tts-setting').forEach(input => {
        input.addEventListener('change', function() {
            const setting = this.dataset.setting;
            const value = this.type === 'checkbox' ? this.checked : this.value;
            updateTTSSetting(setting, value);
        });
    });

    // Test TTS button
    const testTTSBtn = document.querySelector('[data-action="test-tts"]');
    if (testTTSBtn) {
        testTTSBtn.addEventListener('click', testTTS);
    }

    // Voice selection
    const voiceSelects = document.querySelectorAll('.voice-select');
    voiceSelects.forEach(select => {
        select.addEventListener('change', function() {
            const provider = this.dataset.provider;
            const voice = this.value;
            updateTTSSetting(`${provider}_voice`, voice);
        });
    });
}

function setupAdvancedSettings() {
    // Rebuild all models
    const rebuildAllBtn = document.getElementById('rebuildAllBtn');
    if (rebuildAllBtn) {
        rebuildAllBtn.addEventListener('click', function() {
            if (confirm('Are you sure you want to rebuild all models? This may take several minutes.')) {
                rebuildAllModels();
            }
        });
    }

    // Clear cache
    const clearCacheBtn = document.getElementById('clearCacheBtn');
    if (clearCacheBtn) {
        clearCacheBtn.addEventListener('click', function() {
            if (confirm('Are you sure you want to clear all cached data?')) {
                clearCache();
            }
        });
    }

    // Export settings
    const exportSettingsBtn = document.getElementById('exportSettingsBtn');
    if (exportSettingsBtn) {
        exportSettingsBtn.addEventListener('click', exportSettings);
    }

    // Reset to defaults
    const resetDefaultsBtn = document.getElementById('resetDefaultsBtn');
    if (resetDefaultsBtn) {
        resetDefaultsBtn.addEventListener('click', function() {
            if (confirm('This will reset all settings to defaults. This cannot be undone. Continue?')) {
                resetToDefaults();
            }
        });
    }
}

async function updateChannelStatus(channel, enabled) {
    try {
        const response = await fetch(`/api/channel/${channel}/status`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ enabled })
        });
        
        if (response.ok) {
            showToast(`${channel} ${enabled ? 'enabled' : 'disabled'}`, 'success');
        } else {
            throw new Error('Failed to update channel status');
        }
    } catch (error) {
        console.error('Error updating channel status:', error);
        showToast('Failed to update channel status', 'error');
    }
}

async function updateChannelSetting(channel, setting, value) {
    try {
        const response = await fetch(`/api/channel/${channel}/settings`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ [setting]: value })
        });
        
        if (response.ok) {
            showToast(`${channel} ${setting} updated`, 'success');
        } else {
            throw new Error('Failed to update channel setting');
        }
    } catch (error) {
        console.error('Error updating channel setting:', error);
        showToast('Failed to update setting', 'error');
    }
}

async function joinChannel(channel) {
    try {
        const response = await fetch(`/api/channel/${channel}/join`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast(`Joined #${channel}`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            throw new Error('Failed to join channel');
        }
    } catch (error) {
        console.error('Error joining channel:', error);
        showToast(`Failed to join #${channel}`, 'error');
    }
}

async function leaveChannel(channel) {
    try {
        const response = await fetch(`/api/channel/${channel}/leave`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast(`Left #${channel}`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            throw new Error('Failed to leave channel');
        }
    } catch (error) {
        console.error('Error leaving channel:', error);
        showToast(`Failed to leave #${channel}`, 'error');
    }
}

async function addChannel(channelName) {
    // Remove # if provided
    channelName = channelName.replace('#', '');
    
    try {
        const response = await fetch(`/api/channel/${channelName}/add`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast(`Added #${channelName}`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            throw new Error('Failed to add channel');
        }
    } catch (error) {
        console.error('Error adding channel:', error);
        showToast(`Failed to add #${channelName}`, 'error');
    }
}

async function controlBot(action) {
    try {
        const response = await fetch(`/api/bot/${action}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast(`Bot ${action} command sent`, 'success');
            setTimeout(() => location.reload(), 2000);
        } else {
            throw new Error(`Failed to ${action} bot`);
        }
    } catch (error) {
        console.error(`Error ${action}ing bot:`, error);
        showToast(`Failed to ${action} bot`, 'error');
    }
}

async function updateBotSetting(setting, value) {
    try {
        const response = await fetch('/api/bot/settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ [setting]: value })
        });
        
        if (response.ok) {
            showToast(`Bot ${setting} updated`, 'success');
        } else {
            throw new Error('Failed to update bot setting');
        }
    } catch (error) {
        console.error('Error updating bot setting:', error);
        showToast('Failed to update bot setting', 'error');
    }
}

async function updateTTSSetting(setting, value) {
    try {
        const response = await fetch('/api/tts/settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ [setting]: value })
        });
        
        if (response.ok) {
            showToast(`TTS ${setting} updated`, 'success');
        } else {
            throw new Error('Failed to update TTS setting');
        }
    } catch (error) {
        console.error('Error updating TTS setting:', error);
        showToast('Failed to update TTS setting', 'error');
    }
}

function toggleProviderSettings(provider) {
    const providerSections = document.querySelectorAll('.provider-settings');
    providerSections.forEach(section => {
        if (section.dataset.provider === provider) {
            section.style.display = 'block';
        } else {
            section.style.display = 'none';
        }
    });
}

async function testTTS() {
    try {
        showToast('Testing TTS...', 'info');
        const response = await fetch('/api/tts/test', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ text: 'This is a test of the text to speech system.' })
        });
        
        if (response.ok) {
            const result = await response.json();
            if (result.audio_url) {
                const audio = new Audio(result.audio_url);
                audio.play();
                showToast('TTS test successful', 'success');
            }
        } else {
            throw new Error('Failed to test TTS');
        }
    } catch (error) {
        console.error('Error testing TTS:', error);
        showToast('TTS test failed', 'error');
    }
}

async function cleanupDatabase() {
    try {
        const response = await fetch('/api/database/cleanup', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            const result = await response.json();
            showToast(`Database cleanup complete. Removed ${result.removed_count} old records.`, 'success');
        } else {
            throw new Error('Failed to cleanup database');
        }
    } catch (error) {
        console.error('Error cleaning up database:', error);
        showToast('Database cleanup failed', 'error');
    }
}

async function rebuildAllModels() {
    try {
        showToast('Starting model rebuild...', 'info');
        const response = await fetch('/rebuild-all-caches', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast('All models rebuild started successfully', 'success');
            setTimeout(() => location.reload(), 2000);
        } else {
            throw new Error('Failed to start rebuild');
        }
    } catch (error) {
        console.error('Error rebuilding models:', error);
        showToast('Failed to rebuild models', 'error');
    }
}

async function clearCache() {
    try {
        const response = await fetch('/api/clear-cache', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast('Cache cleared successfully', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            throw new Error('Failed to clear cache');
        }
    } catch (error) {
        console.error('Error clearing cache:', error);
        showToast('Failed to clear cache', 'error');
    }
}

async function exportSettings() {
    try {
        const response = await fetch('/api/settings/export');
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ansv-settings-${new Date().toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showToast('Settings exported successfully', 'success');
        } else {
            throw new Error('Failed to export settings');
        }
    } catch (error) {
        console.error('Error exporting settings:', error);
        showToast('Failed to export settings', 'error');
    }
}

async function importSettings(file) {
    try {
        const formData = new FormData();
        formData.append('settings_file', file);
        
        const response = await fetch('/api/settings/import', {
            method: 'POST',
            body: formData
        });
        
        if (response.ok) {
            showToast('Settings imported successfully', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            throw new Error('Failed to import settings');
        }
    } catch (error) {
        console.error('Error importing settings:', error);
        showToast('Failed to import settings', 'error');
    }
}

async function resetToDefaults() {
    try {
        const response = await fetch('/api/settings/reset', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast('Settings reset to defaults', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            throw new Error('Failed to reset settings');
        }
    } catch (error) {
        console.error('Error resetting settings:', error);
        showToast('Failed to reset settings', 'error');
    }
}

async function loadCurrentSettings() {
    try {
        const response = await fetch('/api/settings/current');
        if (!response.ok) return;
        
        const settings = await response.json();
        
        // Load channel settings
        if (settings.channels) {
            Object.entries(settings.channels).forEach(([channel, config]) => {
                updateChannelUI(channel, config);
            });
        }
        
        // Load bot settings
        if (settings.bot) {
            Object.entries(settings.bot).forEach(([key, value]) => {
                updateBotUI(key, value);
            });
        }
        
        // Load TTS settings
        if (settings.tts) {
            Object.entries(settings.tts).forEach(([key, value]) => {
                updateTTSUI(key, value);
            });
        }
        
    } catch (error) {
        console.error('Error loading current settings:', error);
    }
}

function updateChannelUI(channel, config) {
    const toggle = document.querySelector(`[data-channel="${channel}"].channel-toggle`);
    if (toggle) {
        toggle.checked = config.enabled;
    }
    
    Object.entries(config).forEach(([setting, value]) => {
        const input = document.querySelector(`[data-channel="${channel}"][data-setting="${setting}"]`);
        if (input) {
            if (input.type === 'checkbox') {
                input.checked = value;
            } else {
                input.value = value;
            }
        }
    });
}

function updateBotUI(setting, value) {
    const input = document.querySelector(`[data-setting="${setting}"].bot-setting`);
    if (input) {
        if (input.type === 'checkbox') {
            input.checked = value;
        } else {
            input.value = value;
        }
    }
}

function updateTTSUI(setting, value) {
    const input = document.querySelector(`[data-setting="${setting}"].tts-setting`);
    if (input) {
        if (input.type === 'checkbox') {
            input.checked = value;
        } else {
            input.value = value;
        }
    }
    
    // Handle TTS provider selection
    if (setting === 'provider') {
        toggleProviderSettings(value);
    }
}

function toggleChannelSettings(channelName) {
    const settingsDiv = document.getElementById(`settings-${channelName}`);
    if (settingsDiv) {
        if (settingsDiv.style.display === 'none' || !settingsDiv.style.display) {
            settingsDiv.style.display = 'block';
        } else {
            settingsDiv.style.display = 'none';
        }
    }
}

function toggleChannelDetails(channelName) {
    const detailsRow = document.getElementById(`details-${channelName}`);
    if (detailsRow) {
        if (detailsRow.style.display === 'none' || !detailsRow.style.display) {
            detailsRow.style.display = 'table-row';
        } else {
            detailsRow.style.display = 'none';
        }
    }
}

function deleteChannel(channelName) {
    if (confirm(`Are you sure you want to delete the configuration for #${channelName}? This will not make the bot leave the channel.`)) {
        leaveChannel(channelName);
    }
}

function filterChannels(searchTerm) {
    const channelRows = document.querySelectorAll('.channel-row');
    const detailRows = document.querySelectorAll('.channel-details-row');
    
    channelRows.forEach((row, index) => {
        const channelName = row.dataset.channel.toLowerCase();
        const shouldShow = channelName.includes(searchTerm.toLowerCase());
        
        row.style.display = shouldShow ? 'table-row' : 'none';
        
        // Hide corresponding detail row if channel is hidden
        if (detailRows[index]) {
            if (!shouldShow) {
                detailRows[index].style.display = 'none';
            }
        }
    });
}

function toggleAllChannels() {
    const selectAllCheckbox = document.getElementById('selectAllChannels');
    const channelCheckboxes = document.querySelectorAll('.channel-select');
    
    channelCheckboxes.forEach(checkbox => {
        checkbox.checked = selectAllCheckbox.checked;
    });
}

function updateSelectAllState() {
    const selectAllCheckbox = document.getElementById('selectAllChannels');
    const channelCheckboxes = document.querySelectorAll('.channel-select');
    const checkedCount = document.querySelectorAll('.channel-select:checked').length;
    
    if (checkedCount === 0) {
        selectAllCheckbox.indeterminate = false;
        selectAllCheckbox.checked = false;
    } else if (checkedCount === channelCheckboxes.length) {
        selectAllCheckbox.indeterminate = false;
        selectAllCheckbox.checked = true;
    } else {
        selectAllCheckbox.indeterminate = true;
    }
}

function expandAllChannels() {
    const detailRows = document.querySelectorAll('.channel-details-row');
    detailRows.forEach(row => {
        row.style.display = 'table-row';
    });
}

function collapseAllChannels() {
    const detailRows = document.querySelectorAll('.channel-details-row');
    detailRows.forEach(row => {
        row.style.display = 'none';
    });
}

function bulkEnableChannels() {
    const selectedChannels = getSelectedChannels();
    if (selectedChannels.length === 0) {
        showToast('No channels selected', 'warning');
        return;
    }
    
    if (confirm(`Enable auto-join for ${selectedChannels.length} selected channels?`)) {
        selectedChannels.forEach(channel => {
            updateChannelSetting(channel, 'join_channel', true);
            // Update the UI checkbox
            const checkbox = document.querySelector(`[data-channel="${channel}"] .form-check-input`);
            if (checkbox) checkbox.checked = true;
        });
        showToast(`Enabled ${selectedChannels.length} channels`, 'success');
    }
}

function bulkDisableChannels() {
    const selectedChannels = getSelectedChannels();
    if (selectedChannels.length === 0) {
        showToast('No channels selected', 'warning');
        return;
    }
    
    if (confirm(`Disable auto-join for ${selectedChannels.length} selected channels?`)) {
        selectedChannels.forEach(channel => {
            updateChannelSetting(channel, 'join_channel', false);
            // Update the UI checkbox
            const checkbox = document.querySelector(`[data-channel="${channel}"] .form-check-input`);
            if (checkbox) checkbox.checked = false;
        });
        showToast(`Disabled ${selectedChannels.length} channels`, 'success');
    }
}

function getSelectedChannels() {
    const selectedCheckboxes = document.querySelectorAll('.channel-select:checked');
    return Array.from(selectedCheckboxes).map(checkbox => checkbox.value);
}

// Function to update bot status display
async function updateBotStatus() {
    const statusIcon = document.getElementById('botStatusIcon');
    const statusTitle = document.getElementById('botStatusTitle');
    const statusDesc = document.getElementById('botStatusDesc');
    const uptimeElement = document.getElementById('botUptime');
    const connectedChannelsElement = document.getElementById('connectedChannels');
    const memoryUsageElement = document.getElementById('memoryUsage');
    
    try {
        const response = await fetch('/api/bot-status');
        if (!response.ok) throw new Error('Failed to fetch bot status');
        
        const data = await response.json();
        
        // Update status display
        if (statusIcon && statusTitle && statusDesc) {
            if (data.running) {
                statusIcon.className = 'fas fa-robot text-success';
                statusTitle.textContent = 'Bot Online';
                statusDesc.textContent = data.connected ? 'Connected and running normally' : 'Running but not connected to Twitch';
                statusDesc.className = data.connected ? 'status-description text-success' : 'status-description text-warning';
            } else {
                statusIcon.className = 'fas fa-robot text-danger';
                statusTitle.textContent = 'Bot Offline';
                statusDesc.textContent = 'Bot is currently stopped';
                statusDesc.className = 'status-description text-danger';
            }
        }
        
        // Update performance metrics if elements exist
        if (uptimeElement && data.uptime) {
            const uptime = formatUptime(data.uptime);
            uptimeElement.textContent = uptime;
        } else if (uptimeElement) {
            uptimeElement.textContent = data.running ? 'Just started' : 'Offline';
        }
        
        if (connectedChannelsElement) {
            if (data.joined_channels && Array.isArray(data.joined_channels)) {
                connectedChannelsElement.textContent = data.joined_channels.length;
            } else {
                connectedChannelsElement.textContent = data.running ? '0' : 'Offline';
            }
        }
        
        if (memoryUsageElement) {
            // Try to get memory usage from system info API
            try {
                fetch('/api/system-info')
                    .then(response => response.json())
                    .then(sysData => {
                        if (sysData.memory_usage) {
                            memoryUsageElement.textContent = sysData.memory_usage;
                        } else {
                            memoryUsageElement.textContent = 'Unknown';
                        }
                    })
                    .catch(() => {
                        memoryUsageElement.textContent = 'Unknown';
                    });
            } catch (e) {
                memoryUsageElement.textContent = 'Unknown';
            }
        }
        
        // Show/hide bot control buttons based on status
        const startBtn = document.getElementById('startBotBtn');
        const stopBtn = document.getElementById('stopBotBtn');
        
        if (startBtn && stopBtn) {
            if (data.running) {
                startBtn.style.display = 'none';
                stopBtn.style.display = 'inline-block';
            } else {
                startBtn.style.display = 'inline-block';
                stopBtn.style.display = 'none';
            }
        }
        
        // Update navbar status (same logic as beta-base.js)
        const navStatus = document.getElementById('botStatusNav');
        if (navStatus) {
            const icon = navStatus.querySelector('i');
            const text = navStatus.querySelector('span');
            
            if (data.running && data.connected) {
                icon.className = 'fas fa-circle text-success me-1';
                text.textContent = 'Online';
            } else if (data.running) {
                icon.className = 'fas fa-circle text-warning me-1';
                text.textContent = 'Starting...';
            } else {
                icon.className = 'fas fa-circle text-danger me-1';
                text.textContent = 'Offline';
            }
        }
        
    } catch (error) {
        console.error('Error fetching bot status:', error);
        
        // Show error state
        if (statusIcon && statusTitle && statusDesc) {
            statusIcon.className = 'fas fa-robot text-warning';
            statusTitle.textContent = 'Status Unknown';
            statusDesc.textContent = 'Unable to connect to bot service';
            statusDesc.className = 'status-description text-warning';
        }
        
        // Update navbar status to unknown state
        const navStatus = document.getElementById('botStatusNav');
        if (navStatus) {
            const icon = navStatus.querySelector('i');
            const text = navStatus.querySelector('span');
            icon.className = 'fas fa-circle text-muted me-1';
            text.textContent = 'Unknown';
        }
    }
}

// Add new channel function
async function addNewChannel() {
    const channelName = document.getElementById('newChannelName').value.trim();
    const channelOwner = document.getElementById('newChannelOwner').value.trim();
    const joinChannel = document.getElementById('newChannelJoin').checked;
    const ttsEnabled = document.getElementById('newChannelTts').checked;
    const voiceEnabled = document.getElementById('newChannelVoice').checked;
    
    if (!channelName) {
        showToast('Please enter a channel name', 'error');
        return;
    }
    
    // Validate channel name
    if (!/^[a-zA-Z0-9_]+$/.test(channelName)) {
        showToast('Channel name can only contain letters, numbers, and underscores', 'error');
        return;
    }
    
    try {
        const response = await fetch('/add-channel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                channel_name: channelName,
                owner: channelOwner || channelName,
                join_channel: joinChannel,
                tts_enabled: ttsEnabled,
                voice_enabled: voiceEnabled
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(`Channel ${channelName} added successfully`, 'success');
            
            // Reset form
            document.getElementById('addChannelForm').reset();
            document.getElementById('newChannelJoin').checked = true;
            document.getElementById('newChannelTts').checked = true;
            document.getElementById('newChannelVoice').checked = true;
            
            // Hide modal
            const modal = bootstrap.Modal.getInstance(document.getElementById('addChannelModal'));
            if (modal) {
                modal.hide();
            }
            
            // Reload page to show new channel
            setTimeout(() => {
                location.reload();
            }, 1000);
            
        } else {
            showToast(data.error || 'Failed to add channel', 'error');
        }
        
    } catch (error) {
        console.error('Error adding channel:', error);
        showToast('Error adding channel', 'error');
    }
}

// Helper function to format uptime
function formatUptime(seconds) {
    if (!seconds || seconds < 0) return 'Unknown';

    const days = Math.floor(seconds / (24 * 3600));
    const hours = Math.floor((seconds % (24 * 3600)) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    let result = '';
    if (days > 0) result += `${days}d `;
    if (hours > 0 || days > 0) result += `${hours}h `;
    if (minutes > 0 || hours > 0 || days > 0) result += `${minutes}m `;
    result += `${secs}s`;

    return result.trim();
}

// ========================================
// Export Functionality
// ========================================

async function exportData(type) {
    try {
        const response = await fetch(`/api/export/${type}`);

        if (!response.ok) throw new Error('Export failed');

        const blob = await response.blob();
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = `ansv-bot-${type}-${new Date().toISOString().split('T')[0]}`;

        if (contentDisposition) {
            const matches = /filename="?([^"]+)"?/.exec(contentDisposition);
            if (matches) filename = matches[1];
        } else {
            filename += type === 'settings' ? '.json' : '.csv';
        }

        // Create download link
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showToast(`Exported ${type} successfully`, 'success');
    } catch (error) {
        console.error('Error exporting data:', error);
        showToast(`Failed to export ${type}`, 'error');
    }
}

// ========================================
// Bulk Operations
// ========================================

// Toggle select all channels
function toggleAllChannels() {
    const selectAll = document.getElementById('selectAllChannels');
    const checkboxes = document.querySelectorAll('.channel-select');

    checkboxes.forEach(checkbox => {
        checkbox.checked = selectAll.checked;
    });

    updateBulkActionsBar();
}

// Update bulk actions bar visibility
function updateBulkActionsBar() {
    const checkboxes = document.querySelectorAll('.channel-select:checked');
    const bulkBar = document.getElementById('bulkActionsBar');
    const selectedCount = document.getElementById('selectedCount');

    if (checkboxes.length > 0) {
        bulkBar.style.display = 'block';
        selectedCount.textContent = `${checkboxes.length} channel${checkboxes.length > 1 ? 's' : ''} selected`;
    } else {
        bulkBar.style.display = 'none';
    }
}

// Add event listeners to channel checkboxes
document.addEventListener('DOMContentLoaded', function() {
    const checkboxes = document.querySelectorAll('.channel-select');
    checkboxes.forEach(checkbox => {
        checkbox.addEventListener('change', updateBulkActionsBar);
    });
});

// Get selected channel names
function getSelectedChannels() {
    const checkboxes = document.querySelectorAll('.channel-select:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

// Bulk enable TTS
async function bulkEnableTTS() {
    const channels = getSelectedChannels();
    if (channels.length === 0) {
        showToast('No channels selected', 'warning');
        return;
    }

    if (!confirm(`Enable TTS for ${channels.length} channel(s)?`)) return;

    try {
        const response = await fetch('/api/bulk/tts/enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channels })
        });

        const data = await response.json();
        if (data.success) {
            showToast(`TTS enabled for ${channels.length} channel(s)`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error || 'Failed to enable TTS', 'error');
        }
    } catch (error) {
        console.error('Error enabling TTS:', error);
        showToast('Error enabling TTS', 'error');
    }
}

// Bulk disable TTS
async function bulkDisableTTS() {
    const channels = getSelectedChannels();
    if (channels.length === 0) {
        showToast('No channels selected', 'warning');
        return;
    }

    if (!confirm(`Disable TTS for ${channels.length} channel(s)?`)) return;

    try {
        const response = await fetch('/api/bulk/tts/disable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channels })
        });

        const data = await response.json();
        if (data.success) {
            showToast(`TTS disabled for ${channels.length} channel(s)`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error || 'Failed to disable TTS', 'error');
        }
    } catch (error) {
        console.error('Error disabling TTS:', error);
        showToast('Error disabling TTS', 'error');
    }
}

// Bulk enable auto-join
async function bulkEnableAutoJoin() {
    const channels = getSelectedChannels();
    if (channels.length === 0) {
        showToast('No channels selected', 'warning');
        return;
    }

    if (!confirm(`Enable auto-join for ${channels.length} channel(s)?`)) return;

    try {
        const response = await fetch('/api/bulk/autojoin/enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channels })
        });

        const data = await response.json();
        if (data.success) {
            showToast(`Auto-join enabled for ${channels.length} channel(s)`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error || 'Failed to enable auto-join', 'error');
        }
    } catch (error) {
        console.error('Error enabling auto-join:', error);
        showToast('Error enabling auto-join', 'error');
    }
}

// Bulk disable auto-join
async function bulkDisableAutoJoin() {
    const channels = getSelectedChannels();
    if (channels.length === 0) {
        showToast('No channels selected', 'warning');
        return;
    }

    if (!confirm(`Disable auto-join for ${channels.length} channel(s)?`)) return;

    try {
        const response = await fetch('/api/bulk/autojoin/disable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channels })
        });

        const data = await response.json();
        if (data.success) {
            showToast(`Auto-join disabled for ${channels.length} channel(s)`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error || 'Failed to disable auto-join', 'error');
        }
    } catch (error) {
        console.error('Error disabling auto-join:', error);
        showToast('Error disabling auto-join', 'error');
    }
}

// Bulk delete channels
async function bulkDeleteChannels() {
    const channels = getSelectedChannels();
    if (channels.length === 0) {
        showToast('No channels selected', 'warning');
        return;
    }

    const confirmation = prompt(`Type DELETE to confirm deletion of ${channels.length} channel(s):`);
    if (confirmation !== 'DELETE') {
        showToast('Deletion cancelled', 'info');
        return;
    }

    try {
        const response = await fetch('/api/bulk/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channels })
        });

        const data = await response.json();
        if (data.success) {
            showToast(`Deleted ${channels.length} channel(s)`, 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error || 'Failed to delete channels', 'error');
        }
    } catch (error) {
        console.error('Error deleting channels:', error);
        showToast('Error deleting channels', 'error');
    }
}

// Set up periodic status updates (every 30 seconds)
setInterval(updateBotStatus, 30000);