// Main page functionality
document.addEventListener('DOMContentLoaded', function() {
    // Initialize functions
    // checkBotStatus(); // Removed: Handled by bot_status.js
    if (window.BotStatus) { // Ensure BotStatus is available
        window.BotStatus.checkStatus(); // Initial explicit check if needed, or rely on its internal init
    }
    loadChannels();
    
    try {
        loadTTSStats();
    } catch (e) {
        console.error('Error initializing TTS stats:', e);
    }
    
    try {
        loadSystemInfo();
    } catch (e) {
        console.error('Error initializing system info:', e);
    }
    
    // Set up refresh intervals, but NOT on settings page
    if (!window.location.pathname.includes('settings')) {
        console.log("Setting up main page refresh intervals");
        // setInterval(checkBotStatus, 30000); // Removed: Handled by bot_status.js
        // setInterval(loadRecentTTS, 60000);  // Removed: Recent TTS table is being removed
    } else {
        console.log("On settings page - skipping refresh intervals");
    }
    
    // Set up event listeners - with null check
    // const refreshRecentBtn = document.getElementById('refreshRecentBtn'); // Removed: Recent TTS table is being removed
    // if (refreshRecentBtn) {
    //     refreshRecentBtn.addEventListener('click', function() {
    //         this.disabled = true;
    //         loadRecentTTS();
    //         setTimeout(() => { this.disabled = false; }, 2000);
    //     });
    // }
});

// Enhanced bot status check function - REMOVED as bot_status.js handles this.
/*
function checkBotStatus() {
    const statusSpinner = document.getElementById('statusSpinner');
    const statusText = document.getElementById('botStatusText');
    const lastUpdated = document.getElementById('statusLastUpdated');
    const uptimeDisplay = document.getElementById('botUptime');
    
    // Look for buttons in both bot_control and main dashboard
    const startBotBtn = document.getElementById('startBotBtn') || document.getElementById('quickStartBtn');
    const stopBotBtn = document.getElementById('stopBotBtn') || document.getElementById('quickStopBtn');
    
    if (statusSpinner) statusSpinner.style.display = 'inline-block';
    
    fetch('/api/bot-status')
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server responded with ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (statusSpinner) statusSpinner.style.display = 'none';
            
            // Process is running but might not be connected
            if (data.running) {
                if (data.connected) {
                    if (statusText) {
                        statusText.textContent = 'Running & Connected';
                        statusText.className = 'mb-0 text-success';
                    }
                } else {
                    if (statusText) {
                        statusText.textContent = 'Running (Not Connected)';
                        statusText.className = 'mb-0 text-warning';
                    }
                }
                
                // Update bot control buttons if they exist
                if (startBotBtn) startBotBtn.disabled = true;
                if (stopBotBtn) stopBotBtn.disabled = false;
                
                // Show uptime if available
                if (uptimeDisplay && data.uptime) {
                    uptimeDisplay.textContent = data.uptime;
                }
            } else {
                if (statusText) {
                    statusText.textContent = 'Stopped';
                    statusText.className = 'mb-0 text-danger';
                }
                
                // Update bot control buttons if they exist
                if (startBotBtn) startBotBtn.disabled = false;
                if (stopBotBtn) stopBotBtn.disabled = true;
                
                if (uptimeDisplay) {
                    uptimeDisplay.textContent = 'Not running';
                }
            }
            
            // Show when status was last checked
            if (lastUpdated) {
                lastUpdated.textContent = 'Last updated: ' + new Date().toLocaleTimeString();
            }
            
            // If there's an error message, display it
            if (data.error) {
                console.warn('Bot status check warning:', data.error);
                if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                    window.notificationSystem.showToast('Bot status check warning: ' + data.error, 'warning');
                } else {
                    alert('Bot status check warning: ' + data.error);
                }
            }
        })
        .catch(error => {
            console.error('Error checking bot status:', error);
            if (statusSpinner) statusSpinner.style.display = 'none';
            
            if (statusText) {
                statusText.textContent = 'Unknown';
                statusText.className = 'mb-0 text-warning';
            }
            
            // Don't show toast for common errors
            if (error.message !== "Cannot read properties of null (reading 'disabled')") {
                if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                    window.notificationSystem.showToast('Failed to check bot status: ' + error.message, 'error');
                } else {
                    alert('Failed to check bot status: ' + error.message);
                }
            }
        });
}
*/

