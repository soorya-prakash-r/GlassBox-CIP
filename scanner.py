# scanner.py

import os
import json
import threading
import time
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from gradio_client import Client
from dotenv import load_dotenv

from parser_engine import parse_file
from hasher import compute_raw_hash, compute_structural_hash

load_dotenv()

CACHE_FILENAME = "cache_store.json"
HASH_FOLDER    = "hash_store"
MAX_WORKERS    = 2


# ── Terminal Colors ───────────────────────────────────────────────────────
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    GRAY = "\033[90m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

    @staticmethod
    def disable():
        for attr in dir(Colors):
            if not attr.startswith("_") and attr.isupper():
                setattr(Colors, attr, "")

if sys.platform == "win32" and not os.environ.get("ANSICON"):
    Colors.disable()


# ── Helper: Print with auto-flush ─────────────────────────────────────────
def log(msg, color=Colors.RESET, end="\n"):
    print(f"{color}{msg}{Colors.RESET}", end=end, flush=True)


# ── Thread-local Gradio client ────────────────────────────────────────────

_local = threading.local()
_client_lock = threading.Lock()

def _get_client() -> Client:
    if not hasattr(_local, "client"):
        with _client_lock:
            if not hasattr(_local, "client"):
                log("  → Connecting to Gradio space...", Colors.GRAY)
                import io, contextlib
                with contextlib.redirect_stdout(io.StringIO()):
                    _local.client = Client("soorya-123/GlassBox-Model")
                log("  ✓ Connected", Colors.GRAY)
    return _local.client


# ── Cache helpers ─────────────────────────────────────────────────────────

def load_cache(output_base: Path) -> dict:
    cache_file = output_base / CACHE_FILENAME
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠ Warning: Could not load cache: {e}", Colors.YELLOW)
    return {}

def save_cache(output_base: Path, cache_data: dict):
    try:
        with open(output_base / CACHE_FILENAME, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=4)
    except Exception as e:
        log(f"✗ Error saving cache: {e}", Colors.RED)

def save_hash(output_base: Path, relative_path: Path, struct_hash: str):
    try:
        hash_dir = output_base / HASH_FOLDER / relative_path.parent
        hash_dir.mkdir(parents=True, exist_ok=True)
        with open(hash_dir / (relative_path.name + ".hash.json"), "w", encoding="utf-8") as f:
            json.dump({"struct_hash": struct_hash}, f, indent=4)
    except Exception as e:
        log(f"⚠ Warning: Could not save hash for {relative_path}: {e}", Colors.YELLOW)


# ── API call ──────────────────────────────────────────────────────────────

def _call_api(input_text: str) -> str:
    try:
        client = _get_client()
        return client.predict(input_text=input_text, api_name="/summarize")
    except Exception as e:
        log(f"✗ API error: {type(e).__name__}: {e}", Colors.RED)
        return ""

def _parse_result(result: str) -> tuple[str, str]:
    if not result or not result.strip():
        return "", ""
    
    if "One-liner:" in result and "Detailed Summary:" in result:
        one_liner = result.split("One-liner:")[1].split("Detailed Summary:")[0].strip()
        detailed  = result.split("Detailed Summary:")[1].strip()
    else:
        one_liner = result.strip()
        detailed  = result.strip()
    return one_liner, detailed


# ── Parallel summarization ────────────────────────────────────────────────

def generate_summaries(normalized_objs: list) -> tuple[str, str]:
    inputs  = [obj["input"] for obj in normalized_objs]
    results = {}
    
    log(f"  → Generating {len(inputs)} summaries (max {MAX_WORKERS} concurrent)...", Colors.GRAY)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_idx = {
            executor.submit(_call_api, text): idx
            for idx, text in enumerate(inputs)
        }
        
        completed = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            completed += 1
            try:
                result = future.result()
                results[idx] = result
                status = f"{Colors.GREEN}✓{Colors.RESET}" if result.strip() else f"{Colors.YELLOW}⚠ empty{Colors.RESET}"
                log(f"    [{completed}/{len(inputs)}] Summary {status}", Colors.GRAY, end="\n")
            except Exception as e:
                log(f"    [{completed}/{len(inputs)}] ✗ Failed: {e}", Colors.RED, end="\n")
                results[idx] = ""

    log("  ✓ All summaries complete", Colors.GRAY)

    one_liners, detailed_parts = [], []
    for i in range(len(inputs)):
        ol, det = _parse_result(results.get(i, ""))
        one_liners.append(ol)
        detailed_parts.append(det)

    return "\n".join(one_liners), "\n\n".join(detailed_parts).strip()


# ── Single file processor ─────────────────────────────────────────────────

