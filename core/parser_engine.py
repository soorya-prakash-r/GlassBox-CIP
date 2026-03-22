"""
parser_engine.py
Parses Python and Java source files using tree-sitter and returns
normalized component objects.

Fixes applied:
  - Robust name extraction with regex fallback (fixes truncated names like
    'ventory' instead of 'Inventory')
  - typed_default_parameter handling (params with default values)
  - *args / **kwargs handling
  - list_splat_pattern / dictionary_splat_pattern handling
"""

import re
from pathlib import Path
from tree_sitter import Parser
from tree_sitter_languages import get_language

from core.normalizer import (
    normalize_function,
    normalize_class,
    normalize_enum,
    normalize_interface,
    normalize_error_block,
    normalize_generic_function,
)

PY_LANGUAGE   = get_language("python")
JAVA_LANGUAGE = get_language("java")


# ── Parser factory ─────────────────────────────────────────────────────────────
def get_parser(language: str) -> Parser:
    parser   = Parser()
    lang_map = {"python": PY_LANGUAGE, "java": JAVA_LANGUAGE}
    if language not in lang_map:
        raise ValueError(f"Unsupported language: {language}")
    parser.set_language(lang_map[language])
    return parser


# ── Main entry point ───────────────────────────────────────────────────────────
def parse_file(file_path, source_code: str) -> list:
    file_path  = Path(file_path)
    suffix_map = {".py": "python", ".java": "java"}
    language   = suffix_map.get(file_path.suffix)
    if not language:
        return []

    parser = get_parser(language)
    tree   = parser.parse(bytes(source_code, "utf8"))

    if language == "python":
        return extract_python(tree, source_code)
    if language == "java":
        return extract_java(tree, source_code)
    return []


# ── Low-level helpers ──────────────────────────────────────────────────────────
def text(node, source: str) -> str:
    """Extract source text for a node. Returns '' if node is None."""
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte]


def _safe_name(node, source: str, fallback_pattern: str = "") -> str:
    """
    Computes line number from byte offset using str.count('\n') —
    works on all tree-sitter versions without relying on start_point.
    """
    if fallback_pattern:
        # Compute line number reliably from byte offset
        start_row = source.count('\n', 0, node.start_byte)
        lines     = source.splitlines()
        end_row   = min(len(lines), start_row + 3)

        for row in range(start_row, end_row):
            m = re.search(fallback_pattern, lines[row])
            if m:
                name = m.group(1).strip()
                if name and re.match(r'^\w+$', name):
                    return name

    # Fallback — tree-sitter name field
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        raw = source[name_node.start_byte:name_node.end_byte].rstrip(": \t\n").strip()
        if raw and re.match(r'^\w+$', raw):
            return raw

    return ""


def get_decorators(node, source: str) -> str:
    decorators = []
    parent     = node.parent
    if not parent:
        return "None"
    siblings = list(parent.children)
    try:
        idx = siblings.index(node)
    except ValueError:
        return "None"
    for sib in reversed(siblings[:idx]):
        if sib.type == "decorator":
            decorators.insert(0, text(sib, source).strip())
        else:
            break
    return ", ".join(decorators) if decorators else "None"


def get_modifiers(node, source: str) -> str:
    mods = []
    for child in node.children:
        if child.type == "modifiers":
            mods.append(text(child, source).strip())
        elif not child.is_named and child.type in (
            "public", "private", "protected", "static",
            "abstract", "final", "synchronized", "native",
        ):
            mods.append(child.type)
    return ", ".join(mods) if mods else "None"


def get_access_specifier(node, source: str) -> str:
    for child in node.children:
        if child.type == "modifiers":
            mod_text = text(child, source)
            for kw in ("public", "private", "protected"):
                if kw in mod_text:
                    return kw
        elif child.type in ("public", "private", "protected"):
            return child.type
    return "None"


