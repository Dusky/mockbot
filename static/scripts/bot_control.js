/**
 * bot_control.js - Centralized bot control system
 * 
 * This file provides functionality for starting, stopping and managing the bot.
 * It works with bot_status.js to provide a complete bot management solution.
 */

// Create a global namespace for bot control
window.BotController = window.BotController || {
    // Initialize the bot controller
    init: function() {
        console.log("BotController initializing");
        
        // Setup event listeners for control buttons
        this.setupEventListeners();
        
        // Check for existing bot instances
        this.checkForExistingBotInstance();
        
        // Load channels if the channel table exists
        const channelList = document.getElementById('channelsTableBody');
        if (channelList) {
            this.loadChannels();
        }
        
        console.log("BotController initialized");
        return this;
    },
    
    // Set up event listeners for control buttons
    setupEventListeners: function() {
        // Get button elements
        const startBtn = document.getElementById('startBotBtn');
        const stopBtn = document.getElementById('stopBotBtn');
        
        // Setup start button
        if (startBtn) {
            startBtn.addEventListener('click', () => this.startBot());
        }
        
        // Setup stop button
        if (stopBtn) {
            stopBtn.addEventListener('click', () => this.stopBot());
        }
    },
    
    // Check for existing bot instances
    checkForExistingBotInstance: function() {
        fetch('/check_existing_instance')
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    console.log('Found existing bot instance:', data);
                    
                    // Show notification using notification system
                    window.notificationSystem.showToast(`Bot is already running (PID: ${data.pid})`, 'info');
                    
                    // Trigger status check to update buttons
                    if (window.BotStatus) {
                        window.BotStatus.checkStatus();
                    }
                }
            })
            .catch(error => {
                console.error('Error checking for existing instances:', error);
            });
    },
    
    // Start the bot with optional TTS support
    startBot: function() {
        // Show enhanced loading notification with progress tracking
        const loadingToastId = window.showLoading('Starting bot... This may take 30-60 seconds');
        
        // Disable buttons during operation
        document.getElementById('startBotBtn').disabled = true;
        document.getElementById('stopBotBtn').disabled = true;
        
        // Update UI to show starting state with progress indicator
        const startBtn = document.getElementById('startBotBtn');
        const originalStartText = startBtn.innerHTML;
        startBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Starting...';
        
        // Show progress steps to user
        const progressSteps = [
            'Initializing bot service...',
            'Loading configuration...',
            'Connecting to Twitch...',
            'Starting web interface...'
        ];
        
        let currentStep = 0;
        const progressInterval = setInterval(() => {
            if (currentStep < progressSteps.length) {
                startBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${progressSteps[currentStep]}`;
                currentStep++;
            }
        }, 3000); // Update every 3 seconds
        
        // Get TTS status with better validation
        const ttsElement = document.getElementById('enable_tts');
        let enableTTS = false;
        
        if (ttsElement) {
            console.log('TTS checkbox element found, checked state:', ttsElement.checked);
            enableTTS = ttsElement.checked;
        } else {
            console.warn('TTS checkbox element not found');
        }
        
        console.log('Starting bot with TTS enabled:', enableTTS);
        
        // Clear any previous error messages
        const errorDisplay = document.getElementById('botStartError');
        if (errorDisplay) {
            errorDisplay.textContent = '';
            errorDisplay.style.display = 'none';
        }
        
        // Send request to start bot
        fetch('/start_bot', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ enable_tts: enableTTS })
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server returned ${response.status}: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            console.log('Bot start response:', data);
            
            // Clear progress interval
            clearInterval(progressInterval);
            
            if (data.success) {
                // Complete loading notification successfully
                window.completeLoading(loadingToastId, '🤖 Bot started successfully! Ready to chat.', 'success');
                
                // Reset the button text with success state
                startBtn.innerHTML = '<i class="fas fa-check me-2"></i>Started';
                startBtn.className = startBtn.className.replace('btn-primary', 'btn-success');
                
                // Auto-reset button appearance after 3 seconds
                setTimeout(() => {
                    startBtn.innerHTML = '<i class="fas fa-play me-2"></i>Start Bot';
                    startBtn.className = startBtn.className.replace('btn-success', 'btn-primary');
                }, 3000);
                
            } else {
                // Complete loading notification with error
                window.completeLoading(loadingToastId, `❌ Failed to start bot: ${data.message || 'Unknown error'}`, 'error');
                
                // Use enhanced error handling
                window.handleError(data.message || 'Bot start failed', 'bot_control');
                
                // Show detailed error information in expandable section
                if (errorDisplay) {
                    let errorContent = data.message || 'Unknown error';
                    if (data.log) {
                        errorContent += '\n\nTechnical Details:\n' + data.log;
                    }
                    
                    errorDisplay.textContent = errorContent;
                    errorDisplay.style.display = 'block';
                }
                
                console.error('Bot start failed:', data.log || data.message);
                
                // Reset button state for failed start
                this.resetButtonStates();
            }
        })
        .catch(error => {
            console.error('Error starting bot:', error);
            
            // Clear progress interval
            clearInterval(progressInterval);
            
            // Complete loading with error and use enhanced error handling
            window.completeLoading(loadingToastId, 'Failed to start bot', 'error');
            window.handleError(error, 'bot_control');
            
            // Show the error in the UI with helpful context
            if (errorDisplay) {
                errorDisplay.textContent = `Connection Error: ${error.message}\n\nThis might be a network issue or the server might be down.`;
                errorDisplay.style.display = 'block';
            }
            
            // Reset button state on error
            this.resetButtonStates();
        })
        .finally(() => {
            // Clear progress interval if still running
            clearInterval(progressInterval);
            
            // Refresh status after a short delay to verify state
            setTimeout(() => {
                if (window.BotStatus) {
                    window.BotStatus.checkStatus();
                }
            }, 2000);
        });
    },
    
    // Stop the bot
    stopBot: function() {
        if (confirm('Are you sure you want to stop the bot?')) {
            // Show enhanced loading notification
            const loadingToastId = window.showLoading('Stopping bot... This may take a few seconds');
            
            // Disable buttons during operation
            const startBtn = document.getElementById('startBotBtn');
            const stopBtn = document.getElementById('stopBotBtn');
            
            if (startBtn) startBtn.disabled = true;
            if (stopBtn) {
                stopBtn.disabled = true;
                const originalStopText = stopBtn.innerHTML;
                stopBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Stopping...';
            }
            
            fetch('/stop_bot', {
                method: 'POST'
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Server returned ${response.status}: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    // Complete loading notification successfully
                    window.completeLoading(loadingToastId, '⏹️ Bot stopped successfully', 'success');
                    
                    // Reset the stop button with success state
                    if (stopBtn) {
                        stopBtn.innerHTML = '<i class="fas fa-check me-2"></i>Stopped';
                        stopBtn.className = stopBtn.className.replace('btn-danger', 'btn-success');
                        
                        // Auto-reset button appearance after 3 seconds
                        setTimeout(() => {
                            stopBtn.innerHTML = '<i class="fas fa-stop me-2"></i>Stop Bot';
                            stopBtn.className = stopBtn.className.replace('btn-success', 'btn-danger');
                        }, 3000);
                    }
                } else {
                    // Complete loading notification with error
                    window.completeLoading(loadingToastId, `❌ Failed to stop bot: ${data.message}`, 'error');
                    
                    // Use enhanced error handling
                    window.handleError(data.message || 'Bot stop failed', 'bot_control');
                }
                
                // Update status immediately
                if (window.BotStatus) {
                    window.BotStatus.checkStatus();
                }
            })
            .catch(error => {
                console.error('Error stopping bot:', error);
                
                // Complete loading with error and use enhanced error handling
                window.completeLoading(loadingToastId, 'Failed to stop bot', 'error');
                window.handleError(error, 'bot_control');
                
                // Restore button states based on last known status
                if (startBtn) startBtn.disabled = false;
                if (stopBtn) {
                    stopBtn.disabled = false;
                    stopBtn.innerHTML = '<i class="fas fa-stop me-2"></i>Stop Bot';
                }
            });
        }
    },
    
    // Load channels 
    loadChannels: function() {
        console.log('Loading channels...');
        
        // If ChannelManager exists, use it instead
        if (window.ChannelManager && typeof window.ChannelManager.loadChannels === 'function') {
            window.ChannelManager.loadChannels(channels => {
                this.populateChannelTable(channels);
            });
            return;
        }
        
        // Fallback to direct implementation if ChannelManager is not available
        
        // Clear existing channel list
        const channelList = document.getElementById('channelsTableBody');
        if (!channelList) {
            console.warn('Channel list element not found - looking for alternative elements');
            // Try to find the alternative table body
            const alternativeList = document.getElementById('channelList');
            if (!alternativeList) {
                console.error('No channel list element found on page');
                return;
            }
        }
        
        // Show loading indicator
        if (channelList) {
            channelList.innerHTML = '<tr><td colspan="6" class="text-center"><div class="spinner-border text-primary" role="status"></div><p>Loading channels...</p></td></tr>';
        }
        
        // Use the correct endpoint
        console.log('Fetching channel data from /api/channels endpoint');
        fetch('/api/channels')
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Server returned ${response.status}: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                console.log('Channel data received:', data);
                this.populateChannelTable(data);
            })
            .catch(error => {
                console.error('Error loading channels:', error);
                
                if (channelList) {
                    channelList.innerHTML = `
                        <tr><td colspan="6" class="text-center text-danger">
                            <i class="fas fa-exclamation-triangle me-2"></i>
                            Error loading channels: ${error.message}
                            <button class="btn btn-sm btn-primary ms-3" onclick="window.BotController.loadChannels()">
                                <i class="fas fa-redo me-1"></i>Retry
                            </button>
                        </td></tr>
                    `;
                }
            });
    },
    
    // Populate channel table with data
    populateChannelTable: function(data) {
        const channelList = document.getElementById('channelsTableBody');
        
        if (!channelList) {
            console.warn('Channel list element not found');
            return;
        }
        
        if (!data || data.length === 0) {
            channelList.innerHTML = '<tr><td colspan="6" class="text-center">No channels configured</td></tr>';
            return;
        }
        
        // Clear existing content
        channelList.innerHTML = '';
        
        // Process each channel for table view
        data.forEach(channel => {
            // Normalize channel data format
            const normalizedChannel = this.normalizeChannelData(channel);
            
            const row = document.createElement('tr');
            
            // Create cells with correct property names from the API
            const nameCell = document.createElement('td');
            nameCell.innerHTML = `<a href="https://twitch.tv/${normalizedChannel.name}" target="_blank">${normalizedChannel.name}</a>`;
            
            // Enabled Status (Join Channel) - Clickable
            const statusCell = document.createElement('td');
            const statusPill = document.createElement('span');
            statusPill.className = `status-pill ${normalizedChannel.configured_to_join ? 'bg-success' : 'bg-danger'}`;
            statusPill.style.cursor = 'pointer';
            statusPill.innerHTML = normalizedChannel.configured_to_join ? 
                '<i class="fas fa-check-circle"></i> Enabled' : 
                '<i class="fas fa-times-circle"></i> Disabled';
            statusPill.title = `Click to ${normalizedChannel.configured_to_join ? 'disable' : 'enable'}`;
            statusPill.setAttribute('data-channel', normalizedChannel.name);
            statusPill.setAttribute('data-type', 'join');
            statusPill.addEventListener('click', function() {
                window.BotController.toggleChannelStatus(this.getAttribute('data-channel'), this.getAttribute('data-type'));
            });
            statusCell.appendChild(statusPill);
            
            // TTS Status - Clickable
            const ttsCell = document.createElement('td');
            const ttsPill = document.createElement('span');
            ttsPill.className = `status-pill ${normalizedChannel.tts_enabled ? 'bg-info' : 'bg-secondary'}`;
            ttsPill.style.cursor = 'pointer';
            ttsPill.innerHTML = normalizedChannel.tts_enabled ? 
                '<i class="fas fa-volume-up"></i> Enabled' : 
                '<i class="fas fa-volume-mute"></i> Disabled';
            ttsPill.title = `Click to ${normalizedChannel.tts_enabled ? 'disable' : 'enable'} TTS`;
            ttsPill.setAttribute('data-channel', normalizedChannel.name);
            ttsPill.setAttribute('data-type', 'tts');
            ttsPill.addEventListener('click', function() {
                window.BotController.toggleChannelStatus(this.getAttribute('data-channel'), this.getAttribute('data-type'));
            });
            ttsCell.appendChild(ttsPill);
            
            // Messages Sent
            const messagesCell = document.createElement('td');
            messagesCell.textContent = normalizedChannel.messages_sent || '0';
            
            // Last Activity
            const activityCell = document.createElement('td');
            if (normalizedChannel.last_activity) {
                try {
                    if (window.timezoneUtils) {
                        activityCell.textContent = window.timezoneUtils.toLocalTime(normalizedChannel.last_activity);
                        activityCell.title = normalizedChannel.last_activity;
                    } else {
                        const activityDate = new Date(normalizedChannel.last_activity);
                        activityCell.textContent = activityDate.toLocaleString();
                        activityCell.title = activityDate.toISOString();
                    }
                } catch (e) {
                    activityCell.textContent = normalizedChannel.last_activity; // Fallback
                }
            } else {
                activityCell.textContent = 'Never';
            }
            
            // Add action buttons
            const actionsCell = document.createElement('td');
            const joinBtnHtml = `
                <button class="btn btn-sm btn-outline-success me-1 btn-action" onclick="window.BotController.joinChannel('${normalizedChannel.name}')" title="Join Channel">
                    <i class="fas fa-sign-in-alt"></i>
                </button>`;
            const leaveBtnHtml = `
                <button class="btn btn-sm btn-outline-danger me-1 btn-action" onclick="window.BotController.leaveChannel('${normalizedChannel.name}')" title="Leave Channel">
                    <i class="fas fa-sign-out-alt"></i>
                </button>`;
            const settingsBtnHtml = `
                <button class="btn btn-sm btn-outline-secondary btn-action" onclick="window.location.href='/settings#channel-${normalizedChannel.name}'" title="Channel Settings">
                    <i class="fas fa-cog"></i>
                </button>`;
            
            actionsCell.innerHTML = joinBtnHtml + leaveBtnHtml + settingsBtnHtml;
            
            // Add all cells to row
            row.appendChild(nameCell);
            row.appendChild(statusCell);
            row.appendChild(ttsCell);
            row.appendChild(messagesCell);
            row.appendChild(activityCell);
            row.appendChild(actionsCell);
            
            // Add row to table
            channelList.appendChild(row);
        });
    },
    
    // Normalize channel data from different sources to a consistent format
    normalizeChannelData: function(channel) {
        // If ChannelManager exists, use its normalization method
        if (window.ChannelManager && typeof window.ChannelManager.normalizeChannelData === 'function') {
            return window.ChannelManager.normalizeChannelData([channel])[0];
        }
        
        // Otherwise do basic normalization
        let normalizedChannel = {
            name: null,
            connected: false, // Bot's connection to Twitch channel
            configured_to_join: false, // From DB: join_channel
            tts_enabled: false,    // From DB: tts_enabled
            messages_sent: 0,  // From log file line count
            last_activity: null // From messages table MAX(timestamp)
        };
        
        if (Array.isArray(channel)) { // Older format, less likely now
            normalizedChannel.name = channel[0];
            if (channel.length > 1) normalizedChannel.tts_enabled = !!channel[1];
            if (channel.length > 3) normalizedChannel.connected = !!channel[3]; // This was likely 'currently_connected'
            // 'configured_to_join' would need to be passed or assumed
        } else if (typeof channel === 'object' && channel !== null) {
            normalizedChannel.name = channel.name || channel.channel_name || '';
            normalizedChannel.connected = !!channel.currently_connected; // Heartbeat status
            normalizedChannel.configured_to_join = !!channel.configured_to_join; // DB join_channel flag
            normalizedChannel.tts_enabled = !!channel.tts_enabled;    // DB tts_enabled flag
            normalizedChannel.messages_sent = channel.messages_sent || 0;
            normalizedChannel.last_activity = channel.last_activity || null;
        } else if (typeof channel === 'string') { // Simplest case, just name
            normalizedChannel.name = channel;
        }
        
        return normalizedChannel;
    },

    toggleChannelStatus: function(channelName, type) {
        const endpoint = type === 'join' ? `/api/channel/${channelName}/toggle-join` : `/api/channel/${channelName}/toggle-tts`;
        const actionText = type === 'join' ? 'join status' : 'TTS status';

        window.notificationSystem.showToast(`Toggling ${actionText} for ${channelName}...`, 'info');

        fetch(endpoint, { method: 'POST' })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    window.notificationSystem.showToast(data.message, 'success');
                    this.loadChannels(); // Refresh table
                } else {
                    window.notificationSystem.showToast(`Failed to toggle ${actionText}: ${data.message || 'Unknown error'}`, 'error');
                }
            })
            .catch(error => {
                console.error(`Error toggling ${actionText} for ${channelName}:`, error);
                window.notificationSystem.showToast(`Error toggling ${actionText}: ${error.message}`, 'error');
            });
    },

    joinChannel: function(channelName) {
        window.notificationSystem.showToast(`Attempting to join channel: ${channelName}...`, 'info');
        fetch(`/join-channel/${channelName}`, { method: 'POST' })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    window.notificationSystem.showToast(`Successfully sent join command for ${channelName}.`, 'success');
                } else {
                    window.notificationSystem.showToast(`Failed to join ${channelName}: ${data.message || 'Unknown error'}`, 'error');
                }
                this.loadChannels(); // Refresh list
            })
            .catch(error => {
                window.notificationSystem.showToast(`Error joining ${channelName}: ${error.message}`, 'error');
                this.loadChannels(); // Refresh list
            });
    },

    leaveChannel: function(channelName) {
        window.notificationSystem.showToast(`Attempting to leave channel: ${channelName}...`, 'info');
        fetch(`/leave-channel/${channelName}`, { method: 'POST' })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    window.notificationSystem.showToast(`Successfully sent leave command for ${channelName}.`, 'success');
                } else {
                    window.notificationSystem.showToast(`Failed to leave ${channelName}: ${data.message || 'Unknown error'}`, 'error');
                }
                this.loadChannels(); // Refresh list
            })
            .catch(error => {
                window.notificationSystem.showToast(`Error leaving ${channelName}: ${error.message}`, 'error');
                this.loadChannels(); // Refresh list
            });
    },

    loadSystemLogs: function() {
        const logContainer = document.getElementById('logContainer');
        if (!logContainer) return;

        logContainer.innerHTML = '<div class="text-muted p-2">Loading system logs...</div>';
        fetch('/api/system-logs')
            .then(response => response.json())
            .then(data => {
                if (data.logs && data.logs.length > 0) {
                    logContainer.innerHTML = data.logs.map(line => `<div>${this.escapeHtml(line)}</div>`).join('');
                } else {
                    logContainer.innerHTML = '<div class="text-muted p-2">No system logs to display.</div>';
                }
                logContainer.scrollTop = logContainer.scrollHeight; // Scroll to bottom
            })
            .catch(error => {
                console.error('Error loading system logs:', error);
                logContainer.innerHTML = `<div class="text-danger p-2">Error loading system logs: ${error.message}</div>`;
            });
    },

    escapeHtml: function(text) {
        // Basic HTML escaping
        if (typeof text !== 'string') return text;
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    },
    
    // Reset button states to their default enabled/disabled states
    resetButtonStates: function() {
        const startBtn = document.getElementById('startBotBtn');
        const stopBtn = document.getElementById('stopBotBtn');
        
        if (startBtn) {
            startBtn.disabled = false;
            startBtn.innerHTML = '<i class="fas fa-play me-2"></i>Start Bot';
            startBtn.className = startBtn.className.replace('btn-success', 'btn-primary');
        }
        
        if (stopBtn) {
            stopBtn.disabled = true;
            stopBtn.innerHTML = '<i class="fas fa-stop me-2"></i>Stop Bot';
            stopBtn.className = stopBtn.className.replace('btn-success', 'btn-danger');
        }
    }
};

// Initialize the bot controller when the DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    window.BotController.init();
    window.BotController.loadSystemLogs(); // Load logs on page load

    const clearLogsBtn = document.getElementById('clearLogsBtn');
    if (clearLogsBtn) {
        clearLogsBtn.addEventListener('click', function() {
            const logContainer = document.getElementById('logContainer');
            if (logContainer) {
                logContainer.innerHTML = '<div class="text-muted p-2">Logs cleared from display.</div>';
            }
            window.notificationSystem.showToast('System logs display cleared.', 'info');
        });
    }
});

// Legacy compatibility layer - provide functions that older code might use
// These functions now call into the BotController to maintain backward compatibility

// Legacy function for checking bot status
function checkBotStatus() {
    if (window.BotStatus) {
        window.BotStatus.checkStatus();
    }
}

// Legacy function for starting the bot
function startBot() {
    window.BotController.startBot();
}

// Legacy function for stopping the bot
function stopBot() {
    window.BotController.stopBot();
}

// Legacy function for checking for existing bot instances
function checkForExistingBotInstance() {
    window.BotController.checkForExistingBotInstance();
}

// Legacy function for loading channels
function loadChannels() {
    window.BotController.loadChannels();
}

// Legacy function for joining a channel (global scope for onclick)
function joinChannel(channelName) {
    window.BotController.joinChannel(channelName);
}

// Legacy function for leaving a channel (global scope for onclick)
function leaveChannel(channelName) {
    window.BotController.leaveChannel(channelName);
}


// Legacy global safeShowToast is removed as BotController.showNotification is removed.
// The primary window.showToast (from notification.js) should be used.
