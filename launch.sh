#!/bin/bash
set -euo pipefail  # Exit on error, undefined vars, pipe failures

# Source validation functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/validate_input.sh" 2>/dev/null || {
    echo "Warning: Input validation functions not found. Some security features disabled."
}

# Timeout settings
NETWORK_TIMEOUT=30
DEPS_CHECK_INTERVAL=7

# ANSI Color Codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Emoji Constants
ROCKET="🚀"
ROBOT="🤖"
GLOBE="🌐"
TOOLS="🛠️"
PARTY="🎉"
ALIEN="👽"
FIRE="🔥"
BRAIN="🧠"
MUSIC="🎵"

# Environment Setup
VENV_DIR=".venv"
CONFIG_FILE="settings.conf"
EXAMPLE_CONFIG="settings.example.conf"
TTS_MODELS_DIR="models/tts" # This is where Bark models will be cached by HuggingFace
TTS_OUTPUT_DIR="static/outputs"
TTS_VOICES_DIR="voices" # This is for custom .npz voice files

# ASCII Art Banner with Animation Frames
print_banner() {
    clear
    frames=(
        "    █████╗ ███╗   ██╗███████╗██╗   ██╗    "
        "   ██╔══██╗████╗  ██║██╔════╝██║   ██║    "
        "   ███████║██╔██╗ ██║███████╗██║   ██║    "
        "   ██╔══██║██║╚██╗██║╚════██║██║   ██║    "
        "   ██║  ██║██║ ╚████║███████║╚██████╔╝    "
        "   ╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝ ╚═════╝     "
    )
    
    echo -e "${MAGENTA}"
    for frame in "${frames[@]}"; do
        echo "$frame"
        sleep 0.1
    done
    echo -e "${NC}"
}

# Fun Loading Spinner
show_loading() {
    echo -n -e "${YELLOW}Preparing the party "
    while true; do
        for c in "🌑" "🌒" "🌓" "🌔" "🌕" "🌖" "🌗" "🌘"; do
            echo -ne "\b$c"
            sleep 0.1
        done
    done
}

# First-Run Dance Party 🎉
first_run_dance() {
    echo -e "\n${CYAN}First run detected! Let's dance!${NC}"
    dances=("💃" "🕺" "🎊" "🎈")
    for i in {1..10}; do
        echo -n -e "${dances[$RANDOM % ${#dances[@]}]} "
        sleep 0.2
    done
    echo -e "\n"
}

