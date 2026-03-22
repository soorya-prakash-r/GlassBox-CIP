"""
normalizer.py
Converts raw parser output into structured dicts consumed by scanner.py.

Each dict contains:
  type        : "function" | "class" | "enum" | "interface" | ...
  name        : component name
  language    : "python" | "java" | ...
  code_block  : clean structured text passed to get_component_summary()
  input       : full architect-style prompt (kept for backward compatibility)
"""

from typing import Dict, List, Optional
import re


# ── Helpers ────────────────────────────────────────────────────────────────────
def _code_block_header(language: str, obj_type: str, name: str, **fields) -> str:
    """Build a compact, structured code context block for the AI API."""
    lines = [
        f"Language  : {language}",
        f"Type      : {obj_type}",
        f"Name      : {name}",
    ]
    for key, value in fields.items():
        if value and str(value).strip().lower() not in ("", "none"):
            lines.append(f"{key:<10}: {value}")
    return "\n".join(lines)


# ── Function / Method ──────────────────────────────────────────────────────────
def normalize_function(
    name: str,
    language: str,
    parameters: str,
    return_type: str,
    body: str,
    decorators: Optional[str] = None,
    access_specifier: Optional[str] = None,
    modifiers: Optional[str] = None,
    parent_class: Optional[str] = None,
) -> Dict:
    decorators       = decorators       or "None"
    access_specifier = access_specifier or "None"
    modifiers        = modifiers        or "None"
    parent_class     = parent_class     or "None"

    code_block = (
        _code_block_header(
            language, "function", name,
            Parameters=parameters,
            **{"Return Type": return_type},
            Modifiers=modifiers,
            **{"Parent Class": parent_class},
            Decorators=decorators,
        )
        + f"\n\nBody:\n{body}"
    )

    return {
        "type":     "function",
        "name":     name,
        "language": language,
        "parameters":  parameters,
        "return_type": return_type,
        # ── Clean block sent to get_component_summary() ──────────────────
        "code_block": code_block,
        # ── Full architect prompt (backward compat) ───────────────────────
        "input": (
            f"You are a Senior Software Architect. Analyze the following {language} code.\n"
            f"Generate documentation in EXACTLY this format:\n\n"
            f"One-liner: <single sentence describing what the code does>\n"
            f"Detailed Summary:\n"
            f"1. inputs: <describe all parameters and their types>\n"
            f"2. logic: <step-by-step explanation of processing and decision-making>\n"
            f"3. output: <describe the return value, type, and what it represents>\n\n"
            f"Do NOT use markdown. Do NOT output code. Do NOT add extra sections.\n\n"
            f"Code:\n"
            f"Language: {language}\n"
            f"Function Name: {name}\n"
            f"Parameters: {parameters}\n"
            f"Return Type: {return_type}\n"
            f"Body:\n{body}"
        ),
    }


# ── Generic Function / Method ──────────────────────────────────────────────────
def normalize_generic_function(
    name: str,
    language: str,
    type_parameters: str,
    parameters: str,
    return_type: str,
    body: str,
    access_specifier: Optional[str] = None,
    modifiers: Optional[str] = None,
    decorators: Optional[str] = None,
) -> Dict:
    access_specifier = access_specifier or "None"
    modifiers        = modifiers        or "None"
    decorators       = decorators       or "None"
    full_modifiers = (
        f"{modifiers}, generic<{type_parameters}>"
        if type_parameters and type_parameters != "None"
        else modifiers
    )

    code_block = (
        _code_block_header(
            language, "generic_function", name,
            **{"Type Parameters": type_parameters},
            Parameters=parameters,
            **{"Return Type": return_type},
            Modifiers=full_modifiers,
            Decorators=decorators,
        )
        + f"\n\nBody:\n{body}"
    )

    return {
        "type":     "generic_function",
        "name":     name,
        "language": language,
        "parameters":      parameters,
        "return_type":     return_type,
        "type_parameters": type_parameters,
        "code_block": code_block,
        "input": (
            f"You are a Senior Software Architect. Analyze the following {language} code.\n"
            f"Generate documentation in EXACTLY this format:\n\n"
            f"One-liner: <single sentence describing what the code does>\n"
            f"Detailed Summary:\n"
            f"1. inputs: <describe all parameters and their types>\n"
            f"2. logic: <step-by-step explanation of processing and decision-making>\n"
            f"3. output: <describe the return value, type, and what it represents>\n\n"
            f"Do NOT use markdown. Do NOT output code. Do NOT add extra sections.\n\n"
            f"Code:\n"
            f"Language: {language}\n"
            f"Function Name: {name}\n"
            f"Type Parameters: {type_parameters}\n"
            f"Modifiers: {full_modifiers}\n"
            f"Parameters: {parameters}\n"
            f"Return Type: {return_type}\n"
            f"Body:\n{body}"
        ),
    }