# ══════════════════════════════════════════════════════════════════════════════
#  Python extraction
# ══════════════════════════════════════════════════════════════════════════════
def extract_python(tree, source: str) -> list:
    root    = tree.root_node
    outputs = []
    visited: set = set()

    def traverse(node, parent_class=None, inside_class=False):
        node_id = (node.start_byte, node.end_byte, node.type)

        if node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            deco_text = ", ".join(
                text(c, source).strip()
                for c in node.children
                if c.type == "decorator"
            )
            if inner:
                if inner.type == "class_definition":
                    inner_id = (inner.start_byte, inner.end_byte, inner.type)
                    visited.add(inner_id)
                    _handle_python_class(
                        inner, source, outputs, visited,
                        parent_class, inside_class,
                        override_decorators=deco_text,
                        traverse_fn=traverse,
                    )
                else:
                    _handle_python_node(
                        inner, source, outputs, visited,
                        parent_class, inside_class,
                        override_decorators=deco_text,
                    )
                    body = inner.child_by_field_name("body")
                    if body:
                        func_name = _safe_name(inner, source, r"def\s+(\w+)")
                        for child in body.children:
                            traverse(child, func_name, inside_class)
            return

        if node.type == "class_definition" and node_id not in visited:
            visited.add(node_id)
            _handle_python_class(
                node, source, outputs, visited,
                parent_class, inside_class,
                override_decorators=None,
                traverse_fn=traverse,
            )
            return

        if node.type in ("function_definition", "async_function_definition") \
                and node_id not in visited:
            _handle_python_node(
                node, source, outputs, visited,
                parent_class, inside_class,
            )
            func_name = _safe_name(node, source, r"(?:async\s+)?def\s+(\w+)")
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    traverse(child, func_name, inside_class)
            return

        if node.type == "try_statement" and node_id not in visited:
            visited.add(node_id)
            outputs.append(
                normalize_error_block(
                    language="python",
                    content=text(node, source).strip(),
                    block_type="try_except",
                )
            )
            for child in node.children:
                traverse(child, parent_class, inside_class)
            return

        for child in node.children:
            traverse(child, parent_class, inside_class)

    traverse(root)
    return outputs


def _handle_python_class(
    node, source, outputs, visited,
    parent_class, inside_class,
    override_decorators=None,
    traverse_fn=None,
):
    name = _safe_name(node, source, r"class\s+(\w+)")
    if not name:
        return

    decorators_text = (
        override_decorators
        if override_decorators is not None
        else get_decorators(node, source)
    )
    bases = node.child_by_field_name("superclasses")

    _ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
    is_enum = bases and any(b in text(bases, source) for b in _ENUM_BASES)

    if is_enum:
        members: dict = {}
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "assignment":
                    left  = text(child.child_by_field_name("left"),  source)
                    right = text(child.child_by_field_name("right"), source)
                    members[left] = right
        outputs.append(
            normalize_enum(
                name=name,
                language="python",
                members=members,
                base_class=text(bases, source).strip("()") if bases else "Enum",
                underlying_type="None",
                access_specifier="None",
                modifiers="None",
            )
        )
    else:
        attributes: list = []
        methods:    list = []
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type in ("assignment", "annotated_assignment", "expression_statement"):
                    child_text = text(child, source).strip()
                    # Only collect class-level (non-method) assignments
                    if "def " not in child_text:
                        attributes.append(child_text)
                elif child.type in ("function_definition", "async_function_definition"):
                    mname  = _safe_name(child, source, r"(?:async\s+)?def\s+(\w+)")
                    params = text(child.child_by_field_name("parameters"), source)
                    methods.append(f"{mname}{params}")
                elif child.type == "decorated_definition":
                    inner = child.child_by_field_name("definition")
                    if inner and inner.type in ("function_definition", "async_function_definition"):
                        mname  = _safe_name(inner, source, r"(?:async\s+)?def\s+(\w+)")
                        params = text(inner.child_by_field_name("parameters"), source)
                        methods.append(f"{mname}{params}")

        outputs.append(
            normalize_class(
                name=name,
                language="python",
                attributes=attributes,
                methods=methods,
                decorators=decorators_text,
                access_specifier="None",
                modifiers="None",
                parent_class=parent_class if inside_class else None,
            )
        )

    if traverse_fn:
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                traverse_fn(child, parent_class=name, inside_class=True)