# Configuration Wizard with Personality and Input Validation
first_run_wizard() {
    echo -e "${BRAIN} ${YELLOW}Hi! I'm ANSV-Buddy! Let's get you set up!${NC}"
    echo -e "${CYAN}I'll help you create a settings.conf file with your bot configuration.${NC}\n"
    
    # Get and validate bot name
    while true; do
        read -p "$(echo -e "${ROBOT} What's your bot's name? ➤ ")" bot_name
        bot_name="$(sanitize_input "$bot_name" 2>/dev/null || echo "$bot_name")"
        if validate_bot_name "$bot_name" 2>/dev/null || [[ ${#bot_name} -gt 0 && ${#bot_name} -le 50 ]]; then
            break
        fi
        echo -e "${RED}Invalid bot name. Use 1-50 alphanumeric characters, underscores, or hyphens.${NC}"
    done
    
    # Get and validate owner username
    while true; do
        read -p "$(echo -e "${ALIEN} Your Twitch username? ➤ ")" owner
        owner="$(sanitize_input "$owner" 2>/dev/null || echo "$owner")"
        if validate_username "$owner" 2>/dev/null || [[ ${#owner} -ge 4 && ${#owner} -le 25 ]]; then
            break
        fi
        echo -e "${RED}Invalid username. Use 4-25 alphanumeric characters or underscores.${NC}"
    done
    
    # Get and validate channels
    while true; do
        read -p "$(echo -e "${GLOBE} Channels to join (comma-separated)? ➤ ")" channels
        channels="$(sanitize_input "$channels" 2>/dev/null || echo "$channels")"
        if validate_channels "$channels" 2>/dev/null || [[ -n "$channels" ]]; then
            break
        fi
        echo -e "${RED}Invalid channels. Use comma-separated valid usernames.${NC}"
    done
    
    # Get command prefix (optional)
    read -p "$(echo -e "${TOOLS} Command prefix [default: !]: ")" command_prefix
    command_prefix="${command_prefix:-!}"
    
    # Get admin password
    while true; do
        read -s -p "$(echo -e "${FIRE} Admin password for web interface [default: admin123]: ")" admin_password
        echo  # New line after hidden input
        admin_password="${admin_password:-admin123}"
        if [[ ${#admin_password} -ge 4 ]]; then
            break
        fi
        echo -e "${RED}Password must be at least 4 characters long.${NC}"
    done
    
    # Ask about TTS
    read -p "$(echo -e "${MUSIC} Enable TTS functionality? (y/n) [default: n]: ")" enable_tts
    enable_tts="${enable_tts:-n}"
    tts_enabled="false"
    voice_preset="v2/en_speaker_6"
    if [[ "$enable_tts" =~ ^[Yy] ]]; then
        tts_enabled="true"
        read -p "$(echo -e "${MUSIC} Voice preset [default: v2/en_speaker_6]: ")" voice_preset_input
        voice_preset="${voice_preset_input:-v2/en_speaker_6}"
    fi
    
    # Generate a random secret key for web interface
    secret_key="$(openssl rand -hex 32 2>/dev/null || echo "change-this-secret-key-$(date +%s)")"
    
    echo -e "\n${CYAN}📝 Creating your configuration file...${NC}"
    
    # Create comprehensive config file
    if cat > "$CONFIG_FILE" << EOF
[auth]
; Get these from https://dev.twitch.tv/console
tmi_token = oauth:your_token_here
client_id = your_client_id_here
nickname = $bot_name
owner = $owner
; Admin password for web interface
admin_password = $admin_password

[settings]
; Prefix for bot commands
command_prefix = $command_prefix
; Channels to auto-join (comma-separated)
channels = $channels
; Enable debug mode for verbose logging (true/false)
debug_mode = false
; Users to ignore (comma-separated)
ignored_users = nightbot,moobot,streamelements,streamlabs

[web]
; Web interface configuration
port = 5001
host = 0.0.0.0
secret_key = $secret_key
verbose_logs = false

[tts]
; TTS settings
enable_tts = $tts_enabled
voice_preset = $voice_preset
EOF
    then
        echo -e "\n${GREEN}✅ Configuration file created successfully!${NC}"
        echo -e "${PARTY} ${GREEN}All set! Let's get this party started!${NC}"
        
        # Show next steps
        echo -e "\n${YELLOW}🔑 IMPORTANT NEXT STEPS:${NC}"
        echo -e "${CYAN}1. Get your Twitch API credentials from: ${BLUE}https://dev.twitch.tv/console${NC}"
        echo -e "${CYAN}2. Edit settings.conf and replace:${NC}"
        echo -e "   ${YELLOW}oauth:your_token_here${NC} with your OAuth token"
        echo -e "   ${YELLOW}your_client_id_here${NC} with your Client ID"
        echo -e "${CYAN}3. Run the bot again after updating credentials!${NC}\n"
        
        first_run_dance
        return 0
    else
        echo -e "${RED}Failed to create configuration file!${NC}"
        return 1
    fi
}

# Enhanced Security Check
check_security() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo -e "${RED}Configuration file not found!${NC}"
        return 1
    fi
    
    if grep -q "oauth:your" "$CONFIG_FILE" 2>/dev/null; then
        echo -e "${RED}🚨 RED ALERT! 🚨${NC}"
        echo -e "${FIRE} You're using placeholder credentials! ${FIRE}${NC}"
        echo -e "${YELLOW}Visit ${CYAN}https://dev.twitch.tv/console${YELLOW}"
        echo -e "to get your real credentials!${NC}"
        exit 1
    fi
    
    # Check file permissions
    if [[ "$(stat -f "%A" "$CONFIG_FILE" 2>/dev/null || stat -c "%a" "$CONFIG_FILE" 2>/dev/null)" != "600" ]]; then
        echo -e "${YELLOW}Warning: Configuration file has loose permissions. Fixing...${NC}"
        chmod 600 "$CONFIG_FILE" || {
            echo -e "${RED}Failed to secure configuration file permissions!${NC}"
            exit 1
        }
    fi
}

# Enhanced Status Dashboard with User Interaction
show_status() {
    echo -e "\n${CYAN}=== System Status Dashboard ==="
    echo -e "${ROCKET} Bot Running:    $(ps aux | grep [a]nsv.py | wc -l)"
    echo -e "${GLOBE} Web Interface:  $(netstat -tuln | grep ':5001' | wc -l)"
    echo -e "${MUSIC} TTS Enabled:    ${TTS:-false}"
    echo -e "${MUSIC} Voice Models:   $(find "${HF_HOME:-$PWD/.hf_cache}/hub" -type d -name "snapshots" 2>/dev/null | wc -l)"
    echo -e "${MUSIC} Voice Presets:  $(find "$TTS_VOICES_DIR" -name "*.npz" 2>/dev/null | wc -l)"
    echo -e "${BRAIN} Models Loaded:  $(ls cache/*.json 2>/dev/null | wc -l)"
    echo -e "${ROBOT} Messages in DB: $(sqlite3 messages.db "SELECT COUNT(*) FROM messages" 2>/dev/null || echo "N/A")"
    echo -e "${TOOLS} Python Version: $(python3.11 --version 2>&1 | cut -d' ' -f2 2>/dev/null || echo "Not found")"
    echo -e "${ALIEN} Disk Usage:     $(du -sh . 2>/dev/null | cut -f1 || echo "N/A")"
    echo -e "${PARTY} Last Modified:  $(stat -f "%Sm" -t "%Y-%m-%d %H:%M" ansv.py 2>/dev/null || stat -c "%y" ansv.py 2>/dev/null | cut -d' ' -f1-2 || echo "N/A")"
    echo -e "${FIRE} Active Users:   $(who 2>/dev/null | wc -l | tr -d ' ' || echo "N/A")"
    echo -e "==============================${NC}"
    echo -e "\n${YELLOW}Press any key to return to menu...${NC}"
    read -n 1 -s
}

# Enhanced Menu System with Emojis
interactive_menu() {
    while true; do
        print_banner
        echo -e "${CYAN}=== MAIN MENU ==="
        echo -e "0) ${TOOLS} Clean Install & Setup"
        echo -e "1) ${ROCKET} Launch Bot Only"
        echo -e "2) ${GLOBE} Bot + Web Interface"
        echo -e "3) ${MUSIC} Bot + Web + TTS"
        echo -e "4) ${TOOLS} Web Interface Only"
        echo -e "5) ${FIRE} Development Server"
        echo -e "6) ${BRAIN} Rebuild Cache & Start"
        echo -e "7) ${ALIEN} System Status"
        echo -e "8) ${PARTY} Exit"
        echo -n -e "\n${YELLOW}What's your pleasure? [0-8]: ${NC}"

        read -r choice
        case $choice in
            0) 
                echo -e "${YELLOW}Enable TTS for clean install? (y/n): ${NC}"
                read -r tts_choice
                if [ "$tts_choice" = "y" ]; then
                    TTS=true
                    echo -e "${YELLOW}Force CPU-only PyTorch? (y/n): ${NC}"
                    read -r cpu_choice
                    [ "$cpu_choice" = "y" ] && CPU_ONLY=true
                fi
                clean_install
                break
                ;;
            1) start_bot; break ;;
            2) WEB=true; start_bot; break ;;
            3) 
                WEB=true 
                TTS=true
                echo -e "${YELLOW}Force CPU-only PyTorch? (y/n): ${NC}"
                read -r cpu_choice
                [ "$cpu_choice" = "y" ] && CPU_ONLY=true
                check_dependencies
                install_requirements
                start_bot
                break
                ;;
            4) start_web; break ;;
            5) dev_server; break ;;
            6) REBUILD=true; start_bot; break ;;
            7) show_status;;
            8) echo -e "${PARTY} ${RED}Exiting...${NC}"; exit 0 ;;
            *) echo -e "${RED}Invalid choice! Try again! ${FIRE}"; sleep 1 ;;
        esac
    done
}

