# Tech Stack - F1 E-Ink Calendar

## Core Technologies
- **Python (3.11+):** The primary programming language, chosen for its strong ecosystem in data processing and imaging.
- **FastAPI:** A modern, fast (high-performance) web framework for building APIs with Python.
- **Pillow (PIL Fork):** The main imaging library used for generating and manipulating the 1-bit BMP images.

## Data & Storage
- **SQLite:** A lightweight, serverless database used to store race schedules, track information, and historical results.
- **aiosqlite:** Provides an asynchronous interface for SQLite to integrate seamlessly with FastAPI's async nature.
- **Boto3:** The AWS SDK for Python, used for automated database backups to S3-compatible storage.

## Networking & Async
- **HTTPX:** A next-generation HTTP client for Python, used for fetching race data from external APIs (e.g., Jolpica).
- **AIOFiles:** Used for asynchronous file operations, ensuring the application remains responsive during image saving and reading.

## Infrastructure & Tooling
- **uv:** An extremely fast Python package and project manager.
- **Docker:** Used for containerization and consistent deployment across different environments.
- **Ruff:** An extremely fast Python linter and code formatter.
- **Pytest:** The framework used for writing and running unit and integration tests.
