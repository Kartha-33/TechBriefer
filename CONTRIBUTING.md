# Contributing to Daily Tech News Agent

Thank you for considering contributing! This project is designed to be simple and hackable.

## How to Contribute

### Reporting Bugs

Open an issue with:
- Your OS version and Python version
- The complete error message
- Steps to reproduce

### Suggesting Features

Open an issue describing:
- What you want to achieve
- Why it would be useful
- How you imagine it working

### Submitting Code

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Test thoroughly (run `python daily_news.py --force` to verify)
5. Commit with a clear message: `git commit -m "Add support for YouTube RSS feeds"`
6. Push to your fork: `git push origin feature/your-feature-name`
7. Open a Pull Request

### Code Style

- Follow PEP 8 for Python code
- Keep functions small and focused
- Add comments for non-obvious logic
- Update README.md if you add new features

### Testing

Before submitting:
```bash
# Test the full workflow
python daily_news.py --force

# Check that the note was created
ls -la ~/Library/Mobile\ Documents/iCloud~md~obsidian/Documents/TechBrief/01\ Daily\ Briefs/
```

### Ideas for Contributions

**Easy:**
- Add more RSS sources to `sources.yaml`
- Fix typos in documentation
- Improve error messages

**Medium:**
- Better deduplication algorithm (same story from multiple sources)
- Support for video sources (YouTube channels via RSS)
- Email delivery option (send note via email instead of Obsidian)
- Sentiment analysis (track trending topics)

**Hard:**
- Multi-language support (sources in Spanish, French, Chinese, etc.)
- Podcast support (transcribe + summarize daily podcasts)
- Interactive TUI (terminal UI for managing sources)
- Web dashboard (track story momentum over time)

### Questions?

Open an issue or reach out via email. We're friendly!

---

**Note:** This project prioritizes simplicity and hackability over complexity. If your PR adds significant complexity, please open an issue first to discuss the design.