// Enhanced channel display function
function loadChannels() {
    const channelsTableBody = document.getElementById('channelsTableBody');
    const channelsList = document.getElementById('channelsList'); // Backward compatibility
    const spinner = document.getElementById('channelLoadingSpinner');
    const channelCountElement = document.getElementById('channelCount');
    
    // Setup refresh channels button if it exists
    const refreshChannelsBtn = document.getElementById('refreshChannelsBtn');
    if (refreshChannelsBtn) {
        refreshChannelsBtn.addEventListener('click', function() {
            this.disabled = true;
            loadChannels();
            setTimeout(() => { this.disabled = false; }, 1000);
        });
    }
    
    if (!channelsTableBody && !channelsList && !channelCountElement) return;
    
    if (spinner) spinner.style.display = 'block';
    
    fetch('/api/channels')
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server responded with ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (spinner) spinner.style.display = 'none';
            
            // Log the raw data received for channels
            console.log("Data received for /api/channels:", JSON.parse(JSON.stringify(data)));

            // Count connected channels - update even if the list doesn't exist
            const connectedCount = data.filter(channel => channel.currently_connected).length;
            if (channelCountElement) {
                channelCountElement.textContent = connectedCount.toString();
            }
            
            // Sort channels: connected first, then alphabetically
            data.sort((a, b) => {
                if (a.currently_connected !== b.currently_connected) {
                    // True (connected) should come before false (not connected)
                    return (b.currently_connected ? 1 : 0) - (a.currently_connected ? 1 : 0);
                }
                // Ensure names exist before comparing
                const nameA = a.name || '';
                const nameB = b.name || '';
                return nameA.localeCompare(nameB);
            });
            
            // Update channels table if it exists
            if (channelsTableBody) {
                if (data.length === 0) {
                    channelsTableBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No channels configured</td></tr>';
                    return;
                }
                
                channelsTableBody.innerHTML = '';
                
                data.forEach(channel => {
                    const row = document.createElement('tr');
                    
                    // Channel name column
                    const nameCell = document.createElement('td');
                    nameCell.innerHTML = `
                        <div class="d-flex align-items-center">
                            <span class="status-indicator status-${channel.currently_connected ? 'online' : 'offline'} me-2"></span>
                            <strong>#${channel.name}</strong>
                        </div>
                    `;
                    
                    // Status column
                    const statusCell = document.createElement('td');
                    statusCell.className = 'text-center';
                    statusCell.innerHTML = `
                        <span class="badge bg-${channel.currently_connected ? 'success' : 'secondary'}">
                            ${channel.currently_connected ? 'Connected' : 'Offline'}
                        </span>
                    `;
                    
                    // TTS column
                    const ttsCell = document.createElement('td');
                    ttsCell.className = 'text-center';
                    if (channel.tts_enabled) {
                        ttsCell.innerHTML = '<i class="fas fa-volume-up text-info" title="TTS Enabled"></i>';
                    } else {
                        ttsCell.innerHTML = '<i class="fas fa-volume-mute text-muted" title="TTS Disabled"></i>';
                    }
                    
                    // Actions column
                    const actionsCell = document.createElement('td');
                    actionsCell.className = 'text-center';
                    actionsCell.innerHTML = `
                        <a href="/channel/${channel.name}" class="btn btn-sm btn-outline-primary" title="View Channel">
                            <i class="fas fa-eye"></i>
                        </a>
                    `;
                    
                    row.appendChild(nameCell);
                    row.appendChild(statusCell);
                    row.appendChild(ttsCell);
                    row.appendChild(actionsCell);
                    
                    channelsTableBody.appendChild(row);
                });
            }
            
            // Update legacy channels list if it exists (backward compatibility)
            else if (channelsList) {
                if (data.length === 0) {
                    channelsList.innerHTML = '<div class="text-center text-muted">No channels configured</div>';
                    return;
                }
                
                channelsList.innerHTML = '';
                
                data.forEach(channel => {
                    const item = document.createElement('div');
                    item.className = 'list-group-item d-flex justify-content-between align-items-center';
                    
                    const statusIndicator = document.createElement('span');
                    statusIndicator.className = `status-indicator status-${channel.currently_connected ? 'online' : 'offline'}`;
                    
                    const channelName = document.createElement('span');
                    channelName.textContent = channel.name;
                    
                    const statusContainer = document.createElement('div');
                    statusContainer.appendChild(statusIndicator);
                    statusContainer.appendChild(channelName);
                    
                    const badges = document.createElement('div');
                    
                    if (channel.tts_enabled) {
                        const ttsBadge = document.createElement('span');
                        ttsBadge.className = 'badge bg-info rounded-pill ms-1';
                        ttsBadge.innerHTML = '<i class="fas fa-volume-up" title="TTS Enabled"></i>';
                        badges.appendChild(ttsBadge);
                    }
                    
                    if (!channel.configured_to_join) {
                        const notJoinBadge = document.createElement('span');
                        notJoinBadge.className = 'badge bg-warning rounded-pill ms-1';
                        notJoinBadge.innerHTML = '<i class="fas fa-exclamation-triangle" title="Not configured to join"></i>';
                        badges.appendChild(notJoinBadge);
                    }
                    
                    item.appendChild(statusContainer);
                    item.appendChild(badges);
                    channelsList.appendChild(item);
                });
            }
        })
        .catch(error => {
            console.error('Error loading channels:', error);
            if (spinner) spinner.style.display = 'none';
            
            if (channelsTableBody) {
                channelsTableBody.innerHTML = '<tr><td colspan="4" class="text-center text-danger">Error loading channels</td></tr>';
            } else if (channelsList) {
                channelsList.innerHTML = '<div class="text-center text-danger">Error loading channels</div>';
            }
        });
}

