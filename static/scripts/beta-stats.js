// Beta Stats Page JavaScript
document.addEventListener('DOMContentLoaded', function() {
    initializeStatsPage();
});

function initializeStatsPage() {
    setupModelSorting();
    setupModelViewToggle();
    setupChannelPerformanceToggle();
    setupQuickActions();
    setupAutoRefresh();
    loadSystemInfo();
}

function setupModelSorting() {
    const sortSelect = document.getElementById('modelSortBy');
    if (sortSelect) {
        sortSelect.addEventListener('change', function() {
            sortModels(this.value);
        });
    }
}

function setupModelViewToggle() {
    const tableViewBtn = document.getElementById('modelTable');
    const gridViewBtn = document.getElementById('modelGrid');
    const tableView = document.getElementById('modelsTable');
    const gridView = document.getElementById('modelsGrid');
    
    if (tableViewBtn && gridViewBtn && tableView && gridView) {
        tableViewBtn.addEventListener('change', function() {
            if (this.checked) {
                tableView.style.display = 'block';
                gridView.style.display = 'none';
            }
        });
        
        gridViewBtn.addEventListener('change', function() {
            if (this.checked) {
                tableView.style.display = 'none';
                gridView.style.display = 'block';
            }
        });
    }
}

function sortModels(criteria) {
    // Sort both table and grid views
    sortModelTable(criteria);
    sortModelGrid(criteria);
}

function sortModelTable(criteria) {
    const tableBody = document.querySelector('#modelsTable tbody');
    if (!tableBody) return;

    const rows = Array.from(tableBody.querySelectorAll('tr[data-model]'));
    
    rows.sort((a, b) => {
        let aValue, bValue;
        
        switch (criteria) {
            case 'name':
                aValue = a.querySelector('.model-name').textContent.toLowerCase();
                bValue = b.querySelector('.model-name').textContent.toLowerCase();
                return aValue.localeCompare(bValue);
            
            case 'size':
                aValue = parseModelSize(a.querySelector('.model-size').textContent);
                bValue = parseModelSize(b.querySelector('.model-size').textContent);
                return bValue - aValue; // Descending
            
            case 'lines':
                aValue = parseInt(a.querySelector('.model-lines').textContent.replace(/,/g, '')) || 0;
                bValue = parseInt(b.querySelector('.model-lines').textContent.replace(/,/g, '')) || 0;
                return bValue - aValue; // Descending
            
            default:
                return 0;
        }
    });

    // Re-append sorted rows
    rows.forEach(row => tableBody.appendChild(row));
}

function sortModelGrid(criteria) {
    const modelsGrid = document.querySelector('#modelsGrid');
    if (!modelsGrid) return;

    const modelCards = Array.from(modelsGrid.querySelectorAll('.model-card'));
    
    modelCards.sort((a, b) => {
        let aValue, bValue;
        
        switch (criteria) {
            case 'name':
                aValue = a.querySelector('.model-name').textContent.toLowerCase();
                bValue = b.querySelector('.model-name').textContent.toLowerCase();
                return aValue.localeCompare(bValue);
            
            case 'size':
                const aSizeText = a.querySelector('.stat-value').textContent;
                const bSizeText = b.querySelector('.stat-value').textContent;
                aValue = parseModelSize(aSizeText);
                bValue = parseModelSize(bSizeText);
                return bValue - aValue; // Descending
            
            case 'lines':
                const aLinesText = a.querySelector('.stat-value').textContent;
                const bLinesText = b.querySelector('.stat-value').textContent;
                aValue = parseInt(aLinesText.replace(/,/g, '')) || 0;
                bValue = parseInt(bLinesText.replace(/,/g, '')) || 0;
                return bValue - aValue; // Descending
            
            default:
                return 0;
        }
    });

    // Re-append sorted cards
    modelCards.forEach(card => modelsGrid.appendChild(card));
}

function parseModelSize(sizeText) {
    // Parse size strings like "1.2 MB", "500 KB", etc.
    const match = sizeText.match(/([0-9.]+)\s*(B|KB|MB|GB)/i);
    if (!match) return 0;
    
    const value = parseFloat(match[1]);
    const unit = match[2].toUpperCase();
    
    const multipliers = {
        'B': 1,
        'KB': 1024,
        'MB': 1024 * 1024,
        'GB': 1024 * 1024 * 1024
    };
    
    return value * (multipliers[unit] || 1);
}

function setupChannelPerformanceToggle() {
    const toggleButtons = document.querySelectorAll('[data-toggle="channel-view"]');
    
    toggleButtons.forEach(button => {
        button.addEventListener('click', function() {
            const view = this.dataset.view;
            toggleChannelView(view);
            
            // Update active button
            toggleButtons.forEach(btn => btn.classList.remove('active'));
            this.classList.add('active');
        });
    });
}

function toggleChannelView(view) {
    const tableView = document.querySelector('.channel-table-view');
    const gridView = document.querySelector('.channel-grid-view');
    
    if (view === 'table') {
        tableView?.classList.remove('d-none');
        gridView?.classList.add('d-none');
    } else {
        tableView?.classList.add('d-none');
        gridView?.classList.remove('d-none');
    }
}