# Enhanced dependency check with proper error handling
need_dependencies_check() {
    local deps_marker=".deps_installed"
    
    # If marker doesn't exist, need to check deps
    if [[ ! -f "$deps_marker" ]]; then
        return 0  # true, need to check deps
    fi
    
    # Check if marker is older than configured interval
    if command -v find >/dev/null 2>&1; then
        local old_files
        old_files=$(find "$deps_marker" -mtime +"$DEPS_CHECK_INTERVAL" -print 2>/dev/null | wc -l)
        if [[ "$old_files" -gt 0 ]]; then
            return 0  # true, need to check deps
        fi
    fi
    
    return 1  # false, no need to check deps
}

# Safe process termination
safe_kill_process() {
    local pid="$1"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        # Wait briefly for process to terminate
        sleep 0.5
        # Force kill if still running
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    fi
}

# Enhanced start_bot function with array-based execution
start_bot() {
    echo -e "\n${GREEN}${ROCKET} Launch sequence initiated! ${ROCKET}${NC}"
    show_loading &
    local load_pid=$!
    
    # Only check deps if not coming from clean install and deps need checking
    if [[ -z "${CLEAN_INSTALL_DONE:-}" ]] && need_dependencies_check; then
        check_dependencies
        install_requirements
        # Create or update the deps marker file
        touch .deps_installed || {
            echo -e "${RED}Failed to create dependency marker${NC}"
            safe_kill_process "$load_pid"
            exit 1
        }
    else
        echo -e "${GREEN}Using existing dependencies${NC}"
    fi
    
    # Run database migrations if not coming from clean install
    if [[ -z "${CLEAN_INSTALL_DONE:-}" ]]; then
        safe_kill_process "$load_pid"
        if ! run_database_migrations; then
            echo -e "${RED}Database migration failed${NC}"
            exit 1
        fi
        # Restart loading animation
        show_loading &
        load_pid=$!
    fi
    
    # Validate virtual environment
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${RED}Virtual environment not found at $VENV_DIR${NC}"
        safe_kill_process "$load_pid"
        exit 1
    fi
    
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate" || {
        echo -e "${RED}Failed to activate virtual environment${NC}"
        safe_kill_process "$load_pid"
        exit 1
    }
    
    # Build command array instead of string concatenation
    local cmd_array=("python" "ansv.py")
    
    [[ "${WEB:-false}" = true ]] && cmd_array+=("--web")
    [[ "${TTS:-false}" = true ]] && cmd_array+=("--tts")
    [[ "${REBUILD:-false}" = true ]] && cmd_array+=("--rebuild-cache")
    [[ "${VERBOSE:-false}" = true ]] && export VERBOSE=1
    if [[ -n "${VOICE_PRESET:-}" ]]; then
        # Validate voice preset before using
        if validate_voice_preset "$VOICE_PRESET" 2>/dev/null || [[ "$VOICE_PRESET" =~ ^v2/en_speaker_[0-9]$ ]]; then
            cmd_array+=("--voice-preset" "$VOICE_PRESET")
        else
            echo -e "${YELLOW}Warning: Invalid voice preset '$VOICE_PRESET', ignoring${NC}"
        fi
    fi
    
    safe_kill_process "$load_pid"
    echo -e "\n${GREEN}${FIRE} BLAST OFF! ${FIRE}${NC}"
    
    # Set up signal handling for clean shutdown
    trap 'echo "Caught signal, shutting down cleanly..."; safe_kill_process "$bot_pid" 2>/dev/null; exit 130' INT TERM
    
    # Execute command using array (safe from injection)
    "${cmd_array[@]}" &
    local bot_pid=$!
    
    # Wait for process and capture exit code
    wait "$bot_pid"
    local exit_code=$?
    
    if [[ $exit_code -ne 0 ]]; then
        echo -e "${RED}Bot exited with error code $exit_code${NC}"
    fi
    
    exit "$exit_code"
}

