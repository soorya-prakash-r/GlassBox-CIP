# normalizer.py

from typing import Dict, List

# ---------------- Functions ----------------
def normalize_function(
    name: str, 
    language: str, 
    parameters: str, 
    return_type: str, 
    body: str, 
    parent_class: str = None
) -> Dict:
    return {
        "type": "function",
        "name": name,
        "parameters": parameters,
        "return_type": return_type,
        "parent_class": parent_class,
        "input": (
            f"summarize function:\n"
            f"Language: {language}\n"
            f"Function Name: {name}\n"
            f"Decorators: None\n"
            f"Parameters: {parameters}\n"
            f"Return Type: {return_type}\n"
            f"Body:\n{body}"
        )
    }

# ---------------- Classes ----------------
def normalize_class(
    name: str, 
    language: str, 
    parent_class: str = None
) -> Dict:
    return {
        "type": "class",
        "name": name,
        "parameters": "None",
        "parent_class": parent_class,
        "input": (
            f"summarize class:\n"
            f"Language: {language}\n"
            f"Class Name: {name}\n"
            f"Class Decorators: None\n\n"
            "Attributes:\nNone\n\n"
            "Methods:\nNone"
        )
    }

# ---------------- Error Handling ----------------
def normalize_error_block(
    language: str, 
    content: str, 
    name: str = "error_block"
) -> Dict:
    return {
        "type": "error_block",
        "name": name,
        "parameters": "None",
        "input": (
            f"summarize error handling block:\n"
            f"Language: {language}\n"
            f"Content:\n{content}"
        )
    }

# ---------------- Interfaces ----------------
def normalize_interface(
    name: str, 
    language: str, 
    access_specifier: str = "public", 
    constants: dict = None, 
    methods: List[str] = None
) -> Dict:
    constants = constants or {}
    methods = methods or []
    return {
        "type": "interface",
        "name": name,
        "parameters": "None",
        "input": (
            f"summarize interface:\nLanguage: {language}\nInterface Name: {name}\nAccess Specifier: {access_specifier}\n\n"
            f"Constants:\n" + "\n".join(f"{k}: {v}" for k, v in constants.items()) + "\n\n"
            f"Methods:\n" + "\n\n".join(methods)
        )
    }

# ---------------- Enums ----------------
def normalize_enum(
    name: str, 
    language: str, 
    base_class: str = None, 
    members: dict = None
) -> Dict:
    members = members or {}
    return {
        "type": "enum",
        "name": name,
        "parameters": "None",
        "input": (
            f"summarize enum:\nLanguage: {language}\nEnum Name: {name}\nBase Class: {base_class or 'None'}\n\n"
            "Members:\n" + "\n".join(f"{k} = {v}" for k, v in members.items())
        )
    }

# ---------------- Macros ----------------
def normalize_macro(
    name: str, 
    language: str, 
    definition: str
) -> Dict:
    return {
        "type": "macro",
        "name": name,
        "parameters": "None",
        "input": (
            f"summarize macro:\nLanguage: {language}\nMacro Name: {name}\nDefinition:\n{definition}"
        )
    }

# ---------------- Generic Functions (Java) ----------------
def normalize_generic_function(
    name: str,
    language: str,
    type_params: str,
    access_specifier: str,
    modifiers: str,
    parameters: str,
    return_type: str,
    body: str
) -> Dict:
    return {
        "type": "generic_function",
        "name": name,
        "parameters": parameters,
        "input": (
            f"summarize generic function:\nLanguage: {language}\nFunction Name: {name}\n"
            f"Type Parameters: {type_params}\nAccess Specifier: {access_specifier}\nModifiers: {modifiers}\n"
            f"Parameters:\n{parameters}\nReturn Type:\n{return_type}\nBody:\n{body}"
        )
    }