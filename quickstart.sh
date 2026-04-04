#!/bin/bash
# Quick start script for F1 E-Ink Calendar

set -e

echo "🏎️  F1 E-Ink Calendar - Quick Start"
echo "=================================="
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env
    echo "✅ .env file created. Please edit it with your configuration."
    echo ""
fi

# Check if Docker is available
if command -v docker &> /dev/null; then
    echo "🐳 Docker detected. Choose installation method:"
    echo "1) Docker Compose (recommended)"
    echo "2) Docker"
    echo "3) Local Python installation"
    read -p "Enter choice [1-3]: " choice
    
    case $choice in
        1)
            echo "Starting with Docker Compose..."
            docker compose up -d
            echo "✅ Service started on http://localhost:8000"
            echo "📊 View logs: docker compose logs -f"
            ;;
        2)
            echo "Building Docker image..."
            docker build -t f1-eink-cal .
            echo "Starting container..."
            docker run -d -p 8000:8000 --env-file .env --name f1-eink-cal f1-eink-cal
            echo "✅ Service started on http://localhost:8000"
            echo "📊 View logs: docker logs -f f1-eink-cal"
            ;;
        3)
            echo "Installing with Python..."
            pip install -e .
            echo "Starting server..."
            python -m app.main
            ;;
        *)
            echo "Invalid choice. Exiting."
            exit 1
            ;;
    esac
else
    echo "🐍 Installing with Python..."
    pip install -e .
    echo "Starting server..."
    python -m app.main
fi

echo ""
echo "📖 API Endpoints:"
echo "   - http://localhost:8000/ (API info)"
echo "   - http://localhost:8000/health (Health check)"
echo "   - http://localhost:8000/calendar.bmp?lang=en (Calendar)"
echo ""
echo "🎉 Setup complete!"