# ── Class ──────────────────────────────────────────────────────────────────────
def normalize_class(
    name: str,
    language: str,
    attributes: List[str],
    methods: List[str],
    decorators: Optional[str] = None,
    access_specifier: Optional[str] = None,
    modifiers: Optional[str] = None,
    parent_class: Optional[str] = None,
) -> Dict:
    decorators       = decorators       or "None"
    access_specifier = access_specifier or "None"
    modifiers        = modifiers        or "None"
    parent_class     = parent_class     or "None"

    attributes_text = "\n  - ".join(attributes) if attributes else "None"
    methods_text    = "\n  - ".join(methods)    if methods    else "None"

    code_block = (
        _code_block_header(
            language, "class", name,
            Decorators=decorators,
            **{"Access Specifier": access_specifier},
            Modifiers=modifiers,
            **{"Parent Class": parent_class},
        )
        + f"\n\nAttributes:\n  - {attributes_text}"
        + f"\n\nMethods:\n  - {methods_text}"
    )

    return {
        "type":     "class",
        "name":     name,
        "language": language,
        "code_block": code_block,
        "input": (
            f"You are a Senior Software Architect. Analyze the following {language} code.\n"
            f"Generate documentation in EXACTLY this format:\n\n"
            f"One-liner: <single sentence describing what the class does>\n"
            f"Detailed Summary:\n"
            f"1. purpose: <describe the class responsibility>\n"
            f"2. attributes: <describe key data members>\n"
            f"3. methods: <describe key behaviors>\n\n"
            f"Do NOT use markdown. Do NOT output code. Do NOT add extra sections.\n\n"
            f"Code:\n"
            f"Language: {language}\n"
            f"Class Name: {name}\n"
            f"Access Specifier: {access_specifier}\n"
            f"Modifiers: {modifiers}\n"
            f"Parent Class: {parent_class}\n"
            f"Attributes:\n{attributes_text}\n"
            f"Methods:\n{methods_text}"
        ),
    }


# ── Enum ───────────────────────────────────────────────────────────────────────
def normalize_enum(
    name: str,
    language: str,
    members: Dict[str, str],
    base_class: Optional[str] = None,
    underlying_type: Optional[str] = None,
    access_specifier: Optional[str] = None,
    modifiers: Optional[str] = None,
) -> Dict:
    base_class       = base_class       or "None"
    underlying_type  = underlying_type  or "None"
    access_specifier = access_specifier or "None"
    modifiers        = modifiers        or "None"

    members_text = (
        "\n  - ".join(f"{k} = {v}" for k, v in members.items())
        if members else "None"
    )

    code_block = (
        _code_block_header(
            language, "enum", name,
            **{"Base Class": base_class},
            **{"Underlying Type": underlying_type},
            **{"Access Specifier": access_specifier},
            Modifiers=modifiers,
        )
        + f"\n\nMembers:\n  - {members_text}"
    )

    return {
        "type":     "enum",
        "name":     name,
        "language": language,
        "code_block": code_block,
        "input": (
            f"You are a Senior Software Architect. Analyze the following {language} code.\n"
            f"Generate documentation in EXACTLY this format:\n\n"
            f"One-liner: <single sentence describing what the enum represents>\n"
            f"Detailed Summary:\n"
            f"1. purpose: <describe what values this enum defines>\n"
            f"2. members: <describe key enumeration values>\n"
            f"3. usage: <describe typical use cases>\n\n"
            f"Do NOT use markdown. Do NOT output code. Do NOT add extra sections.\n\n"
            f"Code:\n"
            f"Language: {language}\n"
            f"Enum Name: {name}\n"
            f"Base Class: {base_class}\n"
            f"Underlying Type: {underlying_type}\n"
            f"Members:\n{members_text}"
        ),
    }