def _process_file(full_path: Path, project_path: Path,
                  output_base: Path, cached_entry: dict | None) -> dict | None:
    
    # Step 1: Read file
    log("  ├─ Reading file... ", Colors.GRAY, end="")
    try:
        source_code = full_path.read_text(encoding="utf-8")
        log(f"✓ ({len(source_code)} chars)", Colors.GREEN)
    except Exception as e:
        log(f"✗ ({e})", Colors.RED)
        return None

    # Step 2: Check raw hash
    raw_hash = compute_raw_hash(source_code)
    if cached_entry and cached_entry.get("raw_hash") == raw_hash:
        log("  └─ ⤷ Unchanged (skipped)", Colors.YELLOW)
        return None

    # Step 3: Parse file
    log("  ├─ Parsing AST... ", Colors.GRAY, end="")
    try:
        normalized_objs = parse_file(str(full_path), source_code)
        log(f"✓ ({len(normalized_objs)} objects)", Colors.GREEN)
    except Exception as e:
        log(f"✗ ({e})", Colors.RED)
        return None

    if not normalized_objs:
        log("  └─ ⤷ No parseable objects found", Colors.YELLOW)
        return None

    # Step 4: Compute structural hash
    struct_hash = compute_structural_hash(normalized_objs)

    # Step 5: Generate summaries via API
    log("  ├─ Calling AI summarizer...", Colors.GRAY)
    one_liner, detailed_summary = generate_summaries(normalized_objs)

    # Step 6: Prepare output
    relative    = full_path.relative_to(project_path)
    output_file = output_base / relative.parent / (relative.name + ".exp.txt")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    one_liner_list = one_liner.strip().split("\n")          if one_liner.strip()        else []
    detailed_list  = detailed_summary.strip().split("\n\n") if detailed_summary.strip() else []

    # Step 7: Write output file
    log("  ├─ Writing output... ", Colors.GRAY, end="")
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            for i, obj in enumerate(normalized_objs):
                name        = obj.get("name") or f"{obj.get('type', 'object')}_{i + 1}"
                params      = obj.get("parameters") or "None"
                return_type = obj.get("return_type") or "None"
                obj_type    = obj.get("type", "object").replace("_", " ").title()
                overview    = one_liner_list[i].strip() if i < len(one_liner_list) else "N/A"
                summary     = detailed_list[i].strip()  if i < len(detailed_list)  else "N/A"

                formatted = (
                    summary
                    .replace("! ", "!\n")
                    .replace("? ", "?\n")
                    .replace(". ", ".\n")
                )

                f.write(f"🔹 {obj_type}: {name}\n\n")
                f.write("Overview:\n")
                f.write(f"{overview}\n\n")
                f.write("Signature:\n")
                f.write(f"{name}({params}) -> {return_type}\n\n")
                f.write("Summary:\n")
                for line in formatted.split("\n"):
                    if line.strip():
                        f.write(f"- {line.strip()}\n")
                f.write("\n")
                f.write("=" * 60 + "\n\n")
        log("✓", Colors.GREEN)
    except Exception as e:
        log(f"✗ ({e})", Colors.RED)
        return None

    # Step 8: Save hash
    save_hash(output_base, relative, struct_hash)
    
    log(f"  └─ Saved: {output_file.relative_to(output_base)}", Colors.GRAY)
    return {"raw_hash": raw_hash, "struct_hash": struct_hash}


# ── Main scanner ──────────────────────────────────────────────────────────

def run_glassbox(project_path: str, output_root: str = "glassbox"):
    project_path = Path(project_path).resolve()
    output_base  = Path(output_root) / project_path.name
    output_base.mkdir(parents=True, exist_ok=True)

    log("GlassBox Scanner", Colors.BOLD)
    log("─" * 50, Colors.GRAY)
    log(f"Project     : {project_path}", Colors.BLUE)
    log(f"Output      : {output_base}", Colors.BLUE)

    cache_data = load_cache(output_base)

    # Find target files
    target_files = [
        Path(root) / file
        for root, _, files in os.walk(project_path)
        for file in files
        if file.endswith((".py", ".java", ".cpp", ".c"))
    ]

    if not target_files:
        log("✗ No supported source files found (.py, .java, .cpp, .c)", Colors.RED)
        return

    # Check cache status
    pending, skipped = [], 0
    for fp in target_files:
        cached = cache_data.get(str(fp))
        if cached:
            try:
                src = fp.read_text(encoding="utf-8")
                if cached.get("raw_hash") == compute_raw_hash(src):
                    skipped += 1
                    continue
            except Exception:
                pass
        pending.append(fp)

    log(f"Files found : {len(target_files)}")
    log(f"Unchanged   : {skipped} (skipped)", Colors.YELLOW)
    log(f"To process  : {len(pending)}", Colors.GREEN)
    log("─" * 50, Colors.GRAY)
    log("")

    if not pending:
        log("✓ Everything is up to date. Nothing to process.", Colors.GREEN)
        return

    # Process files with progress
    files_processed = 0
    files_failed = 0
    start_time = time.time()

    for idx, full_path in enumerate(pending, 1):
        log(f"\n[{idx}/{len(pending)}] Processing {full_path.name}...", Colors.BOLD)
        
        cached_entry = cache_data.get(str(full_path))
        result = _process_file(full_path, project_path, output_base, cached_entry)

        if result:
            cache_data[str(full_path)] = result
            files_processed += 1
        else:
            files_failed += 1

    # Save cache
    save_cache(output_base, cache_data)

    # Final summary
    elapsed = time.time() - start_time
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    log("", Colors.RESET)
    log("─" * 50, Colors.GRAY)
    log("Summary", Colors.BOLD)
    log(f"Files processed : {files_processed}", Colors.GREEN)
    if files_failed > 0:
        log(f"Files failed    : {files_failed}", Colors.RED)
    if mins > 0:
        log(f"Total time      : {mins}m {secs}s", Colors.BLUE)
    else:
        log(f"Total time      : {secs}s", Colors.BLUE)
    log("✓ Completed GlassBox documentation successfully.", Colors.GREEN)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        log("GlassBox v1.0\n", Colors.BOLD + Colors.GREEN)
        path = input("Enter project folder path: ").strip()
        if not path:
            log("✗ No path provided. Exiting.", Colors.RED)
            sys.exit(1)
        
        if not os.path.isdir(path):
            log(f"✗ Invalid path: {path}", Colors.RED)
            sys.exit(1)
            
        run_glassbox(path)
    except KeyboardInterrupt:
        log("\n⚠ Interrupted by user. Exiting.", Colors.YELLOW)
        sys.exit(130)
    except Exception as e:
        log(f"✗ Unexpected error: {type(e).__name__}: {e}", Colors.RED)
        import traceback
        traceback.print_exc()
        sys.exit(1)