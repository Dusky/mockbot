// ====================================================================
// ANSV Bot - Beta Base JavaScript
// ====================================================================

document.addEventListener('DOMContentLoaded', function() {
    console.log('[Beta] Initializing base functionality...');
    
    // Initialize components
    initializeTheme();
    initializeNavigation();
    initializeBotStatus();
    initializeToasts();
    
    console.log('[Beta] Base initialization complete');
});

// ====================================================================
// Theme System
// ====================================================================

function initializeTheme() {
    const themeOptions = document.querySelectorAll('.theme-option');
    
    themeOptions.forEach(option => {
        option.addEventListener('click', (e) => {
            e.preventDefault();
            const theme = option.dataset.theme;
            setTheme(theme);
        });
    });
    
    // Apply saved theme or detect system preference
    const savedTheme = localStorage.getItem('beta-theme');
    if (savedTheme) {
        setTheme(savedTheme);
    } else {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        setTheme(prefersDark ? 'dark' : 'light');
    }
}

function setTheme(theme) {
    const html = document.documentElement;
    
    if (theme === 'auto') {
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        html.setAttribute('data-bs-theme', prefersDark ? 'dark' : 'light');
    } else {
        html.setAttribute('data-bs-theme', theme);
    }
    
    localStorage.setItem('beta-theme', theme);
    
    // Update active theme option
    document.querySelectorAll('.theme-option').forEach(option => {
        option.classList.remove('active');
        if (option.dataset.theme === theme) {
            option.classList.add('active');
        }
    });
}

// ====================================================================
// Navigation
// ====================================================================

function initializeNavigation() {
    loadChannelsDropdown();
}

async function loadChannelsDropdown() {
    try {
        const response = await fetch('/api/channels');
        const channels = await response.json();
        
        const dropdown = document.getElementById('channelsDropdown');
        if (!dropdown) return;
        
        if (channels.length === 0) {
            dropdown.innerHTML = '<li><span class="dropdown-item text-muted">No channels configured</span></li>';
            return;
        }
        
        dropdown.innerHTML = channels.map(channel => `
            <li>
                <a class="dropdown-item" href="/beta/channel/${channel.name}">
                    <i class="fas fa-hashtag me-2"></i>
                    ${channel.name}
                    ${channel.currently_connected ? 
                        '<i class="fas fa-circle text-success ms-auto" style="font-size: 0.5rem;"></i>' : 
                        '<i class="fas fa-circle text-muted ms-auto" style="font-size: 0.5rem;"></i>'
                    }
                </a>
            </li>
        `).join('');
        
    } catch (error) {
        console.error('[Beta] Error loading channels dropdown:', error);
        const dropdown = document.getElementById('channelsDropdown');
        if (dropdown) {
            dropdown.innerHTML = '<li><span class="dropdown-item text-danger">Error loading channels</span></li>';
        }
    }
}

// ====================================================================
// Bot Status
// ====================================================================

function initializeBotStatus() {
    updateBotStatus();
    // Update bot status every 10 seconds
    setInterval(updateBotStatus, 10000);
}

async function updateBotStatus() {
    try {
        const response = await fetch('/api/bot-status');
        const status = await response.json();
        
        // Update navbar status
        const navStatus = document.getElementById('botStatusNav');
        if (navStatus) {
            const icon = navStatus.querySelector('i');
            const text = navStatus.querySelector('span');
            
            if (status.running && status.connected) {
                icon.className = 'fas fa-circle text-success me-1';
                text.textContent = 'Online';
            } else if (status.running) {
                icon.className = 'fas fa-circle text-warning me-1';
                text.textContent = 'Starting...';
            } else {
                icon.className = 'fas fa-circle text-danger me-1';
                text.textContent = 'Offline';
            }
        }
        
        // Update dashboard status cards if they exist
        updateDashboardStatus(status);
        
    } catch (error) {
        console.error('[Beta] Error updating bot status:', error);
        
        const navStatus = document.getElementById('botStatusNav');
        if (navStatus) {
            const icon = navStatus.querySelector('i');
            const text = navStatus.querySelector('span');
            icon.className = 'fas fa-circle text-muted me-1';
            text.textContent = 'Unknown';
        }
    }
}

