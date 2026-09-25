# Autonomous Multimodal QA & Bug Reproduction Agent

A final-year Computer Science project implementing an AI-powered SDET (Software Development Engineer in Test) that tests web applications like a human tester — autonomously exploring, visually reasoning, detecting defects, and producing reproducible bug reports.

## Abstract

Traditional automated testing fails when visual quality and UI integrity matter. Selector-based frameworks (Selenium, Playwright scripts) can confirm that an element exists and is clickable, but they cannot see that a button is invisible, overlapping another element, or cut off at the screen edge. Meanwhile, manual QA is slow, expensive, and inconsistent.

This project builds a hybrid agent that combines:
- **Playwright browser automation** for deterministic, reliable event-level instrumentation
- **A Vision-Language Model (VLM)** for screenshot-based visual reasoning
- **Autonomous exploration** that discovers pages and interactions without pre-written test cases
- **Structured bug reproduction** with action replay
- **Evidence-backed reports** in JSON and HTML

## Problem Statement

1. **Brittle selectors** — CSS/XPath selectors break when HTML changes, even if the UI looks identical
2. **Shallow coverage** — selector tests verify element existence, not visual correctness
3. **Human-intensive** — every test case must be authored manually
4. **Visual blindness** — automated tests cannot detect overlap, clipping, or layout failures

## Objectives

1. Build a local CLI tool that accepts a URL and autonomously QA-tests it
2. Detect functional defects (errors, failures, broken links/images)
3. Detect visual defects (layout, overlap, overflow, responsive failures)
4. Test across multiple viewport sizes (Desktop → Mobile)
5. Reproduce detected bugs with action replay
6. Generate annotated evidence screenshots
7. Produce structured JSON + HTML reports

## Features

| Feature | Implementation |
|---------|---------------|
| Autonomous exploration | BFS + VLM action planning |
| Console error detection | Playwright console event listener |
| Network failure detection | Playwright request/response events |
| Broken image detection | DOM naturalWidth/naturalHeight check |
| Broken link detection | Concurrent HEAD requests via aiohttp |
| Layout overflow detection | JavaScript DOM measurement |
| Accessibility validation | axe-core injected via Playwright |
| Visual defect detection | GPT-4o multimodal analysis |
| Responsive testing | 4 viewport sizes (Desktop → Mobile) |
| Visual regression | Pixel diff + VLM comparison |
| Bug reproduction | Action history replay |
| Screenshot annotation | Pillow-based bounding box drawing |
| Video recording | Playwright recording API |
| HTML + JSON reports | Jinja2-templated static reports |

## Architecture

```
                  ┌───────────────────┐
                  │   User / CLI      │
                  │   (Rich + Click)  │
                  └─────────┬─────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │   QA Orchestrator │
                  └─────────┬─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   BrowserManager    ExplorationEngine   ValidatorPipeline
   (Playwright)      (BFS + VLM)        (8 validators)
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                    EvidenceCollector
                    AnnotatedScreenshots
                            │
                    BugReproducer
                            │
                    ReportGenerator
                    (JSON + HTML)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design decisions.

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.11 | Best AI/ML ecosystem; async-first |
| Browser automation | Playwright (async) | Video, mobile emulation, network events |
| VLM | GPT-4o via OpenAI API | Best multimodal model available |
| VLM abstraction | Custom provider interface | Swap providers without code changes |
| Image annotation | Pillow | Lightweight, no build step |
| HTML reports | Jinja2 | No frontend build needed |
| Accessibility | axe-core (injected) | Industry-standard WCAG checker |
| CLI | Click + Rich | Clean terminal output |
| Config | PyYAML + Pydantic | Type-safe validated config |
| Testing | pytest + pytest-asyncio | Standard Python testing |

## Installation

### Prerequisites

- Python 3.11+
- pip

### Setup

```bash
# Clone or unzip the project
cd qa-agent

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Copy and configure environment
cp .env.example .env
# Edit .env to add your API key (or use mock mode)
```

### Configuration

Edit `.env`:

```env
# Use mock mode for testing without API costs
VLM_PROVIDER=mock

# For real AI analysis:
# VLM_PROVIDER=openai
# VLM_API_KEY=sk-your-openai-api-key-here
# VLM_MODEL=gpt-4o
```

## Usage

### Basic run

```bash
qa-agent https://example.com
```

### With options

```bash
# Limit exploration scope
qa-agent https://example.com --max-pages 10 --max-actions 50

# Use real AI (requires API key in .env)
qa-agent https://example.com --provider openai

# Desktop only (skip mobile/tablet viewports)
qa-agent https://example.com --desktop-only

# Show browser window (useful for debugging)
qa-agent https://example.com --no-headless

# Verbose logging
qa-agent https://example.com --log-level DEBUG
```

### Visual regression

```bash
# Store baseline screenshots
qa-agent baseline https://example.com

# Run comparison against stored baselines
qa-agent compare https://example.com
```

### Using a custom config file

```bash
qa-agent --config myconfig.yaml https://example.com
```

See `config.yaml` for all available options.

## Example Output

```
╔═══════════════════════════════════════════════╗
║    AUTONOMOUS MULTIMODAL QA AGENT             ║
╚═══════════════════════════════════════════════╝

