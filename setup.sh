#!/usr/bin/env bash
# ============================================================
# Daily News Agent — One-Click Setup Script
# ============================================================
# Run this ONCE to set everything up.
# Usage: bash setup.sh
# ============================================================

set -e  # Stop if any command fails

# Colours for pretty output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_NAME="com.user.dailynews"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║     Daily Tech News Agent — Setup        ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

# ── Step 0: Check config.yaml ─────────────────────────────────────────────────

if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    echo -e "${YELLOW}⚠ config.yaml not found. Creating from example...${RESET}"
    if [ -f "$SCRIPT_DIR/config.yaml.example" ]; then
        cp "$SCRIPT_DIR/config.yaml.example" "$SCRIPT_DIR/config.yaml"
        echo -e "${GREEN}✓ config.yaml created${RESET}"
        echo ""
        echo -e "${YELLOW}  IMPORTANT: Edit config.yaml to set your vault path and preferences!${RESET}"
        echo ""
    else
        echo -e "${RED}ERROR: config.yaml.example not found${RESET}"
        exit 1
    fi
fi

# ── Step 1: Check Python ──────────────────────────────────────────────────────

echo -e "${BOLD}[1/6] Checking Python...${RESET}"

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}✗ Python 3.9+ not found.${RESET}"
    echo ""
    echo "  Install it with Homebrew:"
    echo "    brew install python"
    echo "  Or download from: https://python.org"
    exit 1
fi

PYTHON_PATH=$(command -v "$PYTHON")
echo -e "${GREEN}✓ Python found: $PYTHON_PATH ($($PYTHON --version))${RESET}"


# ── Step 2: Install Python packages ──────────────────────────────────────────

echo ""
echo -e "${BOLD}[2/6] Installing Python packages...${RESET}"
echo -e "${YELLOW}  (This may take a minute on first run)${RESET}"

# Use a virtual environment to keep things clean
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"
PYTHON_PATH="$VENV_DIR/bin/python"

pip install --quiet --upgrade pip
pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

echo -e "${GREEN}✓ Python packages installed${RESET}"


# ── Step 3: Check / Install Ollama ────────────────────────────────────────────

echo ""
echo -e "${BOLD}[3/6] Checking Ollama (the local AI brain)...${RESET}"

if ! command -v ollama &>/dev/null; then
    echo -e "${YELLOW}  Ollama not found. Installing...${RESET}"
    if command -v brew &>/dev/null; then
        brew install ollama
    else
        echo -e "${YELLOW}  Homebrew not found. Please install Ollama manually:${RESET}"
        echo "    → Download from: https://ollama.com/download"
        echo "    → Then re-run this script."
        echo ""
        echo -e "${YELLOW}  Continuing setup anyway...${RESET}"
    fi
else
    echo -e "${GREEN}✓ Ollama installed: $(ollama --version 2>/dev/null || echo 'found')${RESET}"
fi

# Extract the model name from config.yaml
MODEL=$(grep "model:" "$SCRIPT_DIR/config.yaml" | head -1 | awk '{print $2}' | tr -d '"')
MODEL="${MODEL:-qwen2.5:7b}"

echo ""
echo -e "  Configured model: ${BOLD}$MODEL${RESET}"
echo ""

# Check if Ollama is running
if curl -s http://localhost:11434/api/tags &>/dev/null; then
    echo -e "${GREEN}✓ Ollama is running${RESET}"
    # Check if model is already downloaded
    if ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
        echo -e "${GREEN}✓ Model '$MODEL' already downloaded${RESET}"
    else
        echo -e "${YELLOW}  Downloading model '$MODEL'...${RESET}"
        echo -e "${YELLOW}  (This is a one-time download — may take a few minutes)${RESET}"
        ollama pull "$MODEL"
        echo -e "${GREEN}✓ Model downloaded${RESET}"
    fi
else
    echo -e "${YELLOW}  Ollama is not running right now.${RESET}"
    echo -e "${YELLOW}  That's OK — it just needs to be running when the brief generates.${RESET}"
    echo ""
    echo -e "  To start Ollama: ${BOLD}ollama serve${RESET}"
    echo -e "  To pull your model (after starting): ${BOLD}ollama pull $MODEL${RESET}"
fi


# ── Step 4: Verify Obsidian Vault ─────────────────────────────────────────────

echo ""
echo -e "${BOLD}[4/6] Verifying Obsidian vault...${RESET}"

