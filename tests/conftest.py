"""Tests must never read the real .env (it holds real keys and a real dashboard password)."""
import dotenv

dotenv.load_dotenv = lambda *a, **k: False  # runs before any project module is imported
