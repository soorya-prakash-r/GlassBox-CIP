import json
import os
import argparse
from collections import defaultdict
from tree_sitter import Parser, Language
from tree_sitter_c import language as c_language

# =====================================================
# 1️⃣ STRUCTURE ENGINE
# =====================================================
class StructureEngine:
    def __init__(self, structure_path):
        self.structure_path = structure_path
        self.structures = self.load_structures()

    def load_structures(self):
        with open(self.structure_path, "r", encoding="utf-8") as f:
            content = f.read()

        blocks = []
        buffer = ""
        brace_count = 0
        for char in content:
            if char == "{":
                brace_count += 1
            if brace_count > 0:
                buffer += char
            if char == "}":
                brace_count -= 1
                if brace_count == 0:
                    try:
                        obj = json.loads(buffer)
                        blocks.append(obj)
                    except json.JSONDecodeError:
                        pass
                    buffer = ""

        structure_map = defaultdict(lambda: defaultdict(dict))
        for block in blocks:
            if "language" not in block:
                continue
            language = block["language"].lower()
            entity_type = self.detect_entity_type(block)
            summary_type = self.detect_summary_type(block)
            structure_map[language][entity_type][summary_type] = block
        return structure_map

    def detect_entity_type(self, block):
        if "input" in block and isinstance(block["input"], dict):
            inner = block["input"]
            if "function_name" in inner:
                return "function"
        if "function_name" in block:
            return "function"
        if "class_name" in block:
            return "class"
        if "struct_name" in block:
            return "struct"
        if block.get("entity_type") == "enum":
            return "enum"
        if "interface_name" in block:
            return "interface"
        if "macro_name" in block:
            return "macro"
        if block.get("entity_type") == "generic_function":
            return "generic_function"
        if block.get("entity_type") == "error_handling":
            return "error_handling"
        return "unknown"

    def detect_summary_type(self, block):
        instruction = block.get("instruction", "").lower()
        if "one-line" in instruction or "one line" in instruction:
            return "one_liner"
        return "summary"

    def get_template(self, language, entity_type, summary_type="summary"):
        return self.structures.get(language, {}).get(entity_type, {}).get(summary_type)