# Enhanced Helper Functions with Error Handling
check_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${RED}Virtual environment not found!${NC}"
        echo -e "Run: ${YELLOW}python3.11 -m venv $VENV_DIR${NC}"
        exit 1
    fi
    
    # Verify virtual environment is functional
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${RED}Virtual environment appears corrupted!${NC}"
        echo -e "${YELLOW}Try running: ./launch.sh --clean${NC}"
        exit 1
    fi
}

check_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        # Config file doesn't exist - this is normal for first run
        # The main function will handle running the first_run_wizard
        return 0
    fi
    
    # Config file exists, check permissions
    if [[ "$(stat -f "%A" "$CONFIG_FILE" 2>/dev/null || stat -c "%a" "$CONFIG_FILE" 2>/dev/null)" != "600" ]]; then
        echo -e "${YELLOW}Warning: Configuration file has loose permissions. Fixing...${NC}"
        chmod 600 "$CONFIG_FILE" || {
            echo -e "${YELLOW}Warning: Could not set secure permissions on config file${NC}"
        }
    fi
}

# Network operation with timeout
run_with_timeout() {
    local timeout="$1"
    shift
    
    if command -v timeout >/dev/null 2>&1; then
        timeout "$timeout" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then  # macOS with coreutils
        gtimeout "$timeout" "$@"
    else
        # Fallback without timeout
        "$@"
    fi
}

# Enhanced Command Handlers
start_web() {
    echo -e "${GREEN}🌐 Starting Web Interface...${NC}"
    
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${RED}Virtual environment not found!${NC}"
        exit 1
    fi
    
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate" || {
        echo -e "${RED}Failed to activate virtual environment${NC}"
        exit 1
    }
    
    if [[ ! -f "webapp.py" ]]; then
        echo -e "${RED}webapp.py not found!${NC}"
        exit 1
    fi
    
    python webapp.py
}

dev_server() {
    echo -e "${GREEN}🔧 Starting Development Server...${NC}"
    export FLASK_DEBUG=1
    export FLASK_ENV=development
    
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${RED}Virtual environment not found!${NC}"
        exit 1
    fi
    
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate" || {
        echo -e "${RED}Failed to activate virtual environment${NC}"
        exit 1
    }
    
    python webapp.py
}