def _handle_python_node(node, source, outputs, visited, parent_class, inside_class, override_decorators=None):
    node_id = (node.start_byte, node.end_byte, node.type)
    if node_id in visited:
        return
    visited.add(node_id)

    name = _safe_name(node, source, r"(?:async\s+)?def\s+(\w+)")

    if not name:
        return

    params_node  = node.child_by_field_name("parameters")
    params       = _extract_python_parameters(params_node, source) if params_node else "None"
    return_type = "None"
    start_row   = source.count('\n', 0, node.start_byte)
    lines       = source.splitlines()
    # Search across the def line and next few lines for "-> Type:"
    for row in range(start_row, min(len(lines), start_row + 5)):
        m = re.search(r'->\s*([\w\[\], |]+?)\s*:', lines[row])
        if m:
            return_type = m.group(1).strip()
            break
    body         = text(node.child_by_field_name("body"), source)
    decorators   = override_decorators if override_decorators is not None else get_decorators(node, source)
    is_async     = (
        node.type == "async_function_definition"
        or any(c.type == "async" for c in node.children)
    )

    outputs.append(
        normalize_function(
            name=name,
            language="python",
            parameters=params,
            return_type=return_type,
            body=body.strip(),
            decorators=decorators,
            access_specifier="None",
            modifiers="async" if is_async else "None",
            parent_class=parent_class,
        )
    )

def _extract_python_parameters(params_node, source: str) -> str:
    if not params_node:
        return "None"

    # ── Use source line regex instead of byte range ────────────────────────
    # Find the line containing the function definition
    start_row = source.count('\n', 0, params_node.start_byte)
    lines     = source.splitlines()

    # Find the full parameter string from the def line(s)
    # Collect lines until we find the closing )
    raw = ""
    depth = 0
    collecting = False
    for row in range(start_row, min(len(lines), start_row + 10)):
        for ch in lines[row]:
            if ch == '(':
                collecting = True
                depth += 1
            elif ch == ')':
                depth -= 1
                if collecting and depth == 0:
                    break
                if collecting:
                    raw += ch
            elif collecting:
                raw += ch
        if collecting and depth == 0:
            break
        if collecting:
            raw += " "

    raw = raw.strip()

    # ── Strip return annotation if leaked in ───────────────────────────────
    if " ->" in raw:
        raw = raw.split(" ->")[0].strip()
    if "-)" in raw:
        raw = raw.split("-)")[0].strip()

    _SKIP = {"self", "cls"}
    params: list[str] = []

    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        # Strip default value:  "x: int = 5" → "x: int"
        if "=" in part:
            part = part.split("=")[0].strip()

        # Strip trailing junk
        part = re.sub(r'\s*-[\)>].*$', '', part).strip().rstrip(")")

        # Extract name before colon
        name = part.split(":")[0].strip().lstrip("*").strip()

        if not name or name in _SKIP:
            continue

        if ":" in part:
            type_hint = part.split(":", 1)[1].strip()
            type_hint = re.sub(r'\s*-[\)>].*$', '', type_hint).strip()
            params.append(f"{name}: {type_hint}" if type_hint else name)
        else:
            params.append(name)

    return ", ".join(params) if params else "None"

