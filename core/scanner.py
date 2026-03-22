"""
scanner.py
Core orchestration engine for GlassBox.

Pipeline:
  1. Parse each source file → normalized component objects
  2. Per component  → get_component_summary()   (one-liner + detailed)
  3. Per file       → get_file_summary()         (one-liner from component summaries)
  4. Per project    → get_project_summary()      (one-liner from file summaries)

Output structure under glassbox/<project_name>/:
  project_summary.exp.txt       <- project-level summary + file index
  architecture_diagram.png      <- dependency graph image
  <mirrored_dir>/<file>.exp.txt <- per-file: file summary + component docs
"""

from __future__ import annotations

import ast as ast_module
import json
import os
import re
import time, textwrap, math
from pathlib import Path
from typing import Optional

from core.hasher import compute_raw_hash, compute_structural_hash
from core.parser_engine import parse_file
from glassbox_client import (
    get_component_summary,
    get_file_summary,
    get_project_summary,
    warmup,
)

# ── Constants ──────────────────────────────────────────────────────────────────
CACHE_FILENAME   = "cache_store.json"
HASH_FOLDER      = "hash_store"
API_RETRIES      = 5
RETRY_BASE_DELAY = 2.0   # seconds; doubles per retry
DEBUG_RAW        = False  # set True to dump raw API responses to stdout

_SKIP_TYPES = {"error_block"}
_SKIP_NAMES = {"__init__", "init"}

_TYPE_ICONS: dict[str, str] = {
    "class":            "🏛",
    "interface":        "📐",
    "enum":             "🔖",
    "struct":           "📦",
    "function":         "⚙",
    "method":           "🔧",
    "constructor":      "🔨",
    "generic_function": "🧩",
    "generic_method":   "🧩",
}
_DEFAULT_ICON = "🔹" 

# ══════════════════════════════════════════════════════════════════════════════
#  Summary Validator
# ══════════════════════════════════════════════════════════════════════════════
class SummaryValidator:
    """Light-weight post-hoc validator to flag likely hallucinations."""

    _HALLUCINATION_PATTERNS = [
        (r"implements.*interface", "interface_claim"),
        (r"try-with-resources",    "try_resources"),
        (r"extends.*Exception",    "exception_claim"),
    ]
    _REQUIRED_KW: dict[str, list[str]] = {
        "constructor": ["initialize", "construct", "create"],
        "getter":      ["return", "get"],
        "setter":      ["set", "assign"],
        "enum":        ["enum", "constant", "value"],
    }

    @classmethod
    def validate(
        cls, summary: str, input_text: str
    ) -> tuple[bool, float, list[str]]:
        issues:     list[str] = []
        confidence: float     = 1.0

        for pattern, issue_type in cls._HALLUCINATION_PATTERNS:
            if re.search(pattern, summary, re.IGNORECASE):
                if not cls._verify_claim(pattern, input_text):
                    issues.append(f"Potential hallucination: {issue_type}")
                    confidence -= 0.3

        if "constructor" in input_text.lower():
            if not any(kw in summary.lower() for kw in cls._REQUIRED_KW["constructor"]):
                issues.append("Missing constructor keywords")
                confidence -= 0.2

        if len(summary.split()) < 8:
            issues.append("Summary too short")
            confidence -= 0.3

        words = summary.lower().split()
        if words and len(set(words)) / len(words) < 0.5:
            issues.append("High repetition detected")
            confidence -= 0.2

        return confidence >= 0.6, max(0.0, confidence), issues

    @classmethod
    def _verify_claim(cls, pattern: str, input_text: str) -> bool:
        if "interface" in pattern:
            return "interface" in input_text.lower()
        if "try-with-resources" in pattern:
            return "try(" in input_text
        return True


# ══════════════════════════════════════════════════════════════════════════════
#  Response parsers
# ══════════════════════════════════════════════════════════════════════════════ 

# Patterns that mean the model started writing code instead of prose
_CODE_BLEED_PATTERNS = [
    r"\n\s*def ",
    r"\n\s*class ",
    r"\n\s*import ",
    r"\n\s*from ",
    r"\n\s*//",
    r"\n\s*/\*",
    r"```",
    r"<\|",
]
 
