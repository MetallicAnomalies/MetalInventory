#!/bin/bash

# --- CONFIGURATION (EDIT THIS) ---
# IMPORTANT: Keep the single quotes (' ') around the paths!
# You can paste Windows paths directly (e.g., 'C:\My Documents\file.json')

# 1. Where is the NEW file located?
SOURCE_PATH='C:\Users\Your Name\Downloads\albums_metadata.json'

# 2. Where is your local Git repository folder?
REPO_PATH='D:\Workspace\MetalReleases'

# 3. What is the filename inside the repo?
TARGET_FILENAME="albums_metadata.json"
# ---------------------------------

# Stop on error
set -e

# --- AUTO-FIX WINDOWS PATHS ---
# This converts "C:\Folder\File" to "/c/Folder/File" or "C:/Folder/File"
# so Git Bash or WSL can read it correctly.
if command -v cygpath &> /dev/null; then
    # For Git Bash / MinGW
    SOURCE_FILE=$(cygpath -u "$SOURCE_PATH")
    REPO_DIR=$(cygpath -u "$REPO_PATH")
elif command -v wslpath &> /dev/null; then
    # For WSL (Windows Subsystem for Linux)
    SOURCE_FILE=$(wslpath -u "$SOURCE_PATH")
    REPO_DIR=$(wslpath -u "$REPO_PATH")
else
    # Fallback: Just swap backslashes to forward slashes
    SOURCE_FILE=$(echo "$SOURCE_PATH" | sed 's/\\/\//g')
    REPO_DIR=$(echo "$REPO_PATH" | sed 's/\\/\//g')
fi
# ------------------------------

echo "🚀 Starting update process..."
echo "   Source: $SOURCE_FILE"
echo "   Repo:   $REPO_DIR"

# 1. Check if source file exists
if [ ! -f "$SOURCE_FILE" ]; then
    echo "❌ Error: Source file not found."
    exit 1
fi

# 2. Copy the file
echo "📂 Copying file..."
cp "$SOURCE_FILE" "$REPO_DIR/$TARGET_FILENAME"

# 3. Navigate to repo
cd "$REPO_DIR"

# 4. Check for changes
if git diff --quiet "$TARGET_FILENAME"; then
    echo "⚠️  No changes detected. Nothing to push."
    exit 0
fi

# 5. Git operations
echo "📦 Committing changes..."
git add "$TARGET_FILENAME"
git commit -m "Update $TARGET_FILENAME: $(date +'%Y-%m-%d %H:%M:%S')"

echo "⬆️  Pushing to GitHub..."
git push

echo "✅ Success!"