# ── Interface ──────────────────────────────────────────────────────────────────
def normalize_interface(
    name: str,
    language: str,
    constants: Dict[str, str],
    methods: List[str],
    access_specifier: Optional[str] = None,
    modifiers: Optional[str] = None,
    decorators: Optional[str] = None,
) -> Dict:
    access_specifier = access_specifier or "None"
    modifiers        = modifiers        or "None"
    decorators       = decorators       or "None"

    constants_text = (
        "\n  - ".join(f"{k}: {v}" for k, v in constants.items())
        if constants else "None"
    )
    methods_text = "\n  - ".join(methods) if methods else "None"

    code_block = (
        _code_block_header(
            language, "interface", name,
            **{"Access Specifier": access_specifier},
            Modifiers=modifiers,
            Decorators=decorators,
        )
        + f"\n\nConstants:\n  - {constants_text}"
        + f"\n\nMethods:\n  - {methods_text}"
    )

    return {
        "type":     "interface",
        "name":     name,
        "language": language,
        "code_block": code_block,
        "input": (
            f"You are a Senior Software Architect. Analyze the following {language} code.\n"
            f"Generate documentation in EXACTLY this format:\n\n"
            f"One-liner: <single sentence describing what the interface defines>\n"
            f"Detailed Summary:\n"
            f"1. purpose: <describe the contract this interface defines>\n"
            f"2. constants: <describe key constant values>\n"
            f"3. methods: <describe required method signatures>\n\n"
            f"Do NOT use markdown. Do NOT output code. Do NOT add extra sections.\n\n"
            f"Code:\n"
            f"Language: {language}\n"
            f"Interface Name: {name}\n"
            f"Access Specifier: {access_specifier}\n"
            f"Modifiers: {modifiers}\n"
            f"Constants:\n{constants_text}\n"
            f"Methods:\n{methods_text}"
        ),
    }


# ── Error Block ────────────────────────────────────────────────────────────────
def normalize_error_block(
    language: str,
    content: str,
    block_type: Optional[str] = None,
) -> Dict:
    block_type = block_type or "generic"

    exception_type = "Exception"
    if "catch" in content:
        match = re.search(r"catch\s*\(\s*(\w+)", content)
        if match:
            exception_type = match.group(1)

    code_block = (
        _code_block_header(
            language, "error_block", f"{block_type}_error_block",
            **{"Handler Type": block_type},
            **{"Exception Type": exception_type},
        )
        + f"\n\nBody:\n{content}"
    )

    return {
        "type":     "error_block",
        "name":     f"{block_type}_error_block",
        "language": language,
        "code_block": code_block,
        "input": (
            f"You are a Senior Software Architect. Analyze the following {language} code.\n"
            f"Generate documentation in EXACTLY this format:\n\n"
            f"One-liner: <single sentence describing error handling>\n"
            f"Detailed Summary:\n"
            f"1. exception: <describe what exception is caught>\n"
            f"2. handling: <describe how the error is handled>\n"
            f"3. recovery: <describe recovery or logging actions>\n\n"
            f"Do NOT use markdown. Do NOT output code. Do NOT add extra sections.\n\n"
            f"Code:\n"
            f"Language: {language}\n"
            f"Handler Type: {block_type}\n"
            f"Exception Type: {exception_type}\n"
            f"Body:\n{content}"
        ),
    }