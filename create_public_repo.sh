#!/bin/bash
set -e

# =============================================================================
# Script: create_public_repo.sh
# Purpose: Create new public repo InkyCloud-F1 with clean initial commit
# =============================================================================

# Configuration
SOURCE_DIR="/home/rhiz3k/Github/F1_eInk_cal"
TARGET_DIR="/home/rhiz3k/Github/InkyCloud-F1"
REPO_NAME="InkyCloud-F1"
GITHUB_USER="Rhiz3K"

echo "=========================================="
echo "Creating public repo: $REPO_NAME"
echo "=========================================="

# Check if target directory exists
if [ -d "$TARGET_DIR" ]; then
    echo "ERROR: Target directory $TARGET_DIR already exists!"
    echo "Please remove it first: rm -rf $TARGET_DIR"
    exit 1
fi

# Create target directory
echo "Creating target directory..."
mkdir -p "$TARGET_DIR"

# -----------------------------------------------------------------------------
# Copy production files
# -----------------------------------------------------------------------------
echo "Copying production files..."

# Core application
cp -r "$SOURCE_DIR/app" "$TARGET_DIR/"
cp -r "$SOURCE_DIR/tests" "$TARGET_DIR/"
cp -r "$SOURCE_DIR/translations" "$TARGET_DIR/"

# Assets (preview images)
cp -r "$SOURCE_DIR/assets" "$TARGET_DIR/"

# GitHub config
cp -r "$SOURCE_DIR/.github" "$TARGET_DIR/"

# Scripts (selected)
mkdir -p "$TARGET_DIR/scripts"
cp "$SOURCE_DIR/scripts/backup_cli.py" "$TARGET_DIR/scripts/"
cp "$SOURCE_DIR/scripts/update_seasons.py" "$TARGET_DIR/scripts/"
cp "$SOURCE_DIR/scripts/update_historical.py" "$TARGET_DIR/scripts/"
cp "$SOURCE_DIR/scripts/preprocess_flags.py" "$TARGET_DIR/scripts/"
cp "$SOURCE_DIR/scripts/preprocess_tracks.py" "$TARGET_DIR/scripts/"
cp "$SOURCE_DIR/scripts/download_flags.py" "$TARGET_DIR/scripts/"
cp "$SOURCE_DIR/scripts/generate_og_image.py" "$TARGET_DIR/scripts/"

# Config files
cp "$SOURCE_DIR/Dockerfile" "$TARGET_DIR/"
cp "$SOURCE_DIR/docker-compose.yml" "$TARGET_DIR/"
cp "$SOURCE_DIR/pyproject.toml" "$TARGET_DIR/"
cp "$SOURCE_DIR/setup.py" "$TARGET_DIR/"
cp "$SOURCE_DIR/uv.lock" "$TARGET_DIR/"
cp "$SOURCE_DIR/.dockerignore" "$TARGET_DIR/"
cp "$SOURCE_DIR/.coolifyignore" "$TARGET_DIR/"
cp "$SOURCE_DIR/MANIFEST.in" "$TARGET_DIR/"
cp "$SOURCE_DIR/quickstart.sh" "$TARGET_DIR/"

# Documentation
cp "$SOURCE_DIR/README.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/CONTRIBUTING.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/CODE_OF_CONDUCT.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/SECURITY.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/LICENSE" "$TARGET_DIR/"
cp "$SOURCE_DIR/CHANGELOG.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/DEPLOYMENT.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/COOLIFY.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/SELF-HOSTING.md" "$TARGET_DIR/"
cp "$SOURCE_DIR/EXAMPLES.md" "$TARGET_DIR/"

# Environment example
cp "$SOURCE_DIR/.env.example" "$TARGET_DIR/"

# -----------------------------------------------------------------------------
# Create .gitignore (updated)
# -----------------------------------------------------------------------------
echo "Creating updated .gitignore..."
cat > "$TARGET_DIR/.gitignore" << 'EOF'
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[codz]
*$py.class

# C extensions
*.so

# Distribution / packaging
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST

# PyInstaller
*.manifest
*.spec

# Installer logs
pip-log.txt
pip-delete-this-directory.txt

# Unit test / coverage reports
htmlcov/
.tox/
.nox/
.coverage
.coverage.*
.cache
nosetests.xml
coverage.xml
*.cover
*.py.cover
.hypothesis/
.pytest_cache/
cover/

# Translations
*.mo
*.pot

