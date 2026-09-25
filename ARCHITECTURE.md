# ARCHITECTURE.md — Autonomous Multimodal QA & Bug Reproduction Agent

## 1. Problem Statement

Traditional automated testing depends on manually authored selectors (`#login-button`, `.navbar > li:nth-child(2)`).
This approach is:
- **Brittle** — any CSS/HTML refactor breaks tests even if the UI looks identical to a human
- **Shallow** — it cannot reason about visual quality, layout integrity, or aesthetic regressions
- **Human-intensive** — every test scenario must be written by a developer in advance
- **Blind to visual context** — a button could be invisible, overlapping, or off-screen and a selector-based test would still "pass"

**This project addresses all four problems simultaneously.**

## 2. Proposed System

A hybrid deterministic + multimodal AI agent that:
1. Autonomously explores a web application without pre-written test cases
2. Uses a Vision-Language Model (VLM) to reason about screenshots like a human tester
3. Runs deterministic browser-instrumented validators for measurable facts (HTTP errors, console errors, DOM metrics)
4. Records action history and replays it to reproduce detected bugs
5. Generates structured, evidence-backed bug reports

## 3. Architecture Overview

```
                 ┌──────────────────────────────┐
                 │         User / CLI           │
                 │   qa-agent <url> [options]   │
                 └──────────────┬───────────────┘
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │       QA Orchestrator        │  ← coordinates all subsystems
                 │    (src/qa_agent/agent/)     │
                 └──────┬──────────┬────────────┘
                        │          │
          ┌─────────────┘          └───────────────────┐
          ▼                                            ▼
┌──────────────────┐                      ┌────────────────────┐
│ Browser Manager  │                      │ Exploration Engine │
│ (Playwright)     │◄────────────────────►│ (crawler/)         │
│  - page control  │                      │  - state tracking  │
│  - screenshots   │                      │  - action planning │
│  - video record  │                      │  - VLM decisions   │
│  - network mon.  │                      │  - visit limits    │
└────────┬─────────┘                      └────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│                    Validator Pipeline                        │
│                   (src/qa_agent/validators/)                 │
│                                                              │
│  ConsoleValidator   NetworkValidator   LinkValidator         │
│  ImageValidator     LayoutValidator    AccessibilityValidator│
│  VisualValidator    ResponsiveValidator                      │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │    Vision Provider     │  ← abstracted AI layer
              │   (vision/)           │
              │  OpenAIProvider        │
              │  MockVisionProvider    │
              └────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   Evidence Collector   │
              │   (evidence/)          │
              │  - screenshots         │
              │  - annotations         │
              │  - console logs        │
              │  - network logs        │
              │  - video timestamps    │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   Bug Deduplicator     │
              │   (agent/dedup.py)     │
              │  - correlation         │
              │  - severity rules      │
              │  - unique IDs          │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Bug Reproduction      │
              │  (reproduction/)       │
              │  - action replay       │
              │  - state restoration   │
              │  - confirmation        │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   Report Generator     │
              │   (reporting/)         │
              │  - report.json         │
              │  - report.html         │
              └────────────────────────┘
```

## 4. Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `cli/` | Argument parsing, config loading, entry point, Rich terminal output |
| `browser/` | Playwright wrapper — page lifecycle, screenshots, video, JS execution, event capture |
| `crawler/` | Exploration state machine — URL queue, action queue, visited tracking, depth limits |
| `agent/` | Orchestrator — drives the full QA run; bug deduplication and severity rules |
| `vision/` | VLM provider abstraction — OpenAI, Mock; prompt construction; response parsing |
| `validators/` | Independent validator modules, each producing `Finding` objects |
| `reproduction/` | Action history recording and replay for bug reproduction |
| `evidence/` | Screenshot capture, Pillow-based annotation, log collection |
| `reporting/` | JSON/HTML report generation using Jinja2 templates |
| `prompts/` | Centralized VLM prompt templates (not scattered inline) |
| `utils/` | Shared utilities — logging setup, config models, data schemas |

## 5. Core Data Schemas

### Finding
```python
@dataclass
class Finding:
    id: str              # BUG-001
    title: str
    severity: Severity   # CRITICAL / HIGH / MEDIUM / LOW / INFO
    category: Category   # VISUAL / CONSOLE / NETWORK / LAYOUT / ACCESSIBILITY / LINK / IMAGE
    url: str
    viewport: str
    description: str
    steps_to_reproduce: list[str]
    expected: str
    actual: str
    confidence: float    # 0.0–1.0; 1.0 for deterministic findings
    reproduced: bool | None
    console_errors: list[str]
    network_errors: list[str]
    screenshot: str | None
    annotated_screenshot: str | None
    video_timestamp: float | None
    timestamp: str
```

### PageState
```python
@dataclass
class PageState:
    url: str
    title: str
    screenshot_path: str
    visible_elements: list[ElementInfo]
    console_errors: list[ConsoleMessage]
    network_errors: list[NetworkEvent]
    viewport: str
    depth: int
    parent_url: str | None
    actions_performed: list[ActionRecord]
    timestamp: str
    hash: str  # for deduplication — content-based
```

### ActionRecord
```python
@dataclass
class ActionRecord:
    action_type: str   # click / type / scroll / hover / navigate / go_back
    target_description: str
    selector: str | None   # resolved internally, not authored by user
    value: str | None
    url_before: str
    url_after: str | None
    success: bool
    timestamp: str
```

## 6. Exploration Strategy

The exploration engine uses a **breadth-first with priority queue** approach:

