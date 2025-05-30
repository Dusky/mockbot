document.addEventListener('DOMContentLoaded', function() {
    const ttsTableBody = document.getElementById('ttsHistoryTableBody');
    const ttsTableContainer = document.getElementById('ttsHistoryTableContainer');
    const noTTSDataMessage = document.getElementById('noTTSHistoryData');
    const spinner = document.getElementById('ttsHistoryLoadingSpinner');
    
    const refreshBtn = document.getElementById('refreshHistoryBtn');
    const prevPageBtn = document.getElementById('prevPageBtn');
    const nextPageBtn = document.getElementById('nextPageBtn');
    const currentPageSpan = document.getElementById('currentPage');
    const totalPagesSpan = document.getElementById('totalPages');
    const prevPageContainer = document.getElementById('prevPageContainer');
    const nextPageContainer = document.getElementById('nextPageContainer');

    // New UI elements for filtering and sorting
    const channelFilterSelect = document.getElementById('ttsChannelFilter');
    const messageFilterInput = document.getElementById('ttsMessageFilter');
    const sortBySelect = document.getElementById('ttsSortBy');
    const sortOrderBtn = document.getElementById('ttsSortOrderBtn');
    const sortOrderIcon = sortOrderBtn ? sortOrderBtn.querySelector('i') : null;

    let currentPage = 1;
    const perPage = 15; // Or make this configurable
    let currentSortOrder = 'desc'; // Default sort order

    function fetchChannelsForTTSFilter() {
        if (!channelFilterSelect) return;

        fetch('/api/channels') // Assuming this endpoint returns a list of channel objects or names
            .then(response => response.json())
            .then(data => {
                // Clear existing options except "All Channels"
                while (channelFilterSelect.options.length > 1) {
                    channelFilterSelect.remove(1);
                }
                if (data && Array.isArray(data)) {
                    data.forEach(channel => {
                        const option = document.createElement('option');
                        // Assuming channel objects have a 'name' property
                        option.value = channel.name || channel; 
                        option.textContent = channel.name || channel;
                        channelFilterSelect.appendChild(option);
                    });
                }
            })
            .catch(error => {
                console.error('Error fetching channels for TTS filter:', error);
                if (window.notificationSystem && window.notificationSystem.showToast) {
                    window.notificationSystem.showToast('Error loading channel filter for TTS.', 'error');
                }
            });
    }


    function loadTTSHistory(page = 1) {
        if (!ttsTableBody || !ttsTableContainer || !noTTSDataMessage || !spinner) {
            console.warn('TTS History table elements not found, skipping update.');
            return;
        }

        spinner.style.display = 'block';
        ttsTableContainer.style.display = 'none';
        noTTSDataMessage.style.display = 'none';

        const channelFilter = channelFilterSelect ? channelFilterSelect.value : '';
        const messageFilter = messageFilterInput ? messageFilterInput.value : '';
        const sortBy = sortBySelect ? sortBySelect.value : 'timestamp';
        // sortOrder is managed by currentSortOrder variable

        const params = new URLSearchParams({
            page: page,
            per_page: perPage,
            sort_by: sortBy,
            sort_order: currentSortOrder
        });

        if (channelFilter) {
            params.append('channel_filter', channelFilter);
        }
        if (messageFilter) {
            params.append('message_filter', messageFilter);
        }

        fetch(`/api/tts-logs?${params.toString()}`, { cache: 'no-store' })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Server responded with ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                console.log(`[TTS History] Received data for page ${page}:`, JSON.parse(JSON.stringify(data)));
                spinner.style.display = 'none';
                ttsTableBody.innerHTML = ''; // Clear previous entries

                if (data.error || !data.logs || data.logs.length === 0) {
                    console.log(`[TTS History] No logs found or error. Total items from API: ${data.total_items}, Total pages from API: ${data.total_pages}`);
                    noTTSDataMessage.style.display = 'block';
                    ttsTableContainer.style.display = 'none';
                    if (data.error) console.error('[TTS History] Error in TTS history data:', data.error);
                    updatePaginationControls(data.page || 0, data.total_pages || 0);
                    return;
                }
                
                ttsTableContainer.style.display = 'block';
                noTTSDataMessage.style.display = 'none';

                console.log(`[TTS History] Populating table with ${data.logs.length} items. API says Page: ${data.page}, Total Pages: ${data.total_pages}`);
                data.logs.forEach((item, index) => {
                    // console.log(`[TTS History] Processing item ${index + 1}/${data.logs.length}:`, JSON.parse(JSON.stringify(item))); // Uncomment for very detailed item logging
                    const row = ttsTableBody.insertRow();

                    const cellChannel = row.insertCell();
                    cellChannel.className = 'text-center';
                    cellChannel.textContent = item.channel;

                    const cellMessage = row.insertCell();
                    cellMessage.textContent = item.message.length > 100 ? 
                        item.message.substring(0, 100) + '...' : 
                        item.message;
                    cellMessage.title = item.message;

                    const cellTime = row.insertCell();
                    try {
                        if (window.timezoneUtils) {
                            cellTime.textContent = window.timezoneUtils.toLocalTime(item.timestamp);
                        } else {
                            cellTime.textContent = new Date(item.timestamp).toLocaleString();
                        }
                    } catch (e) {
                        cellTime.textContent = item.timestamp; 
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
                    downloadBtn.href = `/static/${item.file_path}`; 
                    downloadBtn.download = item.file_path.split('/').pop();

                    cellActions.appendChild(playBtn);
                    cellActions.appendChild(downloadBtn);
                });

                updatePaginationControls(data.page, data.total_pages);
                currentPage = data.page;
            })
            .catch(error => {
                console.error('Error loading TTS history:', error);
                spinner.style.display = 'none';
                noTTSDataMessage.style.display = 'block';
                noTTSDataMessage.innerHTML = '<div class="empty-state-content"><i class="fas fa-exclamation-circle empty-state-icon text-danger"></i><h4>Error Loading History</h4><p class="text-muted">Could not fetch TTS history.</p></div>';
                ttsTableContainer.style.display = 'none';
                updatePaginationControls(0, 0);
            });
    }

    function updatePaginationControls(page, totalPages) {
        if (currentPageSpan) currentPageSpan.textContent = page;
        if (totalPagesSpan) totalPagesSpan.textContent = totalPages;

        if (prevPageContainer) prevPageContainer.classList.toggle('disabled', page <= 1);
        if (nextPageContainer) nextPageContainer.classList.toggle('disabled', page >= totalPages);
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => loadTTSHistory(currentPage));
    }

    // Event listeners for new filter/sort controls
    if (channelFilterSelect) {
        channelFilterSelect.addEventListener('change', () => loadTTSHistory(1)); // Reset to page 1 on filter change
    }
    if (messageFilterInput) {
        // Use 'input' for instant filtering or 'change' for on blur
        let messageFilterTimeout;
        messageFilterInput.addEventListener('input', () => {
            clearTimeout(messageFilterTimeout);
            messageFilterTimeout = setTimeout(() => {
                loadTTSHistory(1); // Reset to page 1
            }, 500); // Debounce input
        });
    }
    if (sortBySelect) {
        sortBySelect.addEventListener('change', () => loadTTSHistory(1)); // Reset to page 1
    }
    if (sortOrderBtn && sortOrderIcon) {
        sortOrderBtn.addEventListener('click', () => {
            currentSortOrder = currentSortOrder === 'desc' ? 'asc' : 'desc';
            sortOrderIcon.className = `fas fa-sort-amount-${currentSortOrder === 'desc' ? 'down' : 'up'}`;
            sortOrderBtn.title = `Toggle Sort Order (${currentSortOrder === 'desc' ? 'Descending' : 'Ascending'})`;
            loadTTSHistory(1); // Reset to page 1
        });
    }


    if (prevPageBtn) {
        prevPageBtn.addEventListener('click', (e) => {
            e.preventDefault();
            if (currentPage > 1) {
                loadTTSHistory(currentPage - 1);
            }
        });
    }

    if (nextPageBtn) {
        nextPageBtn.addEventListener('click', (e) => {
            e.preventDefault();
            // Check against totalPages which should be updated by updatePaginationControls
            const currentTotalPages = parseInt(totalPagesSpan.textContent, 10);
            if (currentPage < currentTotalPages) {
                loadTTSHistory(currentPage + 1);
            }
        });
    }

    // Initial load
    fetchChannelsForTTSFilter(); // Populate channel filter
    loadTTSHistory(1);

    // Listen for EventBus notifications for new TTS entries
    if (window.EventBus && window.AppEvents) {
        window.EventBus.on(window.AppEvents.TTS_NEW_ENTRY, (data) => {
            console.log('[TTS History] EventBus TTS_NEW_ENTRY:', data);
            if (window.notificationSystem && window.notificationSystem.showToast) {
                // Check if the notification is for the currently viewed channel (if filtering is added later)
                // For now, always show and refresh.
                window.notificationSystem.showToast(`New TTS audio generated. Refreshing history.`, 'info');
            }
            // Reload the current page of TTS history to show the new entry.
            // Consider reloading page 1 if the new entry should always appear at the top of the first page.
            loadTTSHistory(currentPage); 
        });
        console.log("[TTS History] EventBus listener for TTS_NEW_ENTRY attached.");
    } else {
        console.warn("[TTS History] EventBus or AppEvents not available. Real-time updates for new TTS entries will not work.");
    }
});