# Main Execution
parse_arguments() {
    # Default values
    WEB=false
    TTS=false
    REBUILD=false
    DEV=false
    WEB_ONLY=false
    CLEAN=false
    VERBOSE=false
    VOICE_PRESET=""
    CPU_ONLY=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --web) WEB=true ;;
            --tts) TTS=true ;;
            --verbose) VERBOSE=true ;;
            --voice-preset)
                VOICE_PRESET="$2"
                shift 
                ;;
            --download-models) 
                export HF_HOME="${PWD}/.hf_cache"
                
                if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
                    echo -e "${RED}Virtual environment not found! Run --clean first.${NC}"
                    exit 1
                fi
                
                # shellcheck disable=SC1091
                source "$VENV_DIR/bin/activate" || {
                    echo -e "${RED}Failed to activate virtual environment${NC}"
                    exit 1
                }
                
                prepare_tts_directories
                check_tts_models
                download_voice_presets
                echo -e "${GREEN}✅ Models downloaded successfully!${NC}"
                exit 0 
                ;;
            --rebuild) REBUILD=true ;;
            --dev) DEV=true ;;
            --web-only) WEB_ONLY=true ;;
            --clean) CLEAN=true ;;
            --cpu-only) CPU_ONLY=true ;;
            --migrate-db) 
                if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
                    echo -e "${RED}Virtual environment not found! Run --clean first.${NC}"
                    exit 1
                fi
                
                # shellcheck disable=SC1091
                source "$VENV_DIR/bin/activate" || {
                    echo -e "${RED}Failed to activate virtual environment${NC}"
                    exit 1
                }
                
                run_database_migrations
                exit $?
                ;;
            -h|--help) show_help; exit 0 ;;
            *) echo -e "${RED}Unknown option: $1${NC}"; exit 1 ;;
        esac
        shift
    done
    
    # Second pass: execute commands based on flags
    if [[ "$CLEAN" = true ]]; then
        clean_install
        # After clean install, continue with other commands
        [[ "$TTS" = true ]] && TTS=true
    fi
    
    # Handle post-clean operations
    if [[ "$DEV" = true ]]; then
        dev_server
        exit 0
    elif [[ "$WEB_ONLY" = true ]]; then
        start_web
        exit 0
    elif [[ "$CLEAN" = true ]]; then
        # If clean was only command, start normally
        start_bot
        exit 0
    fi
    start_bot
}

show_help() {
    echo -e "${YELLOW}Usage:${NC}"
    echo "  ./launch.sh [options] - Command-line mode"
    echo "  ./launch.sh           - Interactive menu mode"
    echo
    echo "${YELLOW}Options:${NC}"
    echo "  --web       : Enable web interface with bot"
    echo "  --tts       : Enable TTS functionality"
    echo "  --verbose   : Show detailed initialization steps and logs"
    echo "  --voice-preset PRESET : Use specific voice preset (e.g., v2/en_speaker_6)"
    echo "  --download-models : Only download TTS models and exit"
    echo "  --rebuild   : Rebuild Markov cache at startup"
    echo "  --dev       : Start web interface in development mode"
    echo "  --web-only  : Start web interface without bot"
    echo "  --clean     : Perform fresh install"
    echo "  --cpu-only  : Force CPU-only PyTorch installation (no CUDA)"
    echo "  --migrate-db: Run database migrations manually"
    echo "  --help      : Show this help message"
    echo
    echo "${YELLOW}Configuration:${NC}"
    echo "  In settings.conf, set 'verbose_logs = true' in the [web] section"
    echo "  to see detailed debug logs (database heartbeats, stats requests, etc.)"
    echo
    echo "${YELLOW}Examples:${NC}"
    echo "  ./launch.sh --web --tts      # Bot + Web + TTS"
    echo "  ./launch.sh --tts --verbose # Bot with TTS and detailed logs"
    echo "  ./launch.sh --tts --voice-preset v2/en_speaker_9  # Use specific voice"
    echo "  ./launch.sh --tts --cpu-only # TTS with CPU-only PyTorch"
    echo "  ./launch.sh --clean --cpu-only # Clean install with CPU-only PyTorch"
    echo "  ./launch.sh --download-models # Pre-download models"
    echo "  ./launch.sh --migrate-db     # Run database migrations"
    echo "  ./launch.sh --dev            # Dev server with hot reload"
}

check_dependencies() {
    echo -e "${CYAN}🔍 Checking system dependencies...${NC}"
    
    # Detect platform
    case "$(uname -s)" in
        Darwin*)    PLATFORM="macos" ;;
        Linux*)     PLATFORM="linux" ;;
        MINGW*|CYGWIN*) PLATFORM="windows" ;;
        *)          echo -e "${RED}Unsupported platform: $(uname -s)${NC}"; exit 1 ;;
    esac

    # Check for Homebrew on macOS with timeout
    if [[ "$PLATFORM" = "macos" ]] && ! command -v brew &> /dev/null; then
        echo -e "${YELLOW}Installing Homebrew...${NC}"
        
        if ! run_with_timeout "${NETWORK_TIMEOUT}s" /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; then
            echo -e "${RED}Failed to install Homebrew (timeout or error)${NC}"
            echo -e "${YELLOW}Please install Homebrew manually: https://brew.sh${NC}"
            exit 1
        fi
    fi

    # Verify Python 3.11 with timeout
    if ! run_with_timeout "10s" python3.11 --version &> /dev/null; then
        echo -e "${RED}Python 3.11 not found!${NC}"
        echo -e "${YELLOW}Installing via package manager...${NC}"
        
        case "$PLATFORM" in
            "macos")
                if ! run_with_timeout "${NETWORK_TIMEOUT}s" brew install python@3.11; then
                    echo -e "${RED}Failed to install Python 3.11${NC}"
                    exit 1
                fi
                ;;
            "linux")
                if command -v apt-get >/dev/null 2>&1; then
                    if ! run_with_timeout "${NETWORK_TIMEOUT}s" sudo apt-get update && \
                       run_with_timeout "${NETWORK_TIMEOUT}s" sudo apt-get install -y python3.11 python3.11-venv; then
                        echo -e "${RED}Failed to install Python 3.11${NC}"
                        exit 1
                    fi
                else
                    echo -e "${RED}Please install Python 3.11 manually${NC}"
                    exit 1
                fi
                ;;
        esac
    fi

    # Check TTS dependencies if requested
    if [[ "${TTS:-false}" = true ]]; then
        if ! command -v ffmpeg &> /dev/null; then
            echo -e "${YELLOW}Installing ffmpeg...${NC}"
            case "$PLATFORM" in
                "macos")
                    if ! run_with_timeout "${NETWORK_TIMEOUT}s" brew install ffmpeg; then
                        echo -e "${RED}Failed to install ffmpeg${NC}"
                        exit 1
                    fi
                    ;;
                "linux")
                    if command -v apt-get >/dev/null 2>&1; then
                        if ! run_with_timeout "${NETWORK_TIMEOUT}s" sudo apt-get install -y ffmpeg; then
                            echo -e "${RED}Failed to install ffmpeg${NC}"
                            exit 1
                        fi
                    fi
                    ;;
                "windows")
                    echo -e "${RED}Please install ffmpeg from https://ffmpeg.org/download.html${NC}"
                    exit 1
                    ;;
            esac
        fi
    fi
}