def _clean_raw_output(text: str) -> str:
    """
    Cut at the first line that looks like code.
    Only cuts on LINE boundaries — never mid-sentence.
    """
    if not text:
        return ""
    for pattern in _CODE_BLEED_PATTERNS:
        m = re.search(pattern, text)
        if m:
            text = text[:m.start()]
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
 
 
def _extract_one_liner(raw: str) -> str:
    """Clean and return a single sentence from raw Pass 1 output."""
    raw = _clean_raw_output(raw)
    if not raw:
        return "No summary available."
 
    # Take only the first line
    first_line = raw.split("\n")[0].strip()
 
    # Cut at second sentence if present
    m = re.search(r'(?<=[a-zA-Z])\.\s+(?=[A-Z])', first_line)
    if m:
        first_line = first_line[:m.start() + 1]
 
    # Ensure starts with "This"
    if not first_line.lower().startswith("this"):
        first_line = "This " + first_line
 
    return first_line.rstrip(".,;").strip()
 
 
def _extract_detailed(raw: str) -> str:
    """
    Convert raw Pass 2 output into a clean numbered list.
    One fact per line, duplicates removed.
    """
    raw = _clean_raw_output(raw)
    if not raw:
        return ""
 
    # Split on newlines or sentence boundaries
    parts = re.split(r"\n+|\.\s+(?=[A-Z1-9])", raw)
 
    seen:   set[str] = set()
    points: list[str] = []
 
    for p in parts:
        p = p.strip()
 
        # Remove existing numbering like "1." or "1)"
        p = re.sub(r"^\d+[\.\)]\s*", "", p).strip()
 
        # Remove weak openers
        p = re.sub(
            r"^(First|Second|Third|Then|Also|Finally|Additionally|Note)[,:\s]+",
            "", p, flags=re.IGNORECASE
        ).strip()
 
        # Skip too short or duplicate
        if len(p) < 10:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
 
        p = p if p.endswith(".") else p + "."
        points.append(p)
 
    if not points:
        return ""
 
    return "\n".join(f"{i+1}. {pt}" for i, pt in enumerate(points[:6]))
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  REPLACE these 3 existing functions in scanner.py
# ══════════════════════════════════════════════════════════════════════════════
 
def _parse_component_result(
    result: str, input_text: str = ""
) -> tuple[str, str, float]:
    """
    Parse raw response from Kaggle API.
    Handles new RAW format and old One-liner format as fallback.
    """
    if not result or not result.strip():
        return "No summary available.", "", 0.0
 
    text       = result.strip()
    one_liner  = ""
    detailed   = ""
    confidence = 1.0
 
    # ── New format: RAW_ONE_LINER:...|||RAW_DETAILED:... ──────────────────
    if "RAW_ONE_LINER:" in text and "RAW_DETAILED:" in text:
        raw1      = text.split("RAW_ONE_LINER:", 1)[1].split("|||", 1)[0].strip()
        raw2      = text.split("RAW_DETAILED:",  1)[1].strip()
        one_liner = _extract_one_liner(raw1)
        detailed  = _extract_detailed(raw2)
        confidence = 1.0
 
    # ── Old format fallback: "One-liner:\n...\n\nDetailed Summary:\n..." ──
    elif "One-liner:" in text:
        after_ol  = text.split("One-liner:", 1)[1]
        parts     = after_ol.split("Detailed Summary:", 1)
        one_liner = _extract_one_liner(parts[0])
        detailed  = _extract_detailed(parts[1]) if len(parts) > 1 else ""
        confidence = 0.8
 
    # ── Plain text fallback ────────────────────────────────────────────────
    else:
        lines     = [ln.strip() for ln in text.splitlines() if ln.strip()]
        one_liner = _extract_one_liner(lines[0]) if lines else "No summary available."
        detailed  = _extract_detailed("\n".join(lines[1:])) if len(lines) > 1 else ""
        confidence = 0.5
 
    if not one_liner:
        one_liner  = "No summary available."
        confidence = max(0.0, confidence - 0.3)
 
    if input_text:
        _, conf, _ = SummaryValidator.validate(f"{one_liner}\n{detailed}", input_text)
        confidence = min(confidence, conf)
 
    return one_liner, detailed, confidence
 
 
def _parse_file_result(result: str) -> str:
    """Extract file one-liner from Kaggle response."""
    if not result or not result.strip():
        return "No file summary available."
    text = result.strip()
    if "RAW_FILE:" in text:
        return _extract_one_liner(text.split("RAW_FILE:", 1)[1].strip())
    if "File One-liner:" in text:
        return _extract_one_liner(text.split("File One-liner:", 1)[1].strip().split("\n")[0])
    return _extract_one_liner(text.split("\n")[0])
 
 