function updateDashboardStatus(status) {
    // Update bot status card
    const botStatusText = document.getElementById('botStatusText');
    const botUptimeText = document.getElementById('botUptimeText');
    
    if (botStatusText) {
        if (status.running && status.connected) {
            botStatusText.textContent = 'Online';
            botStatusText.className = 'status-value text-success';
        } else if (status.running) {
            botStatusText.textContent = 'Starting...';
            botStatusText.className = 'status-value text-warning';
        } else {
            botStatusText.textContent = 'Offline';
            botStatusText.className = 'status-value text-danger';
        }
    }
    
    if (botUptimeText && status.uptime) {
        const uptime = formatUptime(status.uptime);
        botUptimeText.textContent = `Uptime: ${uptime}`;
    }
    
    // Update active channels count
    const activeChannelsCount = document.getElementById('activeChannelsCount');
    if (activeChannelsCount && status.joined_channels) {
        activeChannelsCount.textContent = status.joined_channels.length;
    }
}

function formatUptime(seconds) {
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

// ====================================================================
// Toast Notifications
// ====================================================================

function initializeToasts() {
    // Create global toast function
    window.showToast = function(message, type = 'info', duration = 4000) {
        const toastContainer = document.getElementById('toastContainer');
        if (!toastContainer) return;
        
        const toastId = 'toast-' + Date.now();
        const iconMap = {
            'success': 'fas fa-check-circle',
            'error': 'fas fa-exclamation-triangle',
            'warning': 'fas fa-exclamation-circle',
            'info': 'fas fa-info-circle'
        };
        
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-bg-${type === 'error' ? 'danger' : type} border-0`;
        toast.id = toastId;
        toast.setAttribute('role', 'alert');
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body d-flex align-items-center">
                    <i class="${iconMap[type] || iconMap.info} me-2"></i>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        
        toastContainer.appendChild(toast);
        
        const bsToast = new bootstrap.Toast(toast, {
            autohide: true,
            delay: duration
        });
        
        bsToast.show();
        
        // Remove from DOM after hiding
        toast.addEventListener('hidden.bs.toast', () => {
            toast.remove();
        });
    };
}

// ====================================================================
// Utility Functions
// ====================================================================

function formatTimestamp(timestamp) {
    // Use timezone utility if available for relative time
    if (window.timezoneUtils) {
        return window.timezoneUtils.toRelativeTime(timestamp);
    }
    
    // Fallback to original logic
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);
    
    if (diffMins < 1) {
        return 'Just now';
    } else if (diffMins < 60) {
        return `${diffMins}m ago`;
    } else if (diffHours < 24) {
        return `${diffHours}h ago`;
    } else if (diffDays < 7) {
        return `${diffDays}d ago`;
    } else {
        return date.toLocaleDateString();
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ====================================================================
// Loading States
// ====================================================================

function showLoading(element, text = 'Loading...') {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (!element) return;
    
    element.innerHTML = `
        <div class="d-flex align-items-center justify-content-center p-3">
            <div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>
            <span class="text-muted">${text}</span>
        </div>
    `;
}

function hideLoading(element) {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (!element) return;
    
    element.innerHTML = '';
}

// ====================================================================
// API Helpers
// ====================================================================

function getCsrfTokenFromCookie() {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
        const [name, value] = cookie.trim().split('=');
        if (name === 'csrf_token') {
            return value;
        }
    }
    return null;
}

async function apiRequest(url, options = {}) {
    try {
        // Get CSRF token from meta tag or generate if needed
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
                         window.csrfToken ||
                         getCsrfTokenFromCookie();
        
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };
        
        // Add CSRF token for POST requests
        if (options.method === 'POST' && csrfToken) {
            headers['X-CSRF-Token'] = csrfToken;
        }
        
        const response = await fetch(url, {
            headers,
            ...options
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || data.message || 'Request failed');
        }
        
        return data;
    } catch (error) {
        console.error(`[Beta] API request failed:`, error);
        throw error;
    }
}

// Export functions for global use
window.betaUtils = {
    formatTimestamp,
    escapeHtml,
    showLoading,
    hideLoading,
    apiRequest,
    updateBotStatus,
    loadChannelsDropdown
};