// Updated loadTTSStats with better error handling
function loadTTSStats() {
    // Check if elements exist first
    const ttsTodayCount = document.getElementById('ttsTodayCount');
    const ttsWeekCount = document.getElementById('ttsWeekCount');
    const ttsTotalCount = document.getElementById('ttsTotalCount');
    
    if (!ttsTodayCount || !ttsWeekCount || !ttsTotalCount) {
        console.log('TTS stats elements not found, skipping update');
        return;
    }
    
    fetch('/api/tts-stats')
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server responded with ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            ttsTodayCount.textContent = data.today || '0';
            ttsWeekCount.textContent = data.week || '0';
            ttsTotalCount.textContent = data.total || '0';
        })
        .catch(error => {
            console.error('Error loading TTS stats:', error);
            // Don't update the UI on error
        });
}

// Load recent TTS messages - REMOVED as the table is being removed
/*
function loadRecentTTS() {
    const ttsTableBody = document.getElementById('recentTTSBody');
    const ttsTable = document.getElementById('recentTTSTable');
    const noTTSDataMessage = document.getElementById('noTTSData');
    const spinner = document.getElementById('ttsLoadingSpinner'); // Spinner for the whole TTS panel

    if (!ttsTableBody || !ttsTable || !noTTSDataMessage) {
        console.warn('Recent TTS table elements not found, skipping update.');
        return;
    }

    if (spinner) spinner.style.display = 'block';
    if (ttsTable) ttsTable.style.display = 'none';
    if (noTTSDataMessage) noTTSDataMessage.style.display = 'none';

    fetch('/api/recent-tts')
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server responded with ${response.status}`);
            }
            return response.json();
        })
        .then(data => { // 'data' here is the array of TTS log objects
            console.log('[loadRecentTTS] Data received from /api/recent-tts:', JSON.parse(JSON.stringify(data))); // Log the received data
            console.log(`[loadRecentTTS] Number of items received: ${data ? data.length : 0}`);

            if (spinner) spinner.style.display = 'none';
            ttsTableBody.innerHTML = ''; // Clear previous entries

            if (!data || data.error || data.length === 0) { // Check if data is null, has an error, or is empty
                if (noTTSDataMessage) noTTSDataMessage.style.display = 'block';
                if (ttsTable) ttsTable.style.display = 'none';
                if (data.error) console.error('Error in TTS data:', data.error);
                return;
            }
            
            if (ttsTable) ttsTable.style.display = 'table'; // Or 'block' if it's not a table element
            if (noTTSDataMessage) noTTSDataMessage.style.display = 'none';

            data.forEach((item, index) => {
                console.log(`[loadRecentTTS] Processing item ${index + 1}/${data.length}:`, JSON.parse(JSON.stringify(item)));
                const row = ttsTableBody.insertRow();

                const cellChannel = row.insertCell();
                cellChannel.className = 'text-center';
                cellChannel.textContent = item.channel;

                const cellMessage = row.insertCell();
                cellMessage.textContent = item.message.length > 70 ? 
                    item.message.substring(0, 70) + '...' : 
                    item.message;
                cellMessage.title = item.message;


                const cellTime = row.insertCell();
                try {
                    cellTime.textContent = new Date(item.timestamp).toLocaleString();
                } catch (e) {
                    cellTime.textContent = item.timestamp; // Fallback if date parsing fails
                    console.warn("Could not parse timestamp:", item.timestamp, e);
                }


                const cellVoice = row.insertCell();
                cellVoice.textContent = item.voice_preset || 'N/A';

                const cellActions = row.insertCell();
                cellActions.className = 'text-center';

                const playBtn = document.createElement('button');
                playBtn.className = 'btn btn-sm btn-primary me-1 action-btn';
                playBtn.innerHTML = '<i class="fas fa-play"></i>';
                playBtn.title = 'Play TTS';
                playBtn.onclick = () => {
                    const audioSrc = `/static/${item.file_path}`;
                    if (typeof window.playAudioIfExists === 'function') {
                        window.playAudioIfExists(audioSrc);
                    } else {
                        console.warn('playAudioIfExists function is not defined on window.');
                        alert('Playback functionality is not available.');
                    }
                };
                
                const downloadBtn = document.createElement('a');
                downloadBtn.className = 'btn btn-sm btn-secondary action-btn';
                downloadBtn.innerHTML = '<i class="fas fa-download"></i>';
                downloadBtn.title = 'Download TTS';
                // Use item.file_path which should be like 'outputs/channel/filename.wav'
                // The link should be relative to the domain, pointing to the static serve path
                downloadBtn.href = `/static/${item.file_path}`; 
                downloadBtn.download = item.file_path.split('/').pop(); // Suggests filename for download

                cellActions.appendChild(playBtn);
                cellActions.appendChild(downloadBtn);
            });
        })
        .catch(error => {
            console.error('Error loading recent TTS:', error);
            if (spinner) spinner.style.display = 'none';
            if (noTTSDataMessage) {
                noTTSDataMessage.style.display = 'block';
                noTTSDataMessage.innerHTML = '<div class="empty-state-content"><i class="fas fa-exclamation-circle empty-state-icon text-danger"></i><h4>Error Loading TTS</h4><p class="text-muted">Could not fetch recent TTS messages.</p></div>';
            }
            if (ttsTable) ttsTable.style.display = 'none';
        });
}
*/