function setupQuickActions() {
    // Rebuild all models
    const rebuildAllBtn = document.querySelector('[data-action="rebuild-all"]');
    if (rebuildAllBtn) {
        rebuildAllBtn.addEventListener('click', function() {
            if (confirm('Are you sure you want to rebuild all models? This may take several minutes.')) {
                rebuildAllModels();
            }
        });
    }

    // Export stats
    const exportBtn = document.querySelector('[data-action="export-stats"]');
    if (exportBtn) {
        exportBtn.addEventListener('click', exportStats);
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

    // Individual model rebuild buttons
    document.querySelectorAll('.rebuild-model-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const modelName = this.dataset.model;
            if (confirm(`Rebuild model for ${modelName}?`)) {
                rebuildModel(modelName);
            }
        });
    });
}

async function rebuildAllModels() {
    try {
        showToast('Starting model rebuild...', 'info');
        const response = await fetch('/api/rebuild-all-models', {
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

async function rebuildModel(modelName) {
    try {
        showToast(`Rebuilding ${modelName} model...`, 'info');
        const response = await fetch(`/api/rebuild-model/${modelName}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (response.ok) {
            showToast(`${modelName} model rebuild started`, 'success');
            setTimeout(() => location.reload(), 2000);
        } else {
            throw new Error('Failed to rebuild model');
        }
    } catch (error) {
        console.error('Error rebuilding model:', error);
        showToast(`Failed to rebuild ${modelName} model`, 'error');
    }
}

async function exportStats() {
    try {
        const response = await fetch('/api/export-stats');
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ansv-stats-${new Date().toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showToast('Stats exported successfully', 'success');
        } else {
            throw new Error('Failed to export stats');
        }
    } catch (error) {
        console.error('Error exporting stats:', error);
        showToast('Failed to export stats', 'error');
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

function setupAutoRefresh() {
    // Auto-refresh stats every 30 seconds
    setInterval(async () => {
        try {
            await updateStats();
        } catch (error) {
            console.error('Error updating stats:', error);
        }
    }, 30000);
}

async function updateStats() {
    try {
        const response = await fetch('/api/stats-data');
        if (!response.ok) return;
        
        const data = await response.json();
        
        // Update summary cards
        updateSummaryCards(data.summary);
        
        // Update recent activity if needed
        if (data.recent_activity) {
            updateRecentActivity(data.recent_activity);
        }
        
    } catch (error) {
        console.error('Error updating stats:', error);
    }
}

function updateSummaryCards(summary) {
    // Update total messages
    const totalMessagesEl = document.querySelector('[data-stat="total-messages"]');
    if (totalMessagesEl && summary.total_messages !== undefined) {
        totalMessagesEl.textContent = summary.total_messages.toLocaleString();
    }
    
    // Update total TTS
    const totalTTSEl = document.querySelector('[data-stat="total-tts"]');
    if (totalTTSEl && summary.total_tts !== undefined) {
        totalTTSEl.textContent = summary.total_tts.toLocaleString();
    }
    
    // Update active channels
    const activeChannelsEl = document.querySelector('[data-stat="active-channels"]');
    if (activeChannelsEl && summary.active_channels !== undefined) {
        activeChannelsEl.textContent = summary.active_channels;
    }
}

async function loadSystemInfo() {
    try {
        const response = await fetch('/api/system-info');
        if (!response.ok) return;
        
        const data = await response.json();
        updateSystemInfo(data);
        
    } catch (error) {
        console.error('Error loading system info:', error);
        // Set default values on error
        updateSystemInfo({
            uptime: 'Unknown',
            cache_size: 'Unknown',
            database_size: 'Unknown'
        });
    }
}

function updateSystemInfo(data) {
    const uptimeEl = document.getElementById('systemUptime');
    const cacheSizeEl = document.getElementById('totalCacheSize');
    const dbSizeEl = document.getElementById('databaseSize');
    
    if (uptimeEl && data.uptime) {
        uptimeEl.querySelector('span').textContent = formatUptime(data.uptime);
    }
    
    if (cacheSizeEl && data.cache_size) {
        cacheSizeEl.querySelector('span').textContent = data.cache_size;
    }
    
    if (dbSizeEl && data.database_size) {
        dbSizeEl.querySelector('span').textContent = data.database_size;
    }
}

function formatUptime(seconds) {
    if (!seconds || isNaN(seconds)) return 'Unknown';
    
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    
    if (days > 0) {
        return `${days}d ${hours}h ${minutes}m`;
    } else if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else {
        return `${minutes}m`;
    }
}

function getActivityIcon(type) {
    const icons = {
        'message': 'fas fa-comment',
        'tts': 'fas fa-volume-up',
        'model': 'fas fa-brain',
        'channel': 'fas fa-hashtag',
        'system': 'fas fa-cog'
    };
    return icons[type] || 'fas fa-info-circle';
}

function showToast(message, type = 'info') {
    // Use the global notification system if available
    if (window.notificationSystem && typeof window.notificationSystem.showToast === 'function') {
        window.notificationSystem.showToast(message, type);
    } else if (typeof window.showToast === 'function') {
        window.showToast(message, type);
    } else {
        // Fallback to console and alert
        console.log(`Toast (${type}): ${message}`);
        if (type === 'error') {
            alert(`Error: ${message}`);
        }
    }
}