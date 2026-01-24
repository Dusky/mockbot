#!/bin/bash
set -euo pipefail

# Mockbot - Service Management Script

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
VENV_DIR=".venv"
CONFIG_FILE="settings.conf"
PID_FILE="bot.pid"
LOG_DIR="logs"

# Detect Python
detect_python() {
    for version in python3.11 python3.10 python3.9 python3.8 python3; do
        if command -v "$version" &> /dev/null; then
            PYTHON_CMD="$version"
            return 0
        fi
    done
    echo -e "${RED}Error: Python 3.8+ required${NC}"
    exit 1
}

detect_python

# Banner
print_banner() {
    echo -e "${CYAN}"
    echo "   █████╗ ███╗   ██╗███████╗██╗   ██╗"
    echo "  ██╔══██╗████╗  ██║██╔════╝██║   ██║"
    echo "  ███████║██╔██╗ ██║███████╗██║   ██║"
    echo "  ██╔══██║██║╚██╗██║╚════██║██║   ██║"
    echo "  ██║  ██║██║ ╚████║███████║╚██████╔╝"
    echo "  ╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝ ╚═════╝ "
    echo -e "${NC}"
    echo -e "${BLUE}Mockbot CLI v2.0${NC}\n"
}

# Help
show_help() {
    print_banner
    echo -e "${YELLOW}Usage:${NC} ./launch.sh <command>"
    echo ""
    echo -e "${CYAN}Commands:${NC}"
    echo "  start             Start the bot in background"
    echo "  stop              Stop the bot"
    echo "  restart           Restart the bot"
    echo "  status            Check if running"
    echo "  cli [tts]         Start interactive CLI (add 'tts' to enable voice)"
    echo "  tui [tts]         Start TUI Mode (add 'tts' to enable voice)"
    echo "  logs [lines]      View logs"
    echo "  setup-tts         Install TTS dependencies"
    echo "  clean             Remove temporary files"
    echo ""
}

# Check running
is_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        else
            rm -f "$PID_FILE"
            return 1
        fi
    fi
    return 1
}

# Start
cmd_start() {
    echo -e "${CYAN}Starting Mockbot...${NC}"
    if is_running; then
        echo -e "${YELLOW}Already running (PID: $(cat $PID_FILE))${NC}"
        exit 0
    fi

    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${YELLOW}Creating virtual environment...${NC}"
        $PYTHON_CMD -m venv "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
        pip install --upgrade pip
        if [[ -f "requirements.txt" ]]; then
            pip install -r requirements.txt
        fi
    else
        source "$VENV_DIR/bin/activate"
    fi

    local tts_flag=""
    
    nohup python main.py --tts > "$LOG_DIR/mockbot.log" 2>&1 &
    echo $! > "$PID_FILE"
    echo -e "${GREEN}✓ Bot started (PID: $(cat $PID_FILE))${NC}"
    echo -e "${BLUE}Logs: $LOG_DIR/mockbot.log${NC}"
}

# Stop
cmd_stop() {
    if ! is_running; then
        echo -e "${YELLOW}Not running${NC}"
        exit 0
    fi
    local pid=$(cat "$PID_FILE")
    echo -e "${YELLOW}Stopping $pid...${NC}"
    kill -TERM "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo -e "${GREEN}Stopped${NC}"
}

# Rest of commands
cmd_restart() {
    cmd_stop
    sleep 2
    cmd_start
}

cmd_status() {
    if is_running; then
        echo -e "${GREEN}Running (PID: $(cat $PID_FILE))${NC}"
    else
        echo -e "${RED}Stopped${NC}"
    fi
}

cmd_logs() {
    local lines="${1:-50}"
    tail -n "$lines" -f "$LOG_DIR/mockbot.log"
}

cmd_cli() {
    # echo -e "${CYAN}Starting CLI Mode...${NC}"
    source "$VENV_DIR/bin/activate" 2>/dev/null || true
    
    local use_tts=false
    local args=()
    
    for arg in "$@"; do
        if [[ "$arg" == "tts" || "$arg" == "--tts" ]]; then
            use_tts=true
        else
            args+=("$arg")
        fi
    done
    
    if [ "$use_tts" = true ]; then
        echo -e "${GREEN}🔊 TTS Enabled${NC}"
        python main.py --tts "${args[@]}"
    else
        echo -e "${CYAN}🔇 TTS Disabled (add 'tts' to enable)${NC}"
        python main.py "${args[@]}"
    fi
}

cmd_setup_tts() {
    echo -e "${CYAN}Installing TTS dependencies...${NC}"
    source "$VENV_DIR/bin/activate" 2>/dev/null || true
    if [[ -f "requirements-tts.txt" ]]; then
        pip install -r requirements-tts.txt
        echo -e "${GREEN}✓ TTS dependencies installed${NC}"
    else
        echo -e "${RED}requirements-tts.txt not found${NC}"
    fi
}

cmd_tui() {
    echo -e "${CYAN}Starting TUI Mode...${NC}"
    source "$VENV_DIR/bin/activate" 2>/dev/null || true
    
    local use_tts=false
    local args=()
    
    # Parse args similar to CLI (allow 'tts' without dashes)
    for arg in "$@"; do
        if [[ "$arg" == "tts" || "$arg" == "--tts" ]]; then
            use_tts=true
        else
            args+=("$arg")
        fi
    done
    
    if [ "$use_tts" = true ]; then
        echo -e "${GREEN}🔊 TUI + TTS Enabled${NC}"
        python main.py --tui --tts "${args[@]}"
    else
        python main.py --tui "${args[@]}"
    fi
}

cmd_clean() {
    echo -e "${YELLOW}Cleaning temp files...${NC}"
    rm -f *.log *.pid bot_heartbeat.json
    find . -name "__pycache__" -type d -exec rm -rf {} +
    echo -e "${GREEN}Cleaned${NC}"
}

# Main
case "${1:-}" in
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_restart ;;
    status) cmd_status ;;
    cli) cmd_cli "${@:2}" ;; # Pass remaining args
    tui) cmd_tui "${@:2}" ;; 
    logs) cmd_logs "${2:-50}" ;;
    setup-tts) cmd_setup_tts ;;
    clean) cmd_clean ;;
    *) show_help ;;
esac