# Read vault path from config.yaml
VAULT_PATH=$(grep "vault_path:" "$SCRIPT_DIR/config.yaml" | head -1 | sed 's/.*vault_path: *"//' | sed 's/".*//' | sed "s|~|$HOME|g")
DAILY_FOLDER=$(grep "daily_notes_folder:" "$SCRIPT_DIR/config.yaml" | head -1 | awk -F'"' '{print $2}')
DAILY_FOLDER="${DAILY_FOLDER:-01 Daily Briefs}"

echo "  Vault location: $VAULT_PATH"

# Ensure all folders exist (safe to run even if already created)
mkdir -p "$VAULT_PATH/.obsidian"
mkdir -p "$VAULT_PATH/$DAILY_FOLDER"
mkdir -p "$VAULT_PATH/02 Deep Dives"
mkdir -p "$VAULT_PATH/03 Topics/AI"
mkdir -p "$VAULT_PATH/03 Topics/Science"
mkdir -p "$VAULT_PATH/04 People"
mkdir -p "$VAULT_PATH/05 Sources"
mkdir -p "$VAULT_PATH/_Templates"
mkdir -p "$VAULT_PATH/_Attachments"

echo -e "${GREEN}✓ Vault ready at: $VAULT_PATH${RESET}"
echo -e "${GREEN}  (Already syncing to iCloud — will appear on your iPhone automatically)${RESET}"


# ── Step 5: Install macOS Scheduler ───────────────────────────────────────────

echo ""
echo -e "${BOLD}[5/6] Setting up 9 AM daily scheduler...${RESET}"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$LOG_DIR"

PLIST_SOURCE="$SCRIPT_DIR/$PLIST_NAME.plist"
PLIST_DEST="$LAUNCH_AGENTS_DIR/$PLIST_NAME.plist"

# Fill in the real paths in the plist template
sed \
    -e "s|PYTHON_PATH_PLACEHOLDER|$PYTHON_PATH|g" \
    -e "s|SCRIPT_PATH_PLACEHOLDER|$SCRIPT_DIR/daily_news.py|g" \
    -e "s|HOME_PATH_PLACEHOLDER|$HOME|g" \
    -e "s|LOG_PATH_PLACEHOLDER|$LOG_DIR|g" \
    "$PLIST_SOURCE" > "$PLIST_DEST"

# Unload first if already registered (ignore errors)
launchctl unload "$PLIST_DEST" 2>/dev/null || true

# Load the new schedule
launchctl load "$PLIST_DEST"

echo -e "${GREEN}✓ Scheduler registered — will run daily at 9:00 AM${RESET}"
echo -e "${GREEN}  (Your Mac must be awake and Ollama must be running at that time)${RESET}"


# ── Step 6: Run a test ────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}[6/6] Running a test to make sure everything works...${RESET}"
echo ""
echo -e "${YELLOW}  This will generate today's brief right now.${RESET}"
echo -e "${YELLOW}  It takes 1–5 minutes depending on your Mac.${RESET}"
echo ""

read -p "  Run the test now? (recommended) [Y/n]: " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Nn]$ ]]; then
    echo -e "${YELLOW}  Skipped. Run manually later with:${RESET}"
    echo "    cd $SCRIPT_DIR && source .venv/bin/activate && python daily_news.py"
else
    echo ""
    echo -e "${CYAN}  Starting the agent...${RESET}"
    echo ""
    "$PYTHON_PATH" "$SCRIPT_DIR/daily_news.py" --force || {
        echo ""
        echo -e "${RED}  Something went wrong. Common fixes:${RESET}"
        echo "    • Is Ollama running? Try: ollama serve"
        echo "    • Is the model downloaded? Try: ollama pull $MODEL"
        echo "    • Check logs: $LOG_DIR/daily-news-agent.log"
    }
fi


# ── Done! ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║              Setup Complete! 🎉          ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}Your daily brief will appear every morning at 9 AM${RESET}"
echo -e "  Vault: ${CYAN}$VAULT_PATH${RESET}"
echo ""
  echo -e "  ${BOLD}Next steps:${RESET}"
  echo -e "  1. Open ${BOLD}Obsidian${RESET} → 'Open another vault' → 'Open folder as vault' → choose TechBrief"
  echo -e "     (It's in iCloud → Obsidian → TechBrief)"
  echo -e "  2. On your iPhone: open Obsidian → tap the vault icon → Open from iCloud Drive"
  echo -e "     → pick 'TechBrief' — it will appear automatically!"
  echo -e "  3. Notes sync every time your Mac is online — no extra setup needed."
echo ""
echo -e "  ${BOLD}Make sure Ollama is always running in the background:${RESET}"
echo -e "    ollama serve &"
echo ""