# Django stuff:
*.log
local_settings.py
db.sqlite3
db.sqlite3-journal

# Flask stuff:
instance/
.webassets-cache

# Scrapy stuff:
.scrapy

# Sphinx documentation
docs/_build/

# PyBuilder
.pybuilder/
target/

# Jupyter Notebook
.ipynb_checkpoints

# IPython
profile_default/
ipython_config.py

# pyenv
# .python-version

# pipenv
#Pipfile.lock

# UV
#uv.lock

# poetry
#poetry.lock
#poetry.toml

# pdm
#pdm.lock
#pdm.toml
.pdm-python
.pdm-build/

# pixi
#pixi.lock
.pixi

# PEP 582
__pypackages__/

# Celery stuff
celerybeat-schedule
celerybeat.pid

# SageMath parsed files
*.sage.py

# Environments
.env
.envrc
.venv
env/
venv/
ENV/
env.bak/
venv.bak/

# Spyder project settings
.spyderproject
.spyproject

# Rope project settings
.ropeproject

# mkdocs documentation
/site

# mypy
.mypy_cache/
.dmypy.json
dmypy.json

# Pyre type checker
.pyre/

# pytype static type analyzer
.pytype/

# Cython debug symbols
cython_debug/

# Ruff stuff:
.ruff_cache/

# PyPI configuration file
.pypirc

# Cursor
.cursorignore
.cursorindexingignore

# Marimo
marimo/_static/
marimo/_lsp/
__marimo__/

# Project-specific
api_response*.json
debug_*.png
test_calendar*.bmp
*.bmp
!app/assets/**/*.bmp
!assets/**/*.bmp
*.db
data/benchmark_results.json

# macOS
.DS_Store
.DS_Store?
._*

# IDE
.idea/
.vscode/
*.swp
*.swo

# AI assistant configs
.claude/
CLAUDE.md
AGENTS.md
PLAN.md

# Internal workflow
conductor/
openspec/

# Local environment files
.env.local
.env.local.example
EOF

# -----------------------------------------------------------------------------
# Create .editorconfig
# -----------------------------------------------------------------------------
echo "Creating .editorconfig..."
cat > "$TARGET_DIR/.editorconfig" << 'EOF'
# EditorConfig helps maintain consistent coding styles
# https://editorconfig.org

root = true

[*]
indent_style = space
indent_size = 4
end_of_line = lf
charset = utf-8
trim_trailing_whitespace = true
insert_final_newline = true

[*.{md,yml,yaml,json}]
indent_size = 2

[*.html]
indent_size = 2

[Makefile]
indent_style = tab

[Dockerfile]
indent_size = 4
EOF

# -----------------------------------------------------------------------------
# Create PUBLIC_REPO_CHECKLIST.md
# -----------------------------------------------------------------------------
echo "Creating PUBLIC_REPO_CHECKLIST.md..."
cat > "$TARGET_DIR/PUBLIC_REPO_CHECKLIST.md" << 'EOF'
# Public Repository Setup Checklist

Checklist for setting up the public GitHub repository `Rhiz3K/InkyCloud-F1`.

## 1. Branch Protection (Settings → Branches)

### Rule for `main` branch
- [ ] Add branch protection rule for `main`
- [ ] **Require a pull request before merging**
  - [ ] Require at least 1 approval
  - [ ] Dismiss stale pull request approvals when new commits are pushed
- [ ] **Require status checks to pass before merging**
  - [ ] Require branches to be up to date before merging
  - [ ] Add required status checks: `test`, `lint`
- [ ] **Do not allow bypassing the above settings**

## 2. Security Settings (Settings → Code security and analysis)

- [ ] Enable **Dependabot alerts**
- [ ] Enable **Dependabot security updates**
- [ ] Enable **Secret scanning**
- [ ] Enable **Push protection**

## 3. Repository Settings (Settings → General)

### Features
- [ ] Enable **Issues**
- [ ] Enable **Discussions**
- [ ] Disable **Wiki**

### Pull Requests
- [ ] Allow **squash merging**
- [ ] **Automatically delete head branches**

## 4. About Section (gear icon on repo page)

- [ ] Description: `F1 E-Ink Calendar - Generate 800x480 1-bit BMP images for E-Ink displays`
- [ ] Website: `https://f1.inkycloud.click`
- [ ] Topics: `f1`, `formula1`, `eink`, `esp32`, `fastapi`, `python`

## 5. Verify CI Workflows

