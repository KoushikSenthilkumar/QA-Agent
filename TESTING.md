# TESTING.md — QA Agent Test Strategy

## Test Structure

```
tests/
├── unit/              # Fast, no browser required
│   ├── test_schemas.py        # Data model tests
│   ├── test_validators.py     # Validator logic tests (mock page states)
│   ├── test_vision.py         # Mock VLM provider tests
│   ├── test_config.py         # Configuration loading tests
│   └── test_reporting.py      # Report generation tests
├── integration/       # Browser + validators (requires Playwright)
│   └── test_browser_validators.py
└── browser/          # Full end-to-end tests against test sites
    └── test_e2e.py
```

## Running Tests

```bash
# All unit tests (fast, no browser)
python -m pytest tests/unit/ -v

# With coverage report
python -m pytest tests/unit/ --cov=qa_agent --cov-report=html

# Integration tests (requires Playwright)
python -m pytest tests/integration/ -v

# All tests
python -m pytest -v
```

## Test Sites

The `examples/test-sites/` directory contains intentionally broken websites
used to validate that the QA agent correctly detects known issues.

### Running test sites locally

These are static HTML files. Serve them with any HTTP server:

```bash
# Using Python's built-in server
cd examples/test-sites
python -m http.server 8080

# Then run the QA agent against each site:
qa-agent http://localhost:8080/console-error-site/ --max-pages 1 --provider mock
qa-agent http://localhost:8080/overflow-site/ --max-pages 1 --provider mock
qa-agent http://localhost:8080/broken-image-site/ --max-pages 1 --provider mock
```

### Expected detections per site

| Test Site | Expected Findings |
|-----------|------------------|
| `console-error-site` | 3+ console errors (CRITICAL/HIGH) |
| `broken-image-site` | 2+ broken images (MEDIUM), 1 missing alt (LOW) |
| `overflow-site` | 1+ horizontal overflow (MEDIUM/HIGH) |
| `broken-link-site` | 2+ broken links (MEDIUM) |
| `responsive-bug-site` | 1+ responsive overflow (HIGH) |
| `accessibility-bug-site` | 3+ axe violations (various) |
| `visual-layout-site` | 1+ visual defects (MEDIUM) from VLM |

## Mock vs Real VLM

All unit tests use `MockVisionProvider` by default. This ensures:
- Tests run without API credits
- Tests are deterministic
- The full pipeline is exercised

To run with real VLM:
```bash
VLM_PROVIDER=openai VLM_API_KEY=your_key python -m pytest tests/
```

## Writing New Tests

### Validator test pattern

```python
@pytest.mark.asyncio
async def test_detects_my_issue():
    validator = MyValidator()
    page_state = PageState(
        url="https://example.com",
        console_messages=[ConsoleMessage(level="error", text="my error")],
        ...
    )
    findings = await validator.validate(page_state)
    assert len(findings) > 0
    assert findings[0].category == Category.CONSOLE
```

### Important: Do not fabricate test results

Never write tests that assert a finding is present without verifying
the detection logic actually works. Use real (mock) page state data
that matches the condition you're testing.

## CI Considerations

- Unit tests can run in any CI environment (no browser needed)
- Integration tests require Playwright browsers: `playwright install chromium`
- VLM tests: set `VLM_PROVIDER=mock` in CI environment variables
- Never commit real API keys — use CI secrets
