# Contributing to F1 E-Ink Calendar

Thank you for your interest in contributing to the F1 E-Ink Calendar project! 🏎️

## Getting Started

1. **Fork the repository**

   ```bash
   git clone https://github.com/Rhiz3K/InkyCloud-F1.git
   cd InkyCloud-F1
   ```

2. **Set up development environment**

   ```bash
   # Create virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate

   # Install dependencies
   pip install -e ".[dev]"
   ```

3. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Workflow

### Running the Server

```bash
# Development mode with auto-reload
uvicorn app.main:app --reload

# Or with debug logging
DEBUG=true python -m app.main
```

### Code Style

We use Ruff for linting and formatting:

```bash
# Check code
uv run ruff check .

# Format code
uv run ruff format .

# Type-check production code
uv run mypy
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run the global 95% statement + branch gate and produce CI's diff input
uv run pytest tests/ -m "not benchmark" --cov=app --cov-branch --cov-report=term-missing --cov-report=xml

# New/changed lines must stay at 100% (the CI ratchet)
uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=100

# Run specific test file
uv run pytest tests/test_renderer.py -v
```

### Adding Translations

To add a new language:

1. Create a new JSON file in `translations/` (e.g., `de.json` for German)
2. Copy the structure from `en.json`
3. Translate all strings
4. Update `app/main.py` to accept the new language code

Example `translations/de.json`:

```json
{
  "next_race": "Nächstes Rennen",
  "schedule": "Zeitplan",
  "error": "Fehler",
  ...
}
```

### Adding New Features

1. **Write tests first** - Add tests in `tests/` directory
2. **Implement the feature** - Make changes in `app/`
3. **Update documentation** - Update README.md and EXAMPLES.md
4. **Test thoroughly** - Run tests and manual testing
5. **Commit and push** - Follow commit message conventions

## Commit Message Conventions

We follow conventional commits:

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes (formatting, etc.)
- `refactor:` Code refactoring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

Examples:

```
feat: add support for Sprint Qualifying session
fix: correct timezone conversion for Australian GP
docs: update Docker deployment instructions
```

## Pull Request Process

1. **Update tests** - Ensure all tests pass
2. **Update documentation** - Document any new features
3. **Prepare a release section** - Add a new semantic-version heading above the previous release,
   keep `## [Unreleased]` empty, and describe the change in that new release section
4. **Validate release metadata** - Run `python -m app.utils.release_validation`
5. **Create PR** - Provide clear description of changes
6. **Address feedback** - Respond to review comments

### PR Checklist

- [ ] Tests added/updated and passing
- [ ] Code follows project style (Ruff checks pass)
- [ ] Documentation updated
- [ ] `CHANGELOG.md` has a new versioned release section and release validation passes
- [ ] Commit messages follow conventions
- [ ] No breaking changes (or clearly documented)

## Project Structure

```
InkyCloud-F1/
├── app/
│   ├── main.py           # FastAPI application (endpoints, lifespan)
│   ├── config.py         # Configuration management
│   ├── models.py         # Pydantic data models
│   └── services/         # Business logic
│       ├── renderer.py      # 1-bit BMP rendering engine
│       ├── f1_service.py    # F1 API integration
│       ├── teams_service.py # Teams & drivers data
│       ├── standings_service.py # Championship standings
│       ├── database.py      # SQLite operations
│       ├── scheduler.py     # APScheduler background jobs
│       ├── backup.py        # S3 database backup
│       ├── analytics.py     # Umami analytics
│       └── i18n.py          # Translations
├── scripts/             # Data update & preprocessing utilities
├── translations/        # i18n JSON files (13 supported locales)
├── tests/               # Test suite
└── .github/             # CI/CD workflows
```

## Areas for Contribution

We welcome contributions in these areas:

- 🎨 **Rendering improvements** - Better layouts, fonts, graphics
- 🗺️ **Track maps** - Add real circuit maps for each track
- 🌍 **Translations** - Add support for more languages
- 🔧 **Features** - Additional data sources, configuration options
- 📱 **ESP32 examples** - More integration examples
- 📚 **Documentation** - Tutorials, guides, examples
- 🐛 **Bug fixes** - Report and fix issues
- ✅ **Tests** - Improve test coverage

## Code Review Process

1. **Automated checks** - GitHub Actions runs tests and linting
2. **Manual review** - Maintainers review code quality and design
3. **Testing** - Changes are tested on real E-Ink displays when possible
4. **Merge** - PR is merged after approval

## Questions?

- Open an [issue](https://github.com/Rhiz3K/InkyCloud-F1/issues) for bugs
- Start a [discussion](https://github.com/Rhiz3K/InkyCloud-F1/discussions) for questions
- Check [EXAMPLES.md](EXAMPLES.md) for usage examples

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (see [LICENSE](LICENSE)).

Thank you for contributing! 🙏
