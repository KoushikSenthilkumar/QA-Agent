"""
CLI package.

Lazy import to prevent RuntimeWarning when running via
`python -m qa_agent.cli.main` (which re-executes the module).
"""

__all__ = ["cli", "main"]


def __getattr__(name: str):
    if name in ("cli", "main"):
        from qa_agent.cli import main as _main_module
        return getattr(_main_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