// Updated loadSystemInfo with better error handling
function loadSystemInfo() {
    // Elements on index.html for Bot Analytics
    const totalMessagesElement = document.getElementById('totalMessages'); // Total lines processed by Markov
    const totalTTSElement = document.getElementById('totalTTS');       // Total TTS clips generated
    const totalResponsesElement = document.getElementById('totalResponses'); // Bot messages sent
    // const activeSinceElement = document.getElementById('activeSince'); // Bot uptime (handled by bot_status.js)

    // Elements for System Metrics (mostly placeholders currently) - These are being removed from UI
    // const cpuUsageElement = document.getElementById('cpuUsage');
    // const memoryUsageElement = document.getElementById('memoryUsage');
    // const diskUsageElement = document.getElementById('diskUsage');
    // const serverUptimeElement = document.getElementById('uptime');


    // Fetch data for "Total Messages" (from Markov line counts)
    if (totalMessagesElement) {
        fetch('/get-stats') // This endpoint returns model stats including line_count
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Error fetching /get-stats: ${response.status}`);
                }
                return response.json();
            })
            .then(statsData => {
                if (Array.isArray(statsData)) {
                    let totalLines = 0;
                    statsData.forEach(channel => {
                        if (channel.line_count) {
                            totalLines += parseInt(channel.line_count, 10);
                        }
                    });
                    totalMessagesElement.textContent = totalLines.toLocaleString();
                } else {
                    totalMessagesElement.textContent = 'N/A';
                    console.warn("/get-stats did not return an array for totalMessages:", statsData);
                }
            })
            .catch(error => {
                console.error('Error loading model stats for #totalMessages:', error);
                totalMessagesElement.textContent = 'Error';
            });
    }

    // Fetch data for "Total TTS Generated" (from tts_logs count)
    if (totalTTSElement) {
        fetch('/api/tts-stats') // This endpoint returns { today, week, total }
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Error fetching /api/tts-stats: ${response.status}`);
                }
                return response.json();
            })
            .then(ttsStatsData => {
                if (ttsStatsData && typeof ttsStatsData.total !== 'undefined') {
                    totalTTSElement.textContent = ttsStatsData.total.toLocaleString();
                } else {
                    totalTTSElement.textContent = 'N/A';
                    console.warn("/api/tts-stats did not return expected data for #totalTTS:", ttsStatsData);
                }
            })
            .catch(error => {
                console.error('Error loading TTS stats for #totalTTS:', error);
                totalTTSElement.textContent = 'Error';
            });
    }

    // Fetch data for "Total Bot Responses"
    if (totalResponsesElement) {
        fetch('/api/bot-response-stats')
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Error fetching /api/bot-response-stats: ${response.status}`);
                }
                return response.json();
            })
            .then(botResponseData => {
                if (botResponseData && typeof botResponseData.total_responses !== 'undefined') {
                    totalResponsesElement.textContent = botResponseData.total_responses.toLocaleString();
                } else {
                    totalResponsesElement.textContent = 'N/A';
                    console.warn("/api/bot-response-stats did not return expected data for #totalResponses:", botResponseData);
                }
            })
            .catch(error => {
                console.error('Error loading bot response stats for #totalResponses:', error);
                totalResponsesElement.textContent = 'Error';
            });
    }

    // Note: #activeSince (Bot Uptime) is handled by static/scripts/bot_status.js
    // Other metrics like CPU, Memory, Disk, Server Uptime would need their own backend data sources if re-added.
}

// Add this function to handle reconnect requests
function reconnectBot() {
    const reconnectBtn = document.getElementById('reconnectBtn');
    
    if (reconnectBtn) {
        reconnectBtn.disabled = true;
        reconnectBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Reconnecting...';
    }
    
    if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
        window.notificationSystem.showToast('Attempting to reconnect bot...', 'info');
    } else {
        alert('Attempting to reconnect bot...');
    }
    
    fetch('/api/reconnect-bot', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                window.notificationSystem.showToast('Reconnect command sent', 'success');
            } else {
                alert('Reconnect command sent');
            }
        } else {
            if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                window.notificationSystem.showToast(data.message || 'Failed to reconnect', 'error');
            } else {
                alert(data.message || 'Failed to reconnect');
            }
        }
    })
    .catch(error => {
        console.error('Error reconnecting bot:', error);
        if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
            window.notificationSystem.showToast('Error requesting reconnect', 'error');
        } else {
            alert('Error requesting reconnect');
        }
    })
    .finally(() => {
        if (reconnectBtn) {
            reconnectBtn.disabled = false;
            reconnectBtn.innerHTML = '<i class="fas fa-plug me-1"></i>Force Reconnect';
            
            // Check status again after a delay
            setTimeout(() => {
                checkBotStatus();
                loadChannels();
            }, 3000);
        }
    });
}

// Bot control function - Start bot
function startBot() {
    // Get button elements with null check - support both bot control page and dashboard
    const startBotBtn = document.getElementById('startBotBtn') || document.getElementById('quickStartBtn');
    const stopBotBtn = document.getElementById('stopBotBtn') || document.getElementById('quickStopBtn');
    
    // Disable buttons if they exist
    if (startBotBtn) startBotBtn.disabled = true;
    if (stopBotBtn) stopBotBtn.disabled = true;
    
    if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
        window.notificationSystem.showToast('Starting bot...', 'info');
    } else {
        alert('Starting bot...');
    }
    
    // Use the correct API endpoint from the existing application
    fetch('/start_bot', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                window.notificationSystem.showToast('Bot started successfully', 'success');
            } else {
                alert('Bot started successfully');
            }
        } else {
            if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                window.notificationSystem.showToast(`Failed to start bot: ${data.message || 'Unknown error'}`, 'error');
            } else {
                alert(`Failed to start bot: ${data.message || 'Unknown error'}`);
            }
        }
        
        checkBotStatus();
    })
    .catch(error => {
        console.error('Error starting bot:', error);
        if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
            window.notificationSystem.showToast('Error starting bot', 'error');
        } else {
            alert('Error starting bot');
        }
        
        // Re-check status which will properly handle button states
        checkBotStatus();
    });
}

// Bot control function - Stop bot
function stopBot() {
    // Get button elements with null check - support both bot control page and dashboard
    const startBotBtn = document.getElementById('startBotBtn') || document.getElementById('quickStartBtn');
    const stopBotBtn = document.getElementById('stopBotBtn') || document.getElementById('quickStopBtn');
    
    // Disable buttons if they exist
    if (startBotBtn) startBotBtn.disabled = true;
    if (stopBotBtn) stopBotBtn.disabled = true;
    
    if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
        window.notificationSystem.showToast('Stopping bot...', 'info');
    } else {
        alert('Stopping bot...');
    }
    
    // Use the correct API endpoint from the existing application
    fetch('/stop_bot', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                window.notificationSystem.showToast('Bot stopped successfully', 'success');
            } else {
                alert('Bot stopped successfully');
            }
        } else {
            if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
                window.notificationSystem.showToast(`Failed to stop bot: ${data.message || 'Unknown error'}`, 'error');
            } else {
                alert(`Failed to stop bot: ${data.message || 'Unknown error'}`);
            }
        }
        
        checkBotStatus();
    })
    .catch(error => {
        console.error('Error stopping bot:', error);
        if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
            window.notificationSystem.showToast('Error stopping bot', 'error');
        } else {
            alert('Error stopping bot');
        }
        
        // Re-check status which will properly handle button states
        checkBotStatus();
    });
}

// Channels dropdown functionality
function loadChannelsDropdown() {
    const dropdown = document.getElementById('channelsDropdownMenu');
    if (!dropdown) return;
    
    // Show loading state
    dropdown.innerHTML = '<li><a class="dropdown-item" href="#"><i class="fas fa-spinner fa-spin me-2"></i>Loading channels...</a></li>';
    
    fetch('/api/channels')
        .then(response => response.json())
        .then(data => {
            if (data && Array.isArray(data) && data.length > 0) {
                // Build dropdown items
                const items = data.map(channel => {
                    const channelName = channel.name || channel.channel_name;
                    const isConnected = channel.currently_connected;
                    const statusIcon = isConnected ? 
                        '<i class="fas fa-circle text-success me-2" style="font-size: 0.7rem;"></i>' : 
                        '<i class="fas fa-circle text-secondary me-2" style="font-size: 0.7rem;"></i>';
                    
                    return `
                        <li>
                            <a class="dropdown-item d-flex align-items-center" href="/channel/${channelName}">
                                ${statusIcon}
                                <span class="me-2">#${channelName}</span>
                                <small class="text-muted ms-auto">${isConnected ? 'Online' : 'Offline'}</small>
                            </a>
                        </li>
                    `;
                }).join('');
                
                dropdown.innerHTML = `
                    ${items}
                    <li><hr class="dropdown-divider"></li>
                    <li><a class="dropdown-item text-muted" href="/settings"><i class="fas fa-cog me-2"></i>Manage Channels</a></li>
                `;
            } else {
                dropdown.innerHTML = `
                    <li><a class="dropdown-item text-muted" href="#"><i class="fas fa-exclamation-circle me-2"></i>No channels configured</a></li>
                    <li><hr class="dropdown-divider"></li>
                    <li><a class="dropdown-item" href="/settings"><i class="fas fa-plus me-2"></i>Add Channel</a></li>
                `;
            }
        })
        .catch(error => {
            console.error('Error loading channels:', error);
            dropdown.innerHTML = `
                <li><a class="dropdown-item text-danger" href="#"><i class="fas fa-exclamation-triangle me-2"></i>Error loading channels</a></li>
                <li><hr class="dropdown-divider"></li>
                <li><a class="dropdown-item" href="#" onclick="loadChannelsDropdown()"><i class="fas fa-sync-alt me-2"></i>Retry</a></li>
            `;
        });
}

// Initialize channels dropdown when page loads
document.addEventListener('DOMContentLoaded', function() {
    // Load channels dropdown after a short delay to ensure other systems are ready
    setTimeout(() => {
        loadChannelsDropdown();
    }, 1000);
    
    // Refresh channels dropdown when it's opened
    const channelsDropdown = document.getElementById('channelsDropdown');
    if (channelsDropdown) {
        channelsDropdown.addEventListener('click', function() {
            // Refresh channels when dropdown is clicked
            setTimeout(() => {
                loadChannelsDropdown();
            }, 100);
        });
    }
});

// Expose function for manual refresh
window.loadChannelsDropdown = loadChannelsDropdown;