Target: https://example.com

Exploration
───────────
Pages discovered:      8
States tested:        16
Actions performed:    32
Viewports tested:      4

Validators
──────────
✓ console
✓ network
✓ images
✓ links
✓ layout
✓ accessibility
✓ responsive
✓ visual_ai

Results
───────
Critical: 0
High:     2
Medium:   3
Low:      4

Reproduction
─────────────
Reproduced:           4
Could not reproduce:  1

Report
──────
HTML: ./reports/<run-id>/report.html
JSON: ./reports/<run-id>/report.json
```

## Report Structure

### report.json

```json
{
  "run_id": "abc123",
  "target_url": "https://example.com",
  "findings": [
    {
      "id": "BUG-001",
      "title": "Mobile navigation overlaps content",
      "severity": "HIGH",
      "category": "RESPONSIVE",
      "url": "https://example.com",
      "viewport": "390x844",
      "description": "...",
      "steps_to_reproduce": [...],
      "expected": "...",
      "actual": "...",
      "confidence": 0.95,
      "reproduced": true,
      "screenshot": "...",
      "annotated_screenshot": "..."
    }
  ]
}
```

### report.html

A self-contained static HTML file with:
- Executive summary with severity counts
- Per-validator pass/fail status
- Full finding details with screenshots
- Reproduction steps
- Viewport information

## Testing

```bash
# Run all unit tests
python -m pytest tests/unit/ -v

# Run integration tests (requires Playwright)
python -m pytest tests/integration/ -v

# Test against the broken test sites
cd examples/test-sites && python -m http.server 8080
# In another terminal:
qa-agent http://localhost:8080/console-error-site/ --max-pages 1 --provider mock
```

See [TESTING.md](TESTING.md) for the complete testing strategy.

## Limitations

The following limitations are documented honestly — they represent areas for future research and improvement:

1. **VLM hallucinations** — GPT-4o can occasionally report visual defects that don't exist, or miss subtle issues. Confidence scores help calibrate this.

2. **Dynamic content** — Pages that render differently on each load (timestamps, personalized content, A/B tests) may produce inconsistent results.

3. **Authentication** — The agent does not handle login pages. Pages behind authentication require manual session configuration.

4. **CAPTCHA** — The agent cannot bypass CAPTCHAs and will stop exploration if one is encountered.

5. **Infinite scroll** — Pages with infinite scroll may not be fully explored due to action limits.

6. **Canvas/WebGL** — Canvas-rendered applications cannot be inspected by DOM-based validators. VLM screenshot analysis still applies.

7. **WebSocket-heavy apps** — Realtime apps with WebSocket-driven state changes may not be fully testable.

8. **Accessibility completeness** — Automated axe-core checks catch roughly 30-40% of WCAG violations. Manual screen reader testing is required for full compliance verification.

9. **False positives** — Particularly in visual AI and layout detection. Review findings before filing bugs.

10. **API rate limits** — Heavy use of GPT-4o may hit rate limits. Mock mode is available for rate-limit-free testing.

## Future Roadmap

```
Current: CLI Prototype
     ↓
Add FastAPI REST layer
     ↓
Add web dashboard (React)
     ↓
Add job queue (Celery/Redis)
     ↓
Cloud browser execution
     ↓
Multi-project SaaS platform
```

The core QA engine is decoupled from the CLI entry point, making this evolution straightforward.

## Project Structure

```
qa-agent/
├── src/qa_agent/
│   ├── agent/           # QAOrchestrator — drives the full run
│   ├── browser/         # Playwright wrapper (BrowserManager, PageController)
│   ├── cli/             # Click CLI entry point
│   ├── crawler/         # Autonomous exploration engine
│   ├── evidence/        # Screenshot capture and annotation
│   ├── prompts/         # VLM prompt templates
│   ├── reproduction/    # Bug reproduction via action replay
│   ├── reporting/       # JSON/HTML report generation
│   ├── validators/      # 8 independent QA validators
│   ├── vision/          # VLM provider abstraction (OpenAI, Mock)
│   └── utils/           # Schemas, config, logging
├── tests/
│   ├── unit/            # No browser required
│   ├── integration/     # Requires Playwright
│   └── browser/         # Full E2E tests
├── examples/
│   └── test-sites/      # Intentionally broken HTML sites
├── reports/             # Generated reports (per run)
├── screenshots/         # Captured screenshots (per run)
├── recordings/          # Video recordings (per run)
├── logs/                # Log files (per run)
├── baselines/           # Visual regression baselines
├── config.yaml          # Default configuration
├── .env.example         # Environment variable template
├── requirements.txt
├── pyproject.toml
├── ARCHITECTURE.md      # Detailed design decisions
└── TESTING.md           # Test strategy
```

## Academic Context

This project is a final-year Computer Science project demonstrating:

- **Novelty**: Combining autonomous visual reasoning, browser automation, deterministic validators, bug reproduction, and multimodal evidence into a single unified QA agent
- **Engineering quality**: Modular architecture, type-safe schemas, comprehensive tests, honest documentation of limitations
- **Practical value**: Produces immediately usable QA reports without any pre-written test cases

The system is designed to be demonstrated live against real websites, producing visible results that can be inspected and explained to a review panel.
