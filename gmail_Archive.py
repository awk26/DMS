"""Legacy entry point kept for compatibility; the application entry point is app.py."""
from pathlib import Path

if __name__ == "__main__":
    import runpy
    runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
else:
    from app import app  # noqa: F401