install_requirements() {
    echo -e "${CYAN}📦 Installing Python requirements...${NC}"
    
    # Configure HF home directory and disable warnings
    export HF_HOME="${PWD}/.hf_cache"
    export HF_HUB_DISABLE_PROGRESS_BARS=1
    export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
    
    # Validate requirements file exists
    if [[ ! -f "requirements.txt" ]]; then
        echo -e "${RED}requirements.txt not found!${NC}"
        exit 1
    fi
    
    # Ensure we're in the virtual environment
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${RED}Virtual environment not found!${NC}"
        exit 1
    fi
    
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate" || {
        echo -e "${RED}Failed to activate virtual environment${NC}"
        exit 1
    }
    
    # Install base requirements with timeout
    if ! run_with_timeout "${NETWORK_TIMEOUT}s" pip install -r requirements.txt; then
        echo -e "${RED}Failed to install base requirements${NC}"
        exit 1
    fi
    
    if [[ "${TTS:-false}" = true ]]; then
        echo -e "${MUSIC} Installing TTS dependencies...${NC}"
        
        if [[ ! -f "requirements-tts.txt" ]]; then
            echo -e "${RED}requirements-tts.txt not found!${NC}"
            exit 1
        fi
        
        prepare_tts_directories || {
            echo -e "${RED}Failed to prepare TTS directories${NC}"
            exit 1
        }
        
        # First, install PyTorch with correct version based on CPU_ONLY flag
        # This must be done BEFORE installing requirements-tts.txt to avoid CUDA installation
        if [[ "${CPU_ONLY:-false}" = true ]]; then
            echo -e "${YELLOW}🖥️ Installing PyTorch CPU-only version (forced)...${NC}"
            if ! run_with_timeout "${NETWORK_TIMEOUT}s" pip install torch==2.3.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cpu; then
                echo -e "${RED}Failed to install PyTorch CPU version${NC}"
                exit 1
            fi
        else
            case "$PLATFORM" in
                "macos")
                    echo "Ensuring PyTorch for macOS (CPU)..."
                    if ! run_with_timeout "${NETWORK_TIMEOUT}s" pip install torch==2.3.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cpu; then
                        echo -e "${RED}Failed to install PyTorch for macOS${NC}"
                        exit 1
                    fi
                    ;;
                "linux"|"windows")
                    if command -v nvidia-smi &> /dev/null; then
                        echo "Ensuring PyTorch with CUDA support..."
                        if ! run_with_timeout "${NETWORK_TIMEOUT}s" pip install torch==2.3.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cu121; then
                            echo -e "${RED}Failed to install PyTorch with CUDA${NC}"
                            exit 1
                        fi
                    else
                        echo "Ensuring PyTorch CPU version..."
                        if ! run_with_timeout "${NETWORK_TIMEOUT}s" pip install torch==2.3.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cpu; then
                            echo -e "${RED}Failed to install PyTorch CPU${NC}"
                            exit 1
                        fi
                    fi
                    ;;
            esac
        fi
        
        # Verify PyTorch installation
        echo -e "${GREEN}✅ PyTorch installed. Checking version and device support...${NC}"
        python -c "import torch; print(f'PyTorch version: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}'); print(f'Device: {\"CUDA\" if torch.cuda.is_available() else \"CPU\"}')" || echo -e "${YELLOW}Warning: Could not verify PyTorch installation${NC}"
        
        echo -e "${CYAN}Installing remaining TTS dependencies from requirements-tts.txt...${NC}"
        if ! run_with_timeout "${NETWORK_TIMEOUT}s" pip install -r requirements-tts.txt --no-cache-dir; then
            echo -e "${RED}Failed to install TTS requirements${NC}"
            exit 1
        fi
        
        # Download required NLTK resources with error handling
        echo -e "${CYAN}📦 Downloading NLTK resources for TTS...${NC}"
        if ! python -c "import nltk; nltk.download('punkt', quiet=True)"; then
            echo -e "${YELLOW}Warning: Failed to download NLTK resources${NC}"
        fi
        
        check_tts_models
        
        # Download voice presets
        download_voice_presets || {
            echo -e "${YELLOW}Warning: Some voice presets failed to download${NC}"
        }
    fi
    
    # Create a marker file to indicate dependencies were installed
    if ! touch .deps_installed; then
        echo -e "${YELLOW}Warning: Could not create dependency marker file${NC}"
    fi
}

