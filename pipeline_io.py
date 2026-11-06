"""Tiny helpers shared by all steps: where data lives and how to read/write step files."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"       # tests point this at a temp folder
PROMPTS_DIR = ROOT / "prompts"


def step_path(domain, filename):
    return DATA_DIR / domain / filename


def load_json(domain, filename):
    return json.loads(step_path(domain, filename).read_text(encoding="utf-8"))


def save_json(domain, filename, obj):
    path = step_path(domain, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def exists(domain, filename):
    return step_path(domain, filename).exists()


def load_prompt(name):
    """Prompts live in prompts/<name> as plain text files so you can edit them."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")
