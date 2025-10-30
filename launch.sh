#!/bin/bash
set -euo pipefail

# ANSV Bot - Service Management Script
# For production deployment and development

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
EXAMPLE_CONFIG="settings.example.conf"
PID_FILE=".ansv.pid"
LOG_DIR="logs"
BACKUP_DIR="backups"

# Detect Python version
detect_python() {
    for version in python3.11 python3.10 python3.9 python3.8 python3; do
        if command -v "$version" &> /dev/null; then
            PYTHON_CMD="$version"
            PYTHON_VERSION=$("$version" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
            return 0
        fi
    done
    echo -e "${RED}Error: Python 3.8+ required${NC}"
    echo -e "${YELLOW}Install Python 3.8 or higher${NC}"
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
    echo -e "${BLUE}Service Management v2.0${NC}\n"
}

# Help message
show_help() {
    print_banner
    echo -e "${YELLOW}Usage:${NC} ./launch.sh <command> [options]"
    echo ""
    echo -e "${CYAN}Service Management:${NC}"
    echo "  start             Start the service (bot + web)"
    echo "  stop              Stop the service gracefully"
    echo "  restart           Restart the service"
    echo "  status            Show service status and health"
    echo "  logs [lines]      View logs (default: last 50 lines)"
    echo ""
    echo -e "${CYAN}Deployment:${NC}"
    echo "  deploy            Pull updates, migrate, restart"
    echo "  migrate           Run database migrations"
    echo "  setup             Initial installation (first time only)"
    echo ""
    echo -e "${CYAN}Maintenance:${NC}"
    echo "  backup            Backup databases and config"
    echo "  restore <file>    Restore from backup"
    echo "  clean             Clean reinstall (wipes data!)"
    echo "  check             Verify system dependencies"
    echo "  update-deps       Update Python dependencies"
    echo ""
    echo -e "${CYAN}Development:${NC}"
    echo "  dev               Start in development mode (hot reload)"
    echo "  shell             Open Python shell with app context"
    echo "  test              Run test suite"
    echo ""
    echo -e "${CYAN}Options:${NC}"
    echo "  --verbose         Show detailed output"
    echo "  --force           Skip confirmations"
    echo "  --help, -h        Show this help"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  ./launch.sh start              # Start the service"
    echo "  ./launch.sh logs 100           # View last 100 log lines"
    echo "  ./launch.sh deploy             # Deploy updates"
    echo "  ./launch.sh backup             # Create backup"
    echo "  ./launch.sh dev                # Development mode"
}

# Check if service is running
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

# Get service PID
get_pid() {
    if [[ -f "$PID_FILE" ]]; then
        cat "$PID_FILE"
    fi
}

# Start service
cmd_start() {
    echo -e "${CYAN}Starting ANSV Bot Service...${NC}"

    if is_running; then
        echo -e "${YELLOW}Service already running (PID: $(get_pid))${NC}"
        exit 0
    fi

    # Verify setup
    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${RED}Virtual environment not found${NC}"
        echo -e "${YELLOW}Run: ./launch.sh setup${NC}"
        exit 1
    fi

    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo -e "${RED}Configuration file not found${NC}"
        echo -e "${YELLOW}Run: ./launch.sh setup${NC}"
        exit 1
    fi

    # Create log directory
    mkdir -p "$LOG_DIR"

    # Activate virtual environment
    source "$VENV_DIR/bin/activate"

    # Run migrations
    echo -e "${CYAN}Running database migrations...${NC}"
    run_migrations

    # Start the service
    echo -e "${GREEN}Starting service...${NC}"
    export HF_HOME="${PWD}/.hf_cache"
    export HF_HUB_DISABLE_IMPLICIT_TOKEN=1

    # Start both bot and web in background
    nohup python ansv.py --web > "$LOG_DIR/ansv.log" 2>&1 &
    echo $! > "$PID_FILE"

    sleep 2

    if is_running; then
        echo -e "${GREEN}✓ Service started successfully (PID: $(get_pid))${NC}"
        echo -e "${BLUE}Logs: $LOG_DIR/ansv.log${NC}"
        echo -e "${BLUE}Web interface: http://localhost:5001${NC}"
    else
        echo -e "${RED}Failed to start service${NC}"
        echo -e "${YELLOW}Check logs: tail -f $LOG_DIR/ansv.log${NC}"
        exit 1
    fi
}

# Stop service
cmd_stop() {
    echo -e "${CYAN}Stopping ANSV Bot Service...${NC}"

    if ! is_running; then
        echo -e "${YELLOW}Service not running${NC}"
        exit 0
    fi

    local pid=$(get_pid)
    echo -e "${YELLOW}Stopping process $pid...${NC}"

    # Send SIGTERM
    kill -TERM "$pid" 2>/dev/null || true

    # Wait for graceful shutdown
    local count=0
    while kill -0 "$pid" 2>/dev/null && [ $count -lt 10 ]; do
        sleep 1
        count=$((count + 1))
    done

    # Force kill if still running
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${YELLOW}Force stopping...${NC}"
        kill -KILL "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    echo -e "${GREEN}✓ Service stopped${NC}"
}

# Restart service
cmd_restart() {
    echo -e "${CYAN}Restarting ANSV Bot Service...${NC}"
    cmd_stop
    sleep 2
    cmd_start
}

# Service status
cmd_status() {
    print_banner

    echo -e "${CYAN}=== Service Status ===${NC}"
    if is_running; then
        local pid=$(get_pid)
        echo -e "${GREEN}Status: Running${NC}"
        echo -e "PID: $pid"
        echo -e "Uptime: $(ps -p $pid -o etime= 2>/dev/null | tr -d ' ' || echo 'N/A')"
    else
        echo -e "${RED}Status: Stopped${NC}"
    fi

    echo -e "\n${CYAN}=== System Info ===${NC}"
    echo -e "Python: $PYTHON_VERSION"
    echo -e "Virtual Env: $([ -d "$VENV_DIR" ] && echo "✓ Present" || echo "✗ Missing")"
    echo -e "Config: $([ -f "$CONFIG_FILE" ] && echo "✓ Present" || echo "✗ Missing")"

    if [[ -f "users.db" ]]; then
        source "$VENV_DIR/bin/activate" 2>/dev/null || true
        local user_count=$(python -c "import sqlite3; print(sqlite3.connect('users.db').execute('SELECT COUNT(*) FROM users').fetchone()[0])" 2>/dev/null || echo "N/A")
        echo -e "Users: $user_count"
    fi

    if [[ -f "messages.db" ]]; then
        local msg_count=$(python -c "import sqlite3; print(sqlite3.connect('messages.db').execute('SELECT COUNT(*) FROM messages').fetchone()[0])" 2>/dev/null || echo "N/A")
        echo -e "Messages: $msg_count"
    fi

    echo -e "\n${CYAN}=== Disk Usage ===${NC}"
    du -sh . 2>/dev/null | cut -f1 || echo "N/A"

    if [[ -f "$LOG_DIR/ansv.log" ]]; then
        echo -e "\n${CYAN}=== Recent Logs ===${NC}"
        tail -n 10 "$LOG_DIR/ansv.log" 2>/dev/null || echo "No logs available"
    fi
}

# View logs
cmd_logs() {
    local lines=${1:-50}

    if [[ ! -f "$LOG_DIR/ansv.log" ]]; then
        echo -e "${YELLOW}No log file found${NC}"
        exit 0
    fi

    echo -e "${CYAN}=== Last $lines lines ===${NC}\n"
    tail -n "$lines" "$LOG_DIR/ansv.log"

    # Offer to tail -f
    echo -e "\n${YELLOW}Press 'f' to follow logs, any other key to exit${NC}"
    read -n 1 -s key
    if [[ "$key" == "f" ]]; then
        tail -f "$LOG_DIR/ansv.log"
    fi
}

# Deploy updates
cmd_deploy() {
    echo -e "${CYAN}=== Deploying Updates ===${NC}\n"

    # Stop service if running
    if is_running; then
        echo -e "${YELLOW}Stopping service...${NC}"
        cmd_stop
    fi

    # Pull latest code
    echo -e "${CYAN}Pulling latest code...${NC}"
    if [[ -d ".git" ]]; then
        git pull || {
            echo -e "${RED}Git pull failed${NC}"
            exit 1
        }
    else
        echo -e "${YELLOW}Not a git repository, skipping pull${NC}"
    fi

    # Update dependencies
    echo -e "${CYAN}Updating dependencies...${NC}"
    cmd_update_deps

    # Run migrations
    echo -e "${CYAN}Running migrations...${NC}"
    run_migrations

    # Start service
    echo -e "${CYAN}Starting service...${NC}"
    cmd_start

    echo -e "\n${GREEN}✓ Deployment complete${NC}"
}

# Database migrations
run_migrations() {
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo -e "${RED}Virtual environment not found${NC}"
        return 1
    fi

    source "$VENV_DIR/bin/activate"

    # Check if migration script exists
    if [[ -f "utils/migrate_to_users.py" ]]; then
        python utils/migrate_to_users.py --db messages.db || {
            echo -e "${YELLOW}Migration warning (may be normal if already migrated)${NC}"
        }
    fi

    echo -e "${GREEN}✓ Migrations complete${NC}"
}

cmd_migrate() {
    echo -e "${CYAN}Running database migrations...${NC}"
    run_migrations
}

# Initial setup
cmd_setup() {
    print_banner
    echo -e "${CYAN}=== Initial Setup ===${NC}\n"

    # Check if already setup
    if [[ -d "$VENV_DIR" ]] && [[ -f "$CONFIG_FILE" ]]; then
        echo -e "${YELLOW}Already configured${NC}"
        echo -e "Use ${CYAN}./launch.sh clean${NC} for fresh install"
        exit 0
    fi

    # Install system dependencies
    echo -e "${CYAN}Checking system dependencies...${NC}"
    check_system_deps

    # Create virtual environment
    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${CYAN}Creating virtual environment...${NC}"
        "$PYTHON_CMD" -m venv "$VENV_DIR" || {
            echo -e "${RED}Failed to create virtual environment${NC}"
            exit 1
        }
    fi

    source "$VENV_DIR/bin/activate"

    # Install Python dependencies
    echo -e "${CYAN}Installing Python dependencies...${NC}"
    pip install --upgrade pip
    pip install -r requirements.txt || {
        echo -e "${RED}Failed to install dependencies${NC}"
        exit 1
    }

    # Install TTS dependencies if available
    if [[ -f "requirements-tts.txt" ]]; then
        echo -e "${CYAN}Installing TTS dependencies...${NC}"
        pip install -r requirements-tts.txt || {
            echo -e "${YELLOW}Warning: Failed to install TTS dependencies${NC}"
            echo -e "${YELLOW}TTS features may not work${NC}"
        }
    fi

    # Create config from example
    if [[ ! -f "$CONFIG_FILE" ]] && [[ -f "$EXAMPLE_CONFIG" ]]; then
        echo -e "${CYAN}Creating configuration file...${NC}"
        cp "$EXAMPLE_CONFIG" "$CONFIG_FILE"
        chmod 600 "$CONFIG_FILE"
    fi

    # Create directories
    mkdir -p "$LOG_DIR" "$BACKUP_DIR" static/outputs models/tts voices

    # Run migrations
    run_migrations

    echo -e "\n${GREEN}✓ Setup complete!${NC}\n"
    echo -e "${YELLOW}Next steps:${NC}"
    echo -e "1. Edit ${CYAN}$CONFIG_FILE${NC} with your credentials"
    echo -e "2. Get Twitch credentials: ${BLUE}https://dev.twitch.tv/console${NC}"
    echo -e "3. Configure Stripe keys for payments"
    echo -e "4. Run: ${CYAN}./launch.sh start${NC}"
}

# Backup
cmd_backup() {
    echo -e "${CYAN}Creating backup...${NC}"

    mkdir -p "$BACKUP_DIR"
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_file="$BACKUP_DIR/ansv_backup_$timestamp.tar.gz"

    tar -czf "$backup_file" \
        messages.db users.db "$CONFIG_FILE" cache/ 2>/dev/null || {
        echo -e "${YELLOW}Some files may be missing, continuing...${NC}"
    }

    echo -e "${GREEN}✓ Backup created: $backup_file${NC}"
    echo -e "${BLUE}Size: $(du -sh "$backup_file" | cut -f1)${NC}"
}

# Restore
cmd_restore() {
    local backup_file="$1"

    if [[ -z "$backup_file" ]]; then
        echo -e "${RED}Usage: ./launch.sh restore <backup_file>${NC}"
        echo -e "\nAvailable backups:"
        ls -lh "$BACKUP_DIR"/*.tar.gz 2>/dev/null || echo "No backups found"
        exit 1
    fi

    if [[ ! -f "$backup_file" ]]; then
        echo -e "${RED}Backup file not found: $backup_file${NC}"
        exit 1
    fi

    echo -e "${YELLOW}This will overwrite current data!${NC}"
    read -p "Continue? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Cancelled"
        exit 0
    fi

    if is_running; then
        cmd_stop
    fi

    echo -e "${CYAN}Restoring from backup...${NC}"
    tar -xzf "$backup_file" || {
        echo -e "${RED}Failed to restore backup${NC}"
        exit 1
    }

    echo -e "${GREEN}✓ Restore complete${NC}"
}

# Clean install
cmd_clean() {
    echo -e "${RED}WARNING: This will delete all data!${NC}"
    read -p "Type 'DELETE' to confirm: " confirm
    if [[ "$confirm" != "DELETE" ]]; then
        echo "Cancelled"
        exit 0
    fi

    if is_running; then
        cmd_stop
    fi

    echo -e "${CYAN}Removing old installation...${NC}"
    rm -rf "$VENV_DIR" messages.db users.db cache/ "$LOG_DIR" .deps_installed

    cmd_setup
}

# Check dependencies
check_system_deps() {
    local missing=()

    # Check Python
    if ! command -v "$PYTHON_CMD" &> /dev/null; then
        missing+=("python3")
    fi

    # Check git
    if ! command -v git &> /dev/null; then
        missing+=("git")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo -e "${RED}Missing dependencies: ${missing[*]}${NC}"
        echo -e "${YELLOW}Install them using your package manager${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ System dependencies OK${NC}"
}

cmd_check() {
    echo -e "${CYAN}Checking system...${NC}\n"
    check_system_deps

    if [[ -d "$VENV_DIR" ]]; then
        echo -e "${GREEN}✓ Virtual environment${NC}"
    else
        echo -e "${RED}✗ Virtual environment${NC}"
    fi

    if [[ -f "$CONFIG_FILE" ]]; then
        echo -e "${GREEN}✓ Configuration file${NC}"
    else
        echo -e "${RED}✗ Configuration file${NC}"
    fi

    echo ""
}

# Update dependencies
cmd_update_deps() {
    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${RED}Virtual environment not found${NC}"
        exit 1
    fi

    source "$VENV_DIR/bin/activate"

    echo -e "${CYAN}Updating Python dependencies...${NC}"
    pip install --upgrade pip
    pip install -r requirements.txt --upgrade

    # Update TTS dependencies if available
    if [[ -f "requirements-tts.txt" ]]; then
        echo -e "${CYAN}Updating TTS dependencies...${NC}"
        pip install -r requirements-tts.txt --upgrade || {
            echo -e "${YELLOW}Warning: Failed to update TTS dependencies${NC}"
        }
    fi

    echo -e "${GREEN}✓ Dependencies updated${NC}"
}

# Development mode
cmd_dev() {
    echo -e "${CYAN}Starting development server...${NC}"

    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${RED}Run ./launch.sh setup first${NC}"
        exit 1
    fi

    source "$VENV_DIR/bin/activate"
    export FLASK_ENV=development
    export FLASK_DEBUG=1

    # Run migrations
    run_migrations

    echo -e "${GREEN}Starting in development mode...${NC}"
    echo -e "${BLUE}Web: http://localhost:5001${NC}"
    python ansv.py --web --verbose
}

# Python shell
cmd_shell() {
    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${RED}Run ./launch.sh setup first${NC}"
        exit 1
    fi

    source "$VENV_DIR/bin/activate"
    echo -e "${CYAN}Opening Python shell...${NC}"
    python
}

# Test suite
cmd_test() {
    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${RED}Run ./launch.sh setup first${NC}"
        exit 1
    fi

    source "$VENV_DIR/bin/activate"

    if [[ -f "pytest" ]] || pip show pytest &>/dev/null; then
        echo -e "${CYAN}Running tests...${NC}"
        pytest
    else
        echo -e "${YELLOW}pytest not installed${NC}"
        echo -e "Install: pip install pytest"
    fi
}

# Main
main() {
    local command="${1:-}"

    case "$command" in
        start) cmd_start ;;
        stop) cmd_stop ;;
        restart) cmd_restart ;;
        status) cmd_status ;;
        logs) cmd_logs "${2:-50}" ;;
        deploy) cmd_deploy ;;
        migrate) cmd_migrate ;;
        setup) cmd_setup ;;
        backup) cmd_backup ;;
        restore) cmd_restore "$2" ;;
        clean) cmd_clean ;;
        check) cmd_check ;;
        update-deps) cmd_update_deps ;;
        dev) cmd_dev ;;
        shell) cmd_shell ;;
        test) cmd_test ;;
        --help|-h|help|"") show_help ;;
        *)
            echo -e "${RED}Unknown command: $command${NC}"
            echo -e "Run ${CYAN}./launch.sh --help${NC} for usage"
            exit 1
            ;;
    esac
}

main "$@"
