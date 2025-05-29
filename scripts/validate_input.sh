#!/bin/bash
# Input validation functions for launch.sh

# Validate bot name - alphanumeric, underscores, hyphens only, 1-50 chars
validate_bot_name() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-zA-Z0-9_-]{1,50}$ ]]; then
        return 1
    fi
    return 0
}

# Validate Twitch username - alphanumeric, underscores only, 4-25 chars
validate_username() {
    local username="$1"
    if [[ ! "$username" =~ ^[a-zA-Z0-9_]{4,25}$ ]]; then
        return 1
    fi
    return 0
}

# Validate channels list - comma-separated usernames
validate_channels() {
    local channels="$1"
    # Remove spaces and split by comma
    IFS=',' read -ra channel_array <<< "${channels// /}"
    
    for channel in "${channel_array[@]}"; do
        if ! validate_username "$channel"; then
            return 1
        fi
    done
    return 0
}

# Sanitize input by removing dangerous characters
sanitize_input() {
    local input="$1"
    # Remove potentially dangerous characters but keep basic punctuation
    echo "$input" | sed 's/[;<>&|`$(){}[\]\\]//g'
}

# Validate voice preset name
validate_voice_preset() {
    local preset="$1"
    # Allow only specific known presets or v2/en_speaker_N format
    if [[ "$preset" =~ ^v2/en_speaker_[0-9]$ ]] || [[ "$preset" == "v2/en_speaker_"[0-9] ]]; then
        return 0
    fi
    return 1
}