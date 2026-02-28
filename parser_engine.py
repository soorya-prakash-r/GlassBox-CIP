# parser_engine.py

from tree_sitter import Parser
from tree_sitter_languages import get_language
from normalizer import normalize_function, normalize_class, normalize_error_block

# Load languages
PY_LANGUAGE = get_language("python")
JAVA_LANGUAGE = get_language("java")


def get_parser(language_name):
    parser = Parser()
    if language_name == "python":
        parser.set_language(PY_LANGUAGE)
    elif language_name == "java":
        parser.set_language(JAVA_LANGUAGE)
    return parser


def parse_file(file_path: str, source_code: str):
    """
    Returns a list of normalized dicts from file content.
    """
    language = None
    if file_path.endswith(".py"):
        language = "python"
    elif file_path.endswith(".java"):
        language = "java"
    else:
        return []

    parser = get_parser(language)
    tree = parser.parse(bytes(source_code, "utf8"))

    if language == "python":
        return extract_python(tree, source_code)
    else:
        return extract_java(tree, source_code)


# ---------------- Python ----------------
def extract_python(tree, source):
    root = tree.root_node
    outputs = []

    def get_text(node):
        return source[node.start_byte:node.end_byte]

    def traverse(node, parent_class=None):
        # Function
        if node.type == "function_definition":
            if parent_class is None and node.parent.type == "class_definition":
                parent_class = get_text(node.parent.child_by_field_name("name"))

            name = get_text(node.child_by_field_name("name"))
            params = get_text(node.child_by_field_name("parameters")).strip("()")
            return_type = get_text(node.child_by_field_name("return_type")) if node.child_by_field_name("return_type") else "None"
            body = get_text(node.child_by_field_name("body")).strip()

            outputs.append(normalize_function(name, "python", params, return_type, body, parent_class))

        # Error Handling
        if node.type == "try_statement":
            content = get_text(node)
            outputs.append(normalize_error_block("python", content))

        # Traverse children
        for child in node.children:
            traverse(child, parent_class)

    traverse(root)
    return outputs


# ---------------- Java ----------------
def extract_java(tree, source):
    root = tree.root_node
    outputs = []

    def get_text(node):
        return source[node.start_byte:node.end_byte]

    def traverse(node, parent_class=None):
        # Method
        if node.type == "method_declaration":
            name = get_text(node.child_by_field_name("name"))
            params = get_text(node.child_by_field_name("parameters")).strip("()")
            return_type = get_text(node.child_by_field_name("type"))
            body_node = node.child_by_field_name("body")
            body = get_text(body_node).strip() if body_node else "None"

            outputs.append(normalize_function(name, "java", params, return_type, body, parent_class))

        # Traverse children
        for child in node.children:
            traverse(child, parent_class)

    traverse(root)
    return outputs