- [ ] CI runs on pull requests
- [ ] CI runs on push to main
- [ ] Tests pass
- [ ] Linting passes

## 6. First Release

```bash
git tag -a v1.0.0 -m "Initial public release"
git push origin v1.0.0
```

- [ ] Create GitHub Release from tag
- [ ] Add release notes

## 7. Final Verification

```bash
# Fresh clone test
git clone https://github.com/Rhiz3K/InkyCloud-F1.git /tmp/test-clone
cd /tmp/test-clone
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check .

# Docker test
docker build -t inkycloud-f1:test .
docker run -p 8000:8000 inkycloud-f1:test
# Verify http://localhost:8000 works
```

- [ ] All tests pass
- [ ] Docker build works
- [ ] App runs correctly
EOF

# -----------------------------------------------------------------------------
# Update README.md - replace repo URLs
# -----------------------------------------------------------------------------
echo "Updating README.md with new repo name..."
sed -i 's|Rhiz3K/F1_eInk_cal|Rhiz3K/InkyCloud-F1|g' "$TARGET_DIR/README.md"
sed -i 's|F1_eInk_cal|InkyCloud-F1|g' "$TARGET_DIR/README.md"

# Also update CONTRIBUTING.md
sed -i 's|Rhiz3K/F1_eInk_cal|Rhiz3K/InkyCloud-F1|g' "$TARGET_DIR/CONTRIBUTING.md"
sed -i 's|F1_eInk_cal|InkyCloud-F1|g' "$TARGET_DIR/CONTRIBUTING.md"

# Update SECURITY.md
sed -i 's|Rhiz3K/F1_eInk_cal|Rhiz3K/InkyCloud-F1|g' "$TARGET_DIR/SECURITY.md"

# Update pyproject.toml
sed -i 's|Rhiz3K/F1_eInk_cal|Rhiz3K/InkyCloud-F1|g' "$TARGET_DIR/pyproject.toml"

# Update COOLIFY.md
sed -i 's|Rhiz3K/F1_eInk_cal|Rhiz3K/InkyCloud-F1|g' "$TARGET_DIR/COOLIFY.md"
sed -i 's|F1_eInk_cal|InkyCloud-F1|g' "$TARGET_DIR/COOLIFY.md"

# Update DEPLOYMENT.md
sed -i 's|Rhiz3K/F1_eInk_cal|Rhiz3K/InkyCloud-F1|g' "$TARGET_DIR/DEPLOYMENT.md"
sed -i 's|F1_eInk_cal|InkyCloud-F1|g' "$TARGET_DIR/DEPLOYMENT.md"

# Update SELF-HOSTING.md
sed -i 's|Rhiz3K/F1_eInk_cal|Rhiz3K/InkyCloud-F1|g' "$TARGET_DIR/SELF-HOSTING.md"
sed -i 's|F1_eInk_cal|InkyCloud-F1|g' "$TARGET_DIR/SELF-HOSTING.md"

# -----------------------------------------------------------------------------
# Initialize git repository
# -----------------------------------------------------------------------------
echo "Initializing git repository..."
cd "$TARGET_DIR"
git init
git branch -M main

# Add all files
git add .

# Create initial commit
git commit -m "feat: initial public release

F1 E-Ink Calendar - Generate 800x480 1-bit BMP images for E-Ink displays.

Features:
- 800x480 1-bit BMP optimized for E-Ink displays
- Multi-language support (Czech, English)
- Any timezone support
- Historical race results
- Track info with circuit maps
- Full session schedule

Public instance: https://f1.inkycloud.click"

echo ""
echo "=========================================="
echo "SUCCESS! Repository created at: $TARGET_DIR"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Create the GitHub repository:"
echo "   gh repo create $REPO_NAME --public --source=$TARGET_DIR --push"
echo ""
echo "   OR manually:"
echo "   - Go to https://github.com/new"
echo "   - Create repo '$REPO_NAME' (public, no README/gitignore/license)"
echo "   - Then run:"
echo "     cd $TARGET_DIR"
echo "     git remote add origin git@github.com:$GITHUB_USER/$REPO_NAME.git"
echo "     git push -u origin main"
echo ""
echo "2. Follow the checklist in PUBLIC_REPO_CHECKLIST.md"
echo ""
echo "3. Create first release:"
echo "   git tag -a v1.0.0 -m 'Initial public release'"
echo "   git push origin v1.0.0"
echo ""