# ══════════════════════════════════════════════════════════════════════════════
#  Java extraction
# ══════════════════════════════════════════════════════════════════════════════
def extract_java(tree, source: str) -> list:
    root    = tree.root_node
    outputs = []
    visited: set = set()

    def traverse(node, parent_class=None):
        node_id = (node.start_byte, node.end_byte, node.type)

        if node.type == "class_declaration" and node_id not in visited:
            visited.add(node_id)
            name   = _safe_name(node, source, r"class\s+(\w+)")
            access = get_access_specifier(node, source)
            mods   = get_modifiers(node, source)
            attributes: list = []
            methods:    list = []
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "field_declaration":
                        attributes.append(text(child, source).strip())
                    elif child.type == "method_declaration":
                        mname  = _safe_name(child, source, r"\s(\w+)\s*\(")
                        params = text(child.child_by_field_name("parameters"), source)
                        methods.append(f"{mname}{params}")
            outputs.append(
                normalize_class(
                    name=name,
                    language="java",
                    attributes=attributes,
                    methods=methods,
                    decorators=_java_annotations(node, source),
                    access_specifier=access,
                    modifiers=mods,
                    parent_class=parent_class,
                )
            )
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    traverse(child, parent_class=name)
            return

        if node.type == "interface_declaration" and node_id not in visited:
            visited.add(node_id)
            name      = _safe_name(node, source, r"interface\s+(\w+)")
            constants: dict = {}
            methods:   list = []
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "constant_declaration":
                        constants[text(child, source)] = ""
                    elif child.type == "method_declaration":
                        mname  = _safe_name(child, source, r"\s(\w+)\s*\(")
                        params = text(child.child_by_field_name("parameters"), source)
                        methods.append(f"{mname}{params}")
            outputs.append(
                normalize_interface(
                    name=name,
                    language="java",
                    constants=constants,
                    methods=methods,
                    access_specifier=get_access_specifier(node, source),
                    modifiers=get_modifiers(node, source),
                    decorators=_java_annotations(node, source),
                )
            )
            for child in node.children:
                traverse(child, parent_class)
            return

        if node.type == "enum_declaration" and node_id not in visited:
            visited.add(node_id)
            name    = _safe_name(node, source, r"enum\s+(\w+)")
            members: dict = {}
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "enum_constant":
                        members[text(child, source)] = ""
            outputs.append(
                normalize_enum(
                    name=name,
                    language="java",
                    members=members,
                    base_class="None",
                    underlying_type="None",
                    access_specifier=get_access_specifier(node, source),
                    modifiers="None",
                )
            )
            for child in node.children:
                traverse(child, parent_class)
            return

        if node.type == "method_declaration" and node_id not in visited:
            parent = node.parent
            if parent and parent.type == "interface_body":
                visited.add(node_id)
                return
            visited.add(node_id)
            name        = _safe_name(node, source, r"\s(\w+)\s*\(")
            params_node = node.child_by_field_name("parameters")
            params      = _extract_java_parameters(params_node, source) if params_node else "None"
            return_type = text(node.child_by_field_name("type"), source) or "void"
            type_params_node = node.child_by_field_name("type_parameters")
            type_params = (
                text(type_params_node, source).strip(" <>")
                if type_params_node else "None"
            )
            body        = text(node.child_by_field_name("body"), source)
            access      = get_access_specifier(node, source)
            mods        = get_modifiers(node, source)
            annotations = _java_annotations(node, source)

            if type_params != "None":
                outputs.append(
                    normalize_generic_function(
                        name=name,
                        language="java",
                        type_parameters=type_params,
                        parameters=params,
                        return_type=return_type,
                        body=body.strip(),
                        access_specifier=access,
                        modifiers=mods,
                        decorators=annotations,
                    )
                )
            else:
                outputs.append(
                    normalize_function(
                        name=name,
                        language="java",
                        parameters=params,
                        return_type=return_type,
                        body=body.strip(),
                        decorators=annotations,
                        access_specifier=access,
                        modifiers=mods,
                        parent_class=parent_class,
                    )
                )
            body_node = node.child_by_field_name("body")
            if body_node:
                for child in body_node.children:
                    traverse(child, parent_class)
            return

        if node.type == "constructor_declaration" and node_id not in visited:
            visited.add(node_id)
            name   = _safe_name(node, source, r"(\w+)\s*\(")
            params = text(node.child_by_field_name("parameters"), source).strip("()")
            body   = text(node.child_by_field_name("body"), source)
            outputs.append(
                normalize_function(
                    name=name,
                    language="java",
                    parameters=params or "None",
                    return_type="constructor",
                    body=body.strip(),
                    decorators=_java_annotations(node, source),
                    access_specifier=get_access_specifier(node, source),
                    modifiers="constructor",
                    parent_class=parent_class,
                )
            )
            return

        if node.type in ("try_statement", "try_with_resources_statement") \
                and node_id not in visited:
            visited.add(node_id)
            outputs.append(
                normalize_error_block(
                    language="java",
                    content=text(node, source).strip(),
                    block_type="try_catch",
                )
            )
            for child in node.children:
                traverse(child, parent_class)
            return

        for child in node.children:
            traverse(child, parent_class)

    traverse(root)
    return outputs


def _java_annotations(node, source: str) -> str:
    annotations = []
    for child in node.children:
        if child.type == "modifiers":
            for sub in child.children:
                if sub.type in ("annotation", "marker_annotation"):
                    annotations.append(text(sub, source).strip())
    return ", ".join(annotations) if annotations else "None"


def _extract_java_parameters(params_node, source: str) -> str:
    if not params_node:
        return "None"
    params: list[str] = []
    for child in params_node.children:
        if child.type == "formal_parameter":
            param_type = ""
            param_name = ""
            for sub in child.children:
                if sub.type in (
                    "type_identifier", "generic_type",
                    "scoped_type_identifier", "array_type",
                ):
                    param_type = text(sub, source)
                elif sub.type == "identifier":
                    param_name = text(sub, source)
            if param_type and param_name:
                params.append(f"{param_type} {param_name}")
            elif param_name:
                params.append(param_name)
    return ", ".join(params) if params else "None"