def _parse_project_result(result: str) -> str:
    """Extract project one-liner from Kaggle response."""
    if not result or not result.strip():
        return "No project summary available."
    text = result.strip()
    if "RAW_PROJECT:" in text:
        return _extract_one_liner(text.split("RAW_PROJECT:", 1)[1].strip())
    if "Project One-liner:" in text:
        return _extract_one_liner(text.split("Project One-liner:", 1)[1].strip().split("\n")[0])
    return _extract_one_liner(text.split("\n")[0])

# ══════════════════════════════════════════════════════════════════════════════
#  Cache / Hash helpers
# ══════════════════════════════════════════════════════════════════════════════
def load_cache(output_base: Path, console) -> dict:
    cache_file = output_base / CACHE_FILENAME
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            console.print(f"Warning: Could not load cache: {e}", style="yellow")
    return {}


def save_cache(output_base: Path, cache_data: dict, console):
    try:
        with open(output_base / CACHE_FILENAME, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=4)
    except Exception as e:
        console.print(f"Error saving cache: {e}", style="bold red")


def save_hash(output_base: Path, relative_path: Path, struct_hash: str, console):
    try:
        hash_dir = output_base / HASH_FOLDER / relative_path.parent
        hash_dir.mkdir(parents=True, exist_ok=True)
        target = hash_dir / (relative_path.name + ".hash.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"struct_hash": struct_hash}, f, indent=4)
    except Exception as e:
        console.print(f"Warning: Could not save hash for {relative_path}: {e}", style="yellow")


def _needs_processing(
    fp: Path, cache_data: dict, source_code: str
) -> tuple[bool, str]:
    """
    Returns (should_process, reason).
    reason: "new file" | "structure changed" | "whitespace/comment only" | "unchanged"
    """
    cached = cache_data.get(str(fp))
    if not cached:
        return True, "new file"

    raw_hash = compute_raw_hash(source_code)
    if cached.get("raw_hash") == raw_hash:
        return False, "unchanged"

    # Raw hash changed — deeper structural check
    try:
        normalized_objs = parse_file(fp, source_code)
        struct_hash = compute_structural_hash(normalized_objs)
        if cached.get("struct_hash") == struct_hash:
            cached["raw_hash"] = raw_hash     # update raw hash in place
            cache_data[str(fp)] = cached
            return False, "whitespace/comment only"
    except Exception:
        pass

    return True, "structure changed"


# ══════════════════════════════════════════════════════════════════════════════
#  Component-level summarization
# ══════════════════════════════════════════════════════════════════════════════
def _summarize_one(obj: dict, console) -> tuple[str, str, float]:
    """Call get_component_summary() with retry logic.
    Retries specifically if detailed summary is empty/bad."""
    code_context = obj.get("code_block") or obj.get("input", "")
    input_text   = obj.get("input", "")

    for attempt in range(1, API_RETRIES + 1):
        try:
            result = get_component_summary(code_context)
            one_liner, detailed, confidence = _parse_component_result(result, input_text)

            # ── Retry if detailed is missing ──────────────────────────────
            is_bad_detailed = (
                not detailed.strip()
                or "did not produce" in detailed.lower()
                or len(detailed.strip()) < 20
            )
            if is_bad_detailed and attempt < API_RETRIES:
                console.print(
                    f"    ⚠ Empty detailed (attempt {attempt}/{API_RETRIES}) — retrying...",
                    style="yellow"
                )
                time.sleep(RETRY_BASE_DELAY)
                continue

            return one_liner, detailed, confidence

        except Exception as exc:
            if attempt < API_RETRIES:
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                console.print(
                    f"    ⚠ API error (attempt {attempt}/{API_RETRIES}): {exc}"
                    f" — retrying in {int(wait)}s",
                    style="yellow"
                )
                time.sleep(wait)

    console.print(
        f"    ✗ API failed after {API_RETRIES} attempts",
        style="bold red"
    )
    return "No summary available.", "", 0.0


def generate_summaries(
    filtered_objs: list, console
) -> list[tuple[str, str, float]]:
    """
    Return one (one_liner, detailed, confidence) per object.
    Sequential — safe for free Kaggle single-GPU tier.
    """
    if not filtered_objs:
        return []

    total = len(filtered_objs)
    console.print(f"  Generating {total} component summaries...", style="dim")

    results: list[tuple[str, str, float]] = []
    for idx, obj in enumerate(filtered_objs, 1):
        name     = obj.get("name", "unknown")
        obj_type = obj.get("type", "unknown")
        console.print(f"    [{idx}/{total}] {obj_type}: {name} ...", style="dim", end="")

        one_liner, detailed, confidence = _summarize_one(obj, console)
        results.append((one_liner, detailed, confidence))

        ok    = one_liner != "No summary available."
        style = "green" if ok else "yellow"
        console.print(" OK" if ok else " WARN", style=style)

    console.print("  Component summaries complete", style="green")
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  File-level summarization
# ══════════════════════════════════════════════════════════════════════════════
def generate_file_summary(
    file_path:    Path,
    filtered_objs: list,
    summaries:    list[tuple[str, str, float]],
    console,
) -> str:
    """Combine component one-liners and call get_file_summary()."""
    one_liners: list[str] = []
    for obj, (one_liner, _, _) in zip(filtered_objs, summaries):
        if one_liner and one_liner != "No summary available.":
            name     = obj.get("name", "unknown")
            obj_type = obj.get("type", "unknown")
            one_liners.append(f"{name} ({obj_type}): {one_liner}")

    if not one_liners:
        names = ", ".join(obj.get("name", "?") for obj in filtered_objs[:5])
        return f"File containing: {names}." if names else "No components found."

    try:
        result = get_file_summary("\n".join(one_liners), file_path.name)
        return _parse_file_result(result)
    except Exception as e:
        console.print(f"  Warning: File summary API error: {e}", style="yellow")
        return one_liners[0].split(": ", 1)[-1] if one_liners else "No file summary."


# ══════════════════════════════════════════════════════════════════════════════
#  Project-level summarization
# ══════════════════════════════════════════════════════════════════════════════
def generate_project_summary(
    project_name:  str,
    file_summaries: dict[str, str],
    console,
) -> str:
    """Combine file one-liners and call get_project_summary()."""
    lines = [
        f"{fname}: {summary}"
        for fname, summary in file_summaries.items()
        if summary
    ]
    if not lines:
        return "No file summaries available."

    try:
        result = get_project_summary("\n".join(lines), project_name)
        return _parse_project_result(result)
    except Exception as e:
        console.print(f"  Warning: Project summary API error: {e}", style="yellow")
        return f"Project {project_name} with {len(lines)} documented files."


# ══════════════════════════════════════════════════════════════════════════════
#  Architecture / dependency diagram
# ══════════════════════════════════════════════════════════════════════════════
def _extract_python_imports(
    source: str, file_path: Path, project_path: Path
) -> list[str]:
    imports: list[str] = []
    try:
        tree = ast_module.parse(source)
        for node in ast_module.walk(tree):
            if isinstance(node, ast_module.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast_module.ImportFrom) and node.module:
                if node.level > 0:
                    parent = file_path.parent
                    for _ in range(node.level - 1):
                        parent = parent.parent
                    try:
                        rel_parent = parent.relative_to(project_path)
                        prefix = str(rel_parent).replace(os.sep, ".")
                        mod = f"{prefix}.{node.module}" if prefix and prefix != "." else node.module
                        imports.append(mod)
                    except ValueError:
                        imports.append(node.module)
                else:
                    imports.append(node.module)
    except Exception:
        pass
    return imports


def _extract_java_imports(source: str) -> list[str]:
    return [
        m.group(1)
        for m in re.finditer(r"^\s*import\s+([\w.]+)\s*;", source, re.MULTILINE)
    ]


def generate_architecture_diagram(
    project_path: Path,
    output_base:  Path,
    target_files: list[Path],
    console,
):
    """
    Build a file-dependency DiGraph and save as architecture_diagram.png.
    Nodes = source files (coloured by language).
    Edges = A -> B  iff  file A imports a module defined by file B.
    """
    try:
        import networkx as nx
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        console.print(
            f"  Warning: Dependency graph skipped — missing library: {exc}\n"
            "    Install with: pip install networkx matplotlib",
            style="yellow",
        )
        return

    console.print("  Building dependency graph...", style="dim")

    # module dotted-name -> relative path string
    module_to_rel: dict[str, str] = {}
    for fp in target_files:
        rel     = fp.relative_to(project_path)
        rel_str = str(rel)
        mod     = str(rel.with_suffix("")).replace(os.sep, ".").replace("/", ".")
        module_to_rel[mod] = rel_str
        # short name fallback  (e.g. "parser_engine" for "core.parser_engine")
        short = mod.split(".")[-1]
        if short not in module_to_rel:
            module_to_rel[short] = rel_str

    G: nx.DiGraph = nx.DiGraph()
    for fp in target_files:
        G.add_node(str(fp.relative_to(project_path)))

    for fp in target_files:
        src_rel = str(fp.relative_to(project_path))
        try:
            source = fp.read_text(encoding="utf-8")
        except Exception:
            continue

        raw_imports: list[str] = []
        if fp.suffix == ".py":
            raw_imports = _extract_python_imports(source, fp, project_path)
        elif fp.suffix == ".java":
            raw_imports = _extract_java_imports(source)

        for imp in raw_imports:
            if imp in module_to_rel and module_to_rel[imp] != src_rel:
                G.add_edge(src_rel, module_to_rel[imp])
            else:
                for mod, tgt in module_to_rel.items():
                    if imp.startswith(mod + ".") and tgt != src_rel:
                        G.add_edge(src_rel, tgt)
                        break

    G.remove_edges_from(list(nx.selfloop_edges(G)))

    if G.number_of_nodes() == 0:
        console.print("  Warning: No graph nodes — diagram skipped", style="yellow")
        return

    n = G.number_of_nodes()
    # Layout
    try:
        if nx.is_directed_acyclic_graph(G):
            pos = nx.nx_agraph.graphviz_layout(G, prog="dot")
        else:
            pos = nx.spring_layout(
                G,
                k=0.9,           # ← smaller k = nodes pulled closer together
                seed=42,
                iterations=300,  # ← more iterations = tighter final positions
                scale=1.0        # ← scale=1.0 instead of 2.0
            )
    except Exception:
        pos = nx.spring_layout(G, k=0.9, seed=42, scale=1.0)

    # ── Normalize positions into a tight bounding box ───────────────────────
    xs = [x for x, y in pos.values()]
    ys = [y for x, y in pos.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = max(x_max - x_min, 1)
    y_range = max(y_max - y_min, 1)

    # Remap all positions into [0.1, 0.9] range
    pos = {
        node: (
            0.1 + 0.8 * (x - x_min) / x_range,
            0.1 + 0.8 * (y - y_min) / y_range,
        )
        for node, (x, y) in pos.items()
    }

    _EXT_COLOR = {".py": "#66BB6A", ".java": "#42A5F5"}
    node_colors = [_EXT_COLOR.get(Path(nd).suffix, "#BDBDBD") for nd in G.nodes]

    node_size = 2800
    fig, ax   = plt.subplots(figsize=(7, 5))    # ← smaller fixed canvas
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=node_size, alpha=0.92, ax=ax)

    # Draw labels manually — filename only, wrapped to stay inside node
    for node, (x, y) in pos.items():
        label   = Path(node).stem.replace("_", " ")
        wrapped = "\n".join(textwrap.wrap(label, width=10, break_long_words=False))
        ax.text(
            x, y, wrapped,
            ha="center", va="center",
            fontsize=7.5, fontweight="bold",
            color="black", multialignment="center",
            linespacing=1.3, zorder=5,
        )

    if G.number_of_edges():
        margin = int(math.sqrt(node_size) * 0.5)
        nx.draw_networkx_edges(
            G, pos,
            edge_color="#546E7A", arrows=True, arrowsize=14,
            width=1.4, ax=ax, connectionstyle="arc3,rad=0.12",
            min_source_margin=margin,
            min_target_margin=margin,
        )

    stats = f"Files: {G.number_of_nodes()}   Dependencies: {G.number_of_edges()}"
    stats = f"Files: {G.number_of_nodes()}     Dependencies: {G.number_of_edges()}"
    fig.text(
        0.99, 0.01, stats,
        fontsize=9, ha="right", va="bottom",
        color="#444", fontweight="bold",
        transform=fig.transFigure   # ← figure coords, not axes coords
    )

    ax.set_title(
        f"Dependency Graph  |  {project_path.name}",
        fontsize=15, fontweight="bold", pad=18,
    )
    ax.axis("off")

    out_path = output_base / "dependency-graph.png"
    plt.tight_layout(pad=2.5)
    plt.subplots_adjust(bottom=0.08)
    plt.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    console.print(f"  Dependency graph saved: {out_path.name}", style="green")


# ══════════════════════════════════════════════════════════════════════════════
#  Output writers
# ══════════════════════════════════════════════════════════════════════════════
def _wrap_paragraph(text: str, words_per_line: int = 14) -> str:
    """
    Wrap a summary into readable paragraph lines of ~13-15 words.
    Prefers breaking at punctuation (. , ; :) but hard-breaks at word limit.
    Does NOT create numbered points — pure paragraph style.
    """
    if not text or not text.strip():
        return text
 
    # Flatten to single line first
    text  = " ".join(text.split())
    words = text.split()
 
    if len(words) <= words_per_line:
        return text   # short enough — no wrapping needed
 
    lines:   list[str] = []
    current: list[str] = []
 
    for word in words:
        current.append(word)
        ends_punct = word.rstrip().endswith((".", ",", ";", ":"))
        at_limit   = len(current) >= words_per_line
 
        if at_limit and ends_punct:
            lines.append(" ".join(current))
            current = []
        elif len(current) >= words_per_line + 3:   # hard cap at 17 words
            lines.append(" ".join(current))
            current = []
 
    if current:
        lines.append(" ".join(current))
 
    return "\n  ".join(lines)

def _extract_input_field(input_text: str, label: str) -> str:
    for line in input_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{label}:"):
            value = stripped[len(label) + 1:].strip()
            return value if value and value.lower() != "none" else ""
    return ""

def _fix_return_type(rt: str) -> str:
    if not rt:
        return "void"
    rt = rt.strip().rstrip(":")

    # Map truncated values (fallback safety net)
    _MAP = {
        "ne":      "None",
        "ol":      "bool",
        "oat":     "float",
        "r":       "str",
        "t":       "int",
        "ct":      "dict",
        "ne:":     "None",
        "ol:":     "bool",
        "oat:":    "float",
        "r:":      "str",
        "t:":      "int",
        "ct:":     "dict",
    }
    return _MAP.get(rt, rt)
 
 
def _format_signature(obj: dict) -> str:
    input_text  = obj.get("input", "")
    name        = obj.get("name", "unknown")
    obj_type    = obj.get("type", "")
    params      = _extract_input_field(input_text, "Parameters")
    return_type = _extract_input_field(input_text, "Return Type")
 
    if obj_type in ("class", "interface", "enum", "struct"):
        return f"{obj_type} {name}"
 
    # Clean params — remove artifacts like "elf", "lf", "-)", "->"
    _SKIP_PARAMS = {"self", "cls", "elf", "lf", "s"}
    parts = []
    for p in params.split(","):
        p = p.strip()
        # Remove trailing junk: "-)", "->", ")"
        p = re.sub(r'\s*[-=][\)>].*$', '', p).strip()
        p = p.strip(")")
        # Get just the name before colon for skip check
        param_name = p.split(":")[0].strip().lstrip("*")
        if not p or param_name.lower() in _SKIP_PARAMS:
            continue
        parts.append(p)
 
    params_str = ", ".join(parts) if parts else "none"
 
    is_constructor = (
        obj_type == "constructor"
        or (return_type.lower() in ("constructor", "void", "") and name[:1].isupper())
    )
    if is_constructor:
        return f"{name}({params_str})"
 
    rt = _fix_return_type(return_type)
    return f"{name}({params_str}) -> {rt}"


def write_file_output(
    output_file:   Path,
    relative_path: Path,
    filtered_objs: list,
    summaries:     list[tuple[str, str, float]],
    file_summary:  str,
    console,
):
    output_file.parent.mkdir(parents=True, exist_ok=True)
 
    with open(output_file, "w", encoding="utf-8") as f:
 
        # ── Header ─────────────────────────────────────────────────────────
        f.write(f"GlassBox Documentation  |  {relative_path}\n")
        f.write(f"Generated  : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Components : {len(summaries)}\n\n")
 
        # ── File summary ────────────────────────────────────────────────────
        f.write("FILE SUMMARY\n\n")
        f.write(f"  {_wrap_paragraph(file_summary)}\n\n\n")
 
        if not summaries:
            f.write("  No components found.\n")
            return
 
        # ── Components ──────────────────────────────────────────────────────
        f.write("COMPONENTS\n\n")
 
        for obj, (one_liner, detailed, confidence) in zip(filtered_objs, summaries):
            name     = obj.get("name", "unknown")
            raw_type = obj.get("type", "object")
            icon     = _TYPE_ICONS.get(raw_type.lower(), _DEFAULT_ICON)
            sig      = _format_signature(obj)
 
            # Component title
            f.write(f"{icon}  {name}  |  {raw_type}\n")
            f.write(f"    Signature : {sig}\n\n")
 
            # One-liner — paragraph style
            ol = one_liner if one_liner and one_liner != "No summary available." else "-"
            f.write(f"    Summary\n")
            f.write(f"      {_wrap_paragraph(ol)}\n\n")
 
            # Detailed — paragraph style, each point on new lines
            if detailed and detailed.strip():
                f.write(f"    Details\n")
                counter = 1
                for line in detailed.strip().splitlines():
                    # Strip existing numbering
                    clean = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
                    if not clean:
                        continue
                    # Split clean line into individual sentences
                    sentences = re.split(r'(?<=[a-zA-Z])\.\s+(?=[A-Z])', clean)
                    for sentence in sentences:
                        sentence = sentence.strip().rstrip(".")
                        if sentence:
                            f.write(f"      {counter}. {sentence}\n")
                            counter += 1
                f.write("\n")
 
            if confidence < 0.7:
                f.write("      [!] Low-confidence — manual review recommended\n\n")
 
            f.write("\n")   # space between components


def write_project_summary(
    output_base:     Path,
    project_name:    str,
    file_summaries:  dict[str, str],
    project_summary: str,
    console,
):
    out = output_base / "project_summary.exp.txt"
    try:
        with open(out, "w", encoding="utf-8") as f:
 
            # ── Header ─────────────────────────────────────────────────────
            f.write("GlassBox Project Summary\n")
            f.write(f"Generated : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Project   : {project_name}\n")
            f.write(f"Files     : {len(file_summaries)}\n\n\n")
 
            # ── Project summary — paragraph style ───────────────────────────
            f.write("PROJECT SUMMARY\n\n")
            f.write(f"  {_wrap_paragraph(project_summary)}\n\n\n")
 
            # ── Per-file summaries — paragraph style ────────────────────────
            f.write("FILE SUMMARIES\n\n")
            for fname, summary in sorted(file_summaries.items()):
                f.write(f"  {fname}\n")
                f.write(f"    {_wrap_paragraph(summary)}\n\n")
 
        console.print(f"  Project summary saved: {out.name}", style="green")
    except Exception as e:
        console.print(f"  Error: Failed to write project summary: {e}", style="bold red")


# ══════════════════════════════════════════════════════════════════════════════
#  Single-file processor
# ══════════════════════════════════════════════════════════════════════════════
def _process_file(
    full_path:    Path,
    project_path: Path,
    output_base:  Path,
    cache_data:   dict,
    console,
) -> Optional[dict]:
    """
    Parse -> component summaries -> file summary -> write .exp.txt.
    Returns a cache-entry dict on success (includes file_summary), else None.
    """
    try:
        source_code = full_path.read_text(encoding="utf-8")
    except Exception as e:
        console.print(f"  Error: Cannot read file: {e}", style="bold red")
        return None

    raw_hash = compute_raw_hash(source_code)

    console.print("  Parsing...", style="dim")
    try:
        all_objs = parse_file(full_path, source_code)
    except Exception as e:
        console.print(f"  Error: Parse failed: {e}", style="bold red")
        return None

    if not all_objs:
        console.print("  No components found.", style="yellow")
        return {"raw_hash": raw_hash, "struct_hash": None, "file_summary": "No components found."}

    struct_hash = compute_structural_hash(all_objs)

    # Filter once — shared by summarization + output writing
    filtered_objs = [
        obj for obj in all_objs
        if obj.get("type") not in _SKIP_TYPES
        and obj.get("name") not in _SKIP_NAMES
    ]

    # Component summaries
    summaries = generate_summaries(filtered_objs, console)

    # File summary
    console.print("  Generating file summary...", style="dim")
    file_summary = generate_file_summary(full_path, filtered_objs, summaries, console)

    # Write .exp.txt
    relative_path = full_path.relative_to(project_path)
    output_file   = output_base / relative_path.with_suffix(".exp.txt")

    console.print("  Writing output...", style="dim")
    try:
        write_file_output(
            output_file, relative_path,
            filtered_objs, summaries, file_summary, console,
        )
        console.print("  Output written", style="green")
    except Exception as e:
        console.print(f"  Error: Write failed: {e}", style="bold red")
        return None

    save_hash(output_base, relative_path, struct_hash, console)

    return {
        "raw_hash":    raw_hash,
        "struct_hash": struct_hash,
        "file_summary": file_summary,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ══════════════════════════════════════════════════════════════════════════════
def run_glassbox(project_path: str, console):
    """
    Full GlassBox scan:
      1. Discover source files
      2. Skip unchanged files via hash cache
      3. Per pending file: parse -> component summaries -> file summary -> .exp.txt
      4. Project summary  -> project_summary.exp.txt
      5. Architecture diagram -> architecture_diagram.png
    """
    project_path = Path(project_path).resolve()
    project_name = project_path.name
    output_base  = project_path.parent / "glassbox" / project_name
    output_base.mkdir(parents=True, exist_ok=True)

    console.print("\nGlassBox Scanner", style="bold")
    console.print("─" * 50, style="dim")
    console.print(f"Project  : {project_path}", style="cyan")
    console.print(f"Output   : {output_base}", style="cyan")

    # Discover source files
    target_files: list[Path] = [
        Path(root) / fname
        for root, _, files in os.walk(project_path)
        for fname in files
        if Path(fname).suffix in {".py", ".java"}
    ]

    if not target_files:
        console.print("Error: No supported source files found.", style="bold red")
        return

    cache_data = load_cache(output_base, console)

    # Classify: pending vs skipped
    pending:         list[Path] = []
    skipped:         int        = 0
    whitespace_only: int        = 0

    for fp in target_files:
        try:
            src = fp.read_text(encoding="utf-8")
        except Exception:
            pending.append(fp)
            continue
        should_process, reason = _needs_processing(fp, cache_data, src)
        if should_process:
            pending.append(fp)
        else:
            skipped += 1
            if reason == "whitespace/comment only":
                whitespace_only += 1

    console.print(f"\nFiles found      : {len(target_files)}")
    console.print(f"Skipped (cache)  : {skipped}", style="yellow")
    if whitespace_only:
        console.print(f"  whitespace only: {whitespace_only}", style="dim")
    console.print(f"To process       : {len(pending)}", style="green")
    console.print("─" * 50, style="dim")

    # Pre-populate file_summaries from cache (for incremental runs)
    file_summaries: dict[str, str] = {}
    for fp in target_files:
        if fp not in pending:
            cached = cache_data.get(str(fp))
            if cached and cached.get("file_summary"):
                file_summaries[str(fp.relative_to(project_path))] = cached["file_summary"]

    if not pending:
        project_summary_file = output_base / "project_summary.exp.txt"
        if file_summaries and not project_summary_file.exists():
            console.print("\nGenerating missing project summary...", style="dim")
            proj_sum = generate_project_summary(project_name, file_summaries, console)
            write_project_summary(output_base, project_name, file_summaries, proj_sum, console)
        save_cache(output_base, cache_data, console)
        console.print("Everything is up to date.", style="green")
        return

    # Warm up model once before batch processing
    console.print("\nConnecting to AI backend...", style="dim")
    warmup(console)

    # Process files sequentially
    files_processed = 0
    files_failed    = 0
    start_time      = time.time()

    for idx, full_path in enumerate(pending, 1):
        console.print(f"\n[{idx}/{len(pending)}] {full_path.name}", style="bold")
        result = _process_file(full_path, project_path, output_base, cache_data, console)

        if result:
            cache_data[str(full_path)] = result
            rel_str = str(full_path.relative_to(project_path))
            if result.get("file_summary"):
                file_summaries[rel_str] = result["file_summary"]
            files_processed += 1
        else:
            files_failed += 1

    save_cache(output_base, cache_data, console)

    # Project summary
    if file_summaries:
        console.print("\nGenerating project-level summary...", style="dim")
        proj_sum = generate_project_summary(project_name, file_summaries, console)
        write_project_summary(output_base, project_name, file_summaries, proj_sum, console)

    # Architecture diagram
    console.print("\nGenerating dependency graph...", style="dim")
    generate_architecture_diagram(project_path, output_base, target_files, console)

    elapsed = int(time.time() - start_time)
    console.print("\n" + "─" * 50, style="dim")
    console.print("Summary", style="bold")
    console.print(f"  Processed : {files_processed}", style="green")
    if files_failed:
        console.print(f"  Failed    : {files_failed}", style="red")
    console.print(f"  Time      : {elapsed}s", style="cyan")
    console.print("GlassBox documentation complete.", style="bold green")


if __name__ == "__main__":
    import argparse
    from rich.console import Console

    parser = argparse.ArgumentParser(description="GlassBox source scanner")
    parser.add_argument("path", nargs="?", help="Project folder path")
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Delete cached results and reprocess all files",
    )
    args    = parser.parse_args()
    console = Console()

    path = args.path or input("Enter project folder path: ").strip()

    if args.clear_cache:
        proj_p   = Path(path).resolve()
        out_base = proj_p.parent / "glassbox" / proj_p.name
        cf       = out_base / CACHE_FILENAME
        if cf.exists():
            cf.unlink()
            console.print("Cache cleared - all files will be reprocessed.", style="yellow")
        else:
            console.print("No cache file found.", style="dim")

    run_glassbox(path, console)