# Entry point kept for backwards compatibility.
# The app has been modularised into the app/ package.
# Run with: uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
from app.main import app  # noqa: F401