1. Start at the given URL
2. Discover all interactive elements via accessibility tree + DOM query
3. Ask VLM (or heuristic in mock mode) to select the most valuable next action
4. Execute action, capture state
5. If new URL/state discovered → add to queue
6. Repeat until limits reached

**Safeguards:**
- `max_pages` — stop after N unique pages
- `max_actions` — stop after N total actions
- `max_depth` — don't explore beyond N clicks from root
- `timeout` — wall-clock timeout for entire run
- URL normalization + hash-based state deduplication

## 7. Deterministic vs AI Validation Split

| Validation | Method | Why |
|-----------|--------|-----|
| HTTP 4xx/5xx | Playwright network events | 100% reliable, no AI needed |
| Console errors | Playwright console events | 100% reliable |
| Broken images | DOM + resource status | Fast, precise |
| Broken links | HTTP HEAD requests | Deterministic |
| DOM overflow | JS measurements | Precise pixel data |
| Accessibility | axe-core injected via JS | Industry standard |
| Visual defects | VLM + screenshot | Only viable approach |
| Exploration decisions | VLM (fallback: heuristic) | Semantic understanding needed |
| Bug explanation | VLM | Natural language reasoning |

This hybrid ensures the tool works usefully even when the VLM API is unavailable.

## 8. VLM Provider Abstraction

```
VisionProvider (ABC)
    ├── analyze_screenshot(screenshot_b64, context) → VisualAnalysisResult
    └── plan_action(screenshot_b64, page_context) → ActionPlan

OpenAIProvider(VisionProvider)
    └── Uses GPT-4o via OpenAI API

MockVisionProvider(VisionProvider)
    └── Returns deterministic mock responses for testing without API credits
```

All VLM calls go through this abstraction. Switching providers requires changing one config line.

## 9. Bug Deduplication Logic

Multiple validators may fire on the same root cause (e.g., a failed API call causes a console error, a network error, and a broken UI component).

Deduplication checks:
1. Same URL + same error message → merge
2. Console error URL matches network error URL → correlate
3. Visual finding at same coordinates within 20px → merge
4. Same `Finding.title` within same page → deduplicate

Each unique root cause gets one `BUG-NNN` ID. Related evidence is attached as supporting evidence rather than separate bugs.

## 10. Severity Rules (deterministic, not arbitrary)

| Condition | Severity |
|-----------|----------|
| Page crash / uncaught exception that breaks navigation | CRITICAL |
| HTTP 5xx on primary navigation | CRITICAL |
| Layout completely broken at any viewport | HIGH |
| VLM visual defect with confidence ≥ 0.85 | HIGH |
| HTTP 4xx on navigation link | HIGH |
| Console error affecting functionality | HIGH |
| Broken image in main content | MEDIUM |
| Accessibility violation (axe critical) | HIGH |
| Accessibility violation (axe serious) | MEDIUM |
| Layout overflow detected | MEDIUM |
| VLM visual defect with confidence 0.60–0.84 | MEDIUM |
| Broken link (non-primary) | LOW |
| Minor console warning | LOW |
| VLM visual defect with confidence < 0.60 | INFO |
| Missing alt text | LOW |

## 11. Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.11+ | Best AI/ML ecosystem; Playwright Python is mature |
| Browser automation | Playwright (async) | Video recording, mobile emulation, network intercept, accessibility tree |
| VLM | OpenAI GPT-4o | Best multimodal model; structured JSON output mode |
| Image annotation | Pillow | Lightweight, no heavy dependencies |
| HTML reports | Jinja2 | Simple template engine, no frontend build needed |
| Accessibility | axe-core (injected JS) | Industry standard WCAG checker |
| Terminal UI | Rich | Clean, readable CLI output |
| Config | PyYAML + Pydantic | Validated config with type safety |
| Testing | pytest + pytest-asyncio | Standard Python testing |
| Packaging | pyproject.toml + setuptools | Modern Python packaging |

## 12. Future Evolution Path

```
Phase 1-8: CLI Prototype (this repository)
    ↓
Add REST API layer (FastAPI)
    ↓
Add web dashboard (React/Next.js)
    ↓
Add job queue (Celery/Redis)
    ↓
Add cloud browser execution (Playwright on Lambda/K8s)
    ↓
Multi-project SaaS platform
```

The current architecture is designed to support this evolution. The core QA engine is decoupled from the CLI, so adding an API layer means wrapping the same engine in FastAPI endpoints.

## 13. Key Architectural Decisions and Rationale

**Decision 1: Python over Node.js**
Playwright has excellent Python bindings. The AI/ML ecosystem (OpenAI SDK, axe-playwright-python, Pillow) is native Python. Better for academic demonstration.

**Decision 2: Async architecture throughout**
Playwright's Python API is async-first. Browser operations like screenshots, clicks, and network monitoring are I/O-bound. Async enables clean concurrent operations.

**Decision 3: Mock VLM as first-class citizen**
The mock provider is not a testing afterthought — it is designed to run the complete pipeline without API costs, which is critical for CI and for testing the non-AI components.

**Decision 4: Validators as independent modules**
Each validator is independent and returns a list of `Finding` objects. This makes them individually testable and easily extensible. Adding a new validator does not touch existing code.

**Decision 5: Prompts centralized in files**
VLM prompts are stored in `src/qa_agent/prompts/` as text templates, not inline strings. This makes them auditable, adjustable, and version-controllable without touching Python code.

**Decision 6: axe-core injected via Playwright**
Rather than a separate axe-core integration, we inject the axe-core library via `page.evaluate()`. This gives us full WCAG scanning on any page without additional browser setup.