# =====================================================
# 2️⃣ LANGUAGE DETECTOR
# =====================================================
def detect_language(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    mapping = {
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".hpp": "cpp",
        ".java": "java",
        ".py": "python"
    }
    return mapping.get(ext)

# =====================================================
# 3️⃣ TREE-SITTER C PARSER
# =====================================================
def parse_c_file(code):
    parser = Parser()
    C_LANGUAGE = Language(c_language())
    parser.language = C_LANGUAGE
    tree = parser.parse(bytes(code, "utf8"))
    root_node = tree.root_node
    functions = []

    def extract_text(node):
        return code[node.start_byte:node.end_byte]

    # 🔹 Recursive function name search
    def find_function_name(node):
        if node.type == "identifier":
            return extract_text(node).strip()
        for child in node.children:
            name = find_function_name(child)
            if name:
                return name
        return ""

    # 🔹 Parameter extraction (type + name)
    def extract_parameters(param_list_node):
        if not param_list_node:
            return ""
        params = []
        for param in param_list_node.named_children:
            if param.type != "parameter_declaration":
                continue
            param_type_parts = []
            param_name = None
            for child in param.children:
                text = extract_text(child).strip()
                if child.type == "identifier":
                    param_name = text
                elif text:
                    param_type_parts.append(text)
            if param_name:
                params.append(f"{' '.join(param_type_parts)}: {param_name}")
            elif param_type_parts:
                params.append(" ".join(param_type_parts))
        return ", ".join(params) if params else ""

    # 🔹 Modifiers extraction
    def extract_modifiers(node):
        modifiers = []
        parent = node.parent
        if parent:
            mod_types = ["storage_class_specifier", "type_qualifier", "inline_specifier"]
            for sibling in parent.named_children:
                if sibling.type in mod_types:
                    text = extract_text(sibling).strip()
                    if text.lower() in ["static", "inline", "const", "constexpr"]:
                        modifiers.append(text)
        return " ".join(modifiers) if modifiers else ""

    # 🔹 Return type extraction
    def extract_return_type(type_node):
        if not type_node:
            return "void"
        type_parts = []

        def collect_types(node):
            if node.type in ["type_identifier", "primitive_type", "pointer_type"]:
                type_parts.append(extract_text(node).strip())
            for child in node.children:
                collect_types(child)

        collect_types(type_node)
        return " ".join(type_parts) if type_parts else "void"

    # 🔹 Walk AST recursively
    def walk(node):
        if node.type == "function_definition":
            type_node = node.child_by_field_name("type")
            declarator = node.child_by_field_name("declarator")
            body_node = node.child_by_field_name("body")
            if declarator and body_node:
                func_name = find_function_name(declarator)
                param_node = declarator.child_by_field_name("parameters")
                params = extract_parameters(param_node)
                modifiers = extract_modifiers(node)
                ret_type = extract_return_type(type_node)
                body_text = extract_text(body_node)
                body_lines = [line.strip() for line in body_text.strip("{}").splitlines() if line.strip()]
                functions.append({
                    "entity_type": "function",
                    "function_name": func_name,
                    "params": params,
                    "return_type": ret_type,
                    "body": body_lines,
                    "language": "c",
                    "modifiers": modifiers
                })
        for child in node.children:
            walk(child)

    walk(root_node)
    return functions

# =====================================================
# 4️⃣ RAW METADATA
# =====================================================
def print_raw_metadata(file_path):
    language = detect_language(file_path)
    if not language:
        print(json.dumps([{"error": "Unsupported language"}], indent=2))
        return
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()
    except Exception as e:
        print(json.dumps([{"error": str(e)}], indent=2))
        return
    entities = parse_c_file(code) if language == "c" else []
    print(json.dumps(entities, indent=2))

# =====================================================
# 5️⃣ STRUCTURE.TXT FORMATTED OUTPUT
# =====================================================
def analyze_file(file_path, structure_path, summary_type="summary"):
    language = detect_language(file_path)
    if not language or language != "c":
        return "Only C language supported"

    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()

    extracted_entities = parse_c_file(code)
    if not extracted_entities:
        return "No functions found"

    structure_engine = StructureEngine(structure_path)
    results = []

    for extracted in extracted_entities:
        template = structure_engine.get_template("c", "function", summary_type)
        if template:
            formatted_data = {
                "function_name": extracted["function_name"],
                "language": "c",
                "modifiers": extracted["modifiers"],
                "params": extracted["params"],
                "return_type": extracted["return_type"],
                "body": extracted["body"]
            }
            instruction = template.get("instruction", "")
            example_output = template.get("output", "")
            formatted = f"""Instruction:
{instruction}

Extracted Metadata:
{json.dumps(formatted_data, indent=2)}

Expected Output Format Example:
{example_output}"""
            results.append(formatted.strip())

    return "\n\n".join(results)

# =====================================================
# 6️⃣ CLI
# =====================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ultimate C Code Analyzer (Tree-sitter)")
    parser.add_argument("file_path", nargs="?", help="Path to C source file")
    parser.add_argument("--structure", default=r"D:\College Works\sem6\CIP\format-testing\GlassBox-CIP\structure.txt", help="Path to structure.txt")
    parser.add_argument("--raw", "-r", action="store_true", help="Raw JSON metadata")
    parser.add_argument("--full", "-f", action="store_true", help="Full structure.txt formatted output")
    parser.add_argument("--summary-type", choices=["summary", "one_liner"], default="summary", help="Template type")

    args = parser.parse_args()
    if not args.file_path:
        args.file_path = r"D:\College Works\sem6\CIP\src\test_c\code\test3.c"

    if args.full:
        result = analyze_file(args.file_path, args.structure, args.summary_type)
        print(result)
    else:
        print_raw_metadata(args.file_path)