clean_install() {
    echo -e "${RED}♻️  Nuclear Option: Fresh Install${NC}"
    
    # Remove existing virtual environment
    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR" || {
            echo -e "${RED}Failed to remove existing virtual environment${NC}"
            exit 1
        }
    fi
    
    # Create new virtual environment
    if ! python3.11 -m venv "$VENV_DIR"; then
        echo -e "${RED}Failed to create virtual environment${NC}"
        exit 1
    fi
    
    # Activate virtual environment
    # shellcheck disable=SC1091
    if ! source "$VENV_DIR/bin/activate"; then
        echo -e "${RED}Failed to activate virtual environment${NC}"
        exit 1
    fi
    
    check_dependencies
    install_requirements
    
    # Run database migrations
    if ! run_database_migrations; then
        echo -e "${RED}Database migration failed during clean install${NC}"
        exit 1
    fi
    
    export CLEAN_INSTALL_DONE=true
    echo -e "${GREEN}✅ Fresh environment created!${NC}"
}

run_database_migrations() {
    echo -e "${CYAN}🗄️ Running database migrations...${NC}"
    
    # Ensure we're in the virtual environment
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${RED}Virtual environment not found!${NC}"
        return 1
    fi
    
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate" || {
        echo -e "${RED}Failed to activate virtual environment${NC}"
        return 1
    }
    
    # Determine which database file to use (check what ansv.py uses)
    local db_file="messages.db"
    if [[ -f "ansv_bot.db" ]] && [[ ! -f "messages.db" ]]; then
        db_file="ansv_bot.db"
    fi
    
    # Check if we need to migrate to user system
    if [[ ! -f "$db_file" ]] || ! python -c "
import sqlite3
import sys
try:
    conn = sqlite3.connect('$db_file')
    cursor = conn.cursor()
    cursor.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='users'\")
    result = cursor.fetchone()
    conn.close()
    sys.exit(0 if result else 1)
except:
    sys.exit(1)
" 2>/dev/null; then
        echo -e "${YELLOW}User management system not found. Running migration...${NC}"
        
        # Run user migration
        if ! python utils/migrate_to_users.py --db "$db_file"; then
            echo -e "${RED}Failed to migrate user system${NC}"
            return 1
        fi
        
        echo -e "${GREEN}✅ User migration completed${NC}"
    else
        echo -e "${GREEN}✅ User management system already exists${NC}"
    fi
    
    # Verify migration was successful
    if ! python utils/migrate_to_users.py --verify-only --db "$db_file"; then
        echo -e "${RED}Migration verification failed${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✅ Database migrations completed successfully${NC}"
    return 0
}

prepare_tts_directories() {
    echo -e "${CYAN}📂 Setting up TTS directories...${NC}"
    
    # Create main TTS directories with error checking
    for dir in "$TTS_MODELS_DIR" "$TTS_OUTPUT_DIR" "$TTS_VOICES_DIR"; do
        if ! mkdir -p "$dir"; then
            echo -e "${RED}Failed to create directory: $dir${NC}"
            return 1
        fi
    done
    
    # Create channel-specific output directories from settings
    if [[ -f "$CONFIG_FILE" ]]; then
        local channels_line
        channels_line=$(grep "^channels[[:space:]]*=" "$CONFIG_FILE" | head -n 1)
        if [[ -n "$channels_line" ]]; then
            local channels_value
            channels_value=$(echo "$channels_line" | cut -d'=' -f2- | tr -d ' ')
            IFS=',' read -ra channel_array <<< "$channels_value"
            for channel in "${channel_array[@]}"; do
                # Validate channel name and create directory
                if [[ -n "$channel" ]] && validate_username "$channel" 2>/dev/null; then
                    if ! mkdir -p "$TTS_OUTPUT_DIR/$channel"; then
                        echo -e "${YELLOW}Warning: Failed to create directory for channel: $channel${NC}"
                    fi
                elif [[ -n "$channel" ]]; then
                    echo -e "${YELLOW}Warning: Invalid channel name: $channel${NC}"
                fi
            done
        fi
    fi
    
    return 0
}

