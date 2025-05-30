/**
 * Timezone detection and conversion utilities
 */

class TimezoneUtils {
    constructor() {
        this.userTimezone = this.detectTimezone();
    }

    /**
     * Detect user's timezone from browser
     * @returns {string} IANA timezone identifier
     */
    detectTimezone() {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (error) {
            console.warn('Could not detect timezone, defaulting to UTC:', error);
            return 'UTC';
        }
    }

    /**
     * Convert UTC timestamp to user's local time
     * @param {string} utcTimestamp - ISO timestamp string in UTC
     * @returns {string} Formatted local time string
     */
    toLocalTime(utcTimestamp) {
        try {
            const date = new Date(utcTimestamp);
            return date.toLocaleString(undefined, {
                timeZone: this.userTimezone,
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            });
        } catch (error) {
            console.error('Error converting timestamp:', error);
            return utcTimestamp;
        }
    }

    /**
     * Convert UTC timestamp to user's local time with relative formatting
     * @param {string} utcTimestamp - ISO timestamp string in UTC
     * @returns {string} Relative time string (e.g., "2 hours ago")
     */
    toRelativeTime(utcTimestamp) {
        try {
            const date = new Date(utcTimestamp);
            const now = new Date();
            const diffMs = now - date;
            const diffMinutes = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMinutes / 60);
            const diffDays = Math.floor(diffHours / 24);

            if (diffMinutes < 1) return 'Just now';
            if (diffMinutes < 60) return `${diffMinutes} minute${diffMinutes !== 1 ? 's' : ''} ago`;
            if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
            if (diffDays < 7) return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
            
            return this.toLocalTime(utcTimestamp);
        } catch (error) {
            console.error('Error calculating relative time:', error);
            return this.toLocalTime(utcTimestamp);
        }
    }

    /**
     * Update all timestamps on the page to local time
     */
    updateAllTimestamps() {
        // Update elements with data-utc-time attribute
        document.querySelectorAll('[data-utc-time]').forEach(element => {
            const utcTime = element.getAttribute('data-utc-time');
            if (utcTime) {
                element.textContent = this.toLocalTime(utcTime);
            }
        });

        // Update elements with data-utc-relative attribute for relative time
        document.querySelectorAll('[data-utc-relative]').forEach(element => {
            const utcTime = element.getAttribute('data-utc-relative');
            if (utcTime) {
                element.textContent = this.toRelativeTime(utcTime);
                element.title = this.toLocalTime(utcTime); // Show full time on hover
            }
        });
    }

    /**
     * Format timestamp for display in tables
     * @param {string} utcTimestamp - ISO timestamp string in UTC
     * @returns {string} Short formatted local time
     */
    toShortLocalTime(utcTimestamp) {
        try {
            const date = new Date(utcTimestamp);
            const now = new Date();
            const isToday = date.toDateString() === now.toDateString();
            
            if (isToday) {
                return date.toLocaleTimeString(undefined, {
                    timeZone: this.userTimezone,
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: false
                });
            } else {
                return date.toLocaleDateString(undefined, {
                    timeZone: this.userTimezone,
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: false
                });
            }
        } catch (error) {
            console.error('Error formatting short time:', error);
            return utcTimestamp;
        }
    }
}

// Create global instance
window.timezoneUtils = new TimezoneUtils();

// Helper function to update dynamic content that gets added after page load
function updateDynamicTimestamps() {
    if (window.timezoneUtils) {
        window.timezoneUtils.updateAllTimestamps();
    }
}

// Auto-update timestamps when page loads
document.addEventListener('DOMContentLoaded', () => {
    window.timezoneUtils.updateAllTimestamps();
    
    // Set up MutationObserver to watch for new timestamp elements
    const observer = new MutationObserver((mutations) => {
        let hasNewTimestamps = false;
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach((node) => {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    // Check if the added node has timestamp attributes or contains them
                    if (node.hasAttribute && (node.hasAttribute('data-utc-time') || node.hasAttribute('data-utc-relative'))) {
                        hasNewTimestamps = true;
                    } else if (node.querySelectorAll) {
                        const timestampElements = node.querySelectorAll('[data-utc-time], [data-utc-relative]');
                        if (timestampElements.length > 0) {
                            hasNewTimestamps = true;
                        }
                    }
                }
            });
        });
        
        if (hasNewTimestamps) {
            window.timezoneUtils.updateAllTimestamps();
        }
    });
    
    // Start observing document changes
    observer.observe(document.body, {
        childList: true,
        subtree: true
    });
});

// Export helper function for manual updates
window.updateDynamicTimestamps = updateDynamicTimestamps;