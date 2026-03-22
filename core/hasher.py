import hashlib
import json
from pathlib import Path

CACHE_FILENAME = "cache_store.json"

def compute_raw_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def compute_structural_hash(normalized_obj) -> str:
    stable_json = json.dumps(normalized_obj, sort_keys=True)
    return hashlib.sha256(stable_json.encode("utf-8")).hexdigest()

def load_cache(project_path: Path) -> dict:
    cache_file = project_path / CACHE_FILENAME
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    return {}

def save_cache(project_path: Path, cache_data: dict, console=None):
    cache_file = project_path / CACHE_FILENAME
    cache_file.write_text(json.dumps(cache_data, indent=4), encoding="utf-8")