check_tts_models() {
    echo -e "${CYAN}🔍 Checking TTS models...${NC}"
    
    # Validate HF_HOME is set
    if [[ -z "${HF_HOME:-}" ]]; then
        echo -e "${YELLOW}HF_HOME not set, using default cache${NC}"
        export HF_HOME="${PWD}/.hf_cache"
    fi
    
    # Basic presence check
    if [[ ! -d "$HF_HOME/hub" ]]; then
        echo -e "${YELLOW}⚠️ No Hugging Face models found. First run will download models.${NC}"
        echo -e "${YELLOW}   This may take a while (1-2GB). Get some coffee! ☕${NC}"
    else
        local model_count
        model_count=$(find "$HF_HOME/hub" -type d -name "snapshots" 2>/dev/null | wc -l)
        echo -e "${GREEN}✅ Found $model_count cached model(s)${NC}"
    fi
}

download_voice_preset() {
    local preset="$1"
    
    # Validate preset parameter
    if [[ -z "$preset" ]]; then
        echo -e "${RED}Error: No preset specified${NC}"
        return 1
    fi
    
    # Validate preset name for security
    if ! validate_voice_preset "$preset" 2>/dev/null && [[ ! "$preset" =~ ^v2/en_speaker_[0-9]$ ]]; then
        echo -e "${YELLOW}Warning: Potentially invalid preset: $preset${NC}"
    fi
    
    echo -e "${CYAN}🎤 Downloading/caching voice preset: $preset...${NC}"
    
    # Use external Python script instead of embedded code
    if [[ -f "${SCRIPT_DIR}/scripts/download_voice_preset.py" ]]; then
        if python "${SCRIPT_DIR}/scripts/download_voice_preset.py" "$preset" --cache-dir "$TTS_MODELS_DIR"; then
            echo -e "${GREEN}Voice preset $preset cached successfully!${NC}"
            return 0
        else
            echo -e "${RED}Failed to cache voice preset $preset${NC}"
            return 1
        fi
    else
        echo -e "${YELLOW}Download script not found, using fallback method${NC}"
        # Fallback to embedded Python (for backward compatibility)
        python -c "
import os
from transformers import AutoProcessor, BarkModel
import torch

# Suppress most Bark warnings
os.environ['SUNO_ENABLE_MPS'] = 'False'
os.environ['SUNO_OFFLOAD_CPU'] = 'True'

try:
    processor = AutoProcessor.from_pretrained('suno/bark-small', cache_dir='$TTS_MODELS_DIR')
    model = BarkModel.from_pretrained('suno/bark-small', cache_dir='$TTS_MODELS_DIR')
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = model.to(device)

    text_prompt = 'Hello.'
    inputs = processor(text_prompt, voice_preset='$preset', return_tensors='pt').to(device)
    
    with torch.no_grad():
        speech_values = model.generate(**inputs, do_sample=True, min_eos_p=0.2, max_new_tokens=16)
    
    print(f'Voice preset $preset cached successfully!')
except Exception as e:
    print(f'Failed to cache voice preset $preset: {e}')" || {
            echo -e "${RED}Python script failed for preset $preset${NC}"
            return 1
        }
    fi
}

# Enhanced voice presets download with error handling
download_voice_presets() {
    echo -e "${CYAN}🎤 Downloading standard voice presets...${NC}"
    
    local presets=("v2/en_speaker_0" "v2/en_speaker_1" "v2/en_speaker_5" "v2/en_speaker_6" "v2/en_speaker_9")
    local failed_count=0
    
    for preset in "${presets[@]}"; do
        if ! download_voice_preset "$preset"; then
            ((failed_count++))
            echo -e "${YELLOW}Warning: Failed to download preset $preset${NC}"
        fi
    done
    
    if [[ $failed_count -gt 0 ]]; then
        echo -e "${YELLOW}Warning: $failed_count voice presets failed to download${NC}"
        return 1
    else
        echo -e "${GREEN}All voice presets downloaded successfully!${NC}"
        return 0
    fi
}

# Enhanced runtime execution with proper error handling
main() {
    # Check for help flag first, before any other processing
    for arg in "$@"; do
        if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
            show_help
            exit 0
        fi
    done
    
    # Set up error handling
    trap 'echo -e "\n${RED}Script interrupted${NC}"; exit 130' INT
    trap 'echo -e "\n${RED}Script terminated${NC}"; exit 143' TERM
    
    print_banner
    
    # Check prerequisites with error handling
    check_venv || exit 1
    check_config || exit 1
    
    # Run first-time setup wizard if needed
    if [[ ! -f "$CONFIG_FILE" ]]; then
        first_run_wizard || {
            echo -e "${RED}Configuration setup failed${NC}"
            exit 1
        }
    fi
    
    check_security || exit 1
    
    # Check if arguments were provided
    if [[ $# -eq 0 ]]; then
        # No arguments, show interactive menu
        interactive_menu
    else
        # Parse command line arguments
        parse_arguments "$@"
    fi
}

# Call main function with all arguments
main "$@"
