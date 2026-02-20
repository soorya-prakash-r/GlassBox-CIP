import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())
parser = Parser(PY_LANGUAGE)

def parse_function(node, code_bytes):
    name_node = node.child_by_field_name("name")
    params_node = node.child_by_field_name("parameters")
    body_node = node.child_by_field_name("body")
    return_node = node.child_by_field_name("return_type")
    
    name = code_bytes[name_node.start_byte:name_node.end_byte].decode('utf8') if name_node else "unknown"
    
    params_list = []
    if params_node:
        for p in params_node.children:
            if p.type in ["identifier", "typed_parameter", "default_parameter", "attribute"]:
                if p.type == "typed_parameter":
                    id_node = p.children[0] 
                elif p.type == "default_parameter":
                    id_node = p.child_by_field_name("name") or p.children[0]
                else:
                    id_node = p
                params_list.append(code_bytes[id_node.start_byte:id_node.end_byte].decode('utf8'))
    
    return_type = "None"
    if return_node:
        raw_return = code_bytes[return_node.start_byte:return_node.end_byte].decode('utf8')
        return_type = raw_return.strip()

    # ✅ Body as list of statements
    body_statements = []
    if body_node:
        for stmt in body_node.children:
            if stmt.type not in ["comment", "\n", "newline"]:
                text = code_bytes[stmt.start_byte:stmt.end_byte].decode('utf8').strip()
                if text:
                    body_statements.append(text)
        
    return {
        "name": name,
        "params": ",".join(params_list),
        "body": body_statements,       # now a list
        "return_type": return_type
    }

def extract_all(source_code):
    code_bytes = source_code.encode('utf8')
    tree = parser.parse(code_bytes)
    root = tree.root_node

    classes = []
    standalone_functions = []
    others = []

    for child in root.children:
        if child.type == "class_definition":
            class_name_node = child.child_by_field_name("name")
            class_name = code_bytes[class_name_node.start_byte:class_name_node.end_byte].decode('utf8')
            
            methods = []
            body_node = child.child_by_field_name("body")
            if body_node:
                for sub_child in body_node.children:
                    if sub_child.type == "function_definition":
                        methods.append(parse_function(sub_child, code_bytes))
            
            classes.append({"class_name": class_name, "methods": methods})
        elif child.type == "function_definition":
            standalone_functions.append(parse_function(child, code_bytes))
        elif child.type not in ["comment", "module"]:
            text = code_bytes[child.start_byte:child.end_byte].decode('utf8').strip()
            if text: others.append(text)

    return classes, standalone_functions, others

# --- Corrected Execution ---
code_input = """
class Calculator:
    def add(self, x: int, y: int):
        return x + y
    
    def clear(self) -> None:
        print("Cleared")

def greet(name: str) -> str:
    return "hello " + name

greet("soorya")
"""

# Call the correct function
classes, functions, others = extract_all(code_input)

# Display Output
for cls in classes:
    print(f"CLASS: {cls['class_name']}")
    for m in cls['methods']:
        print(f"  Function: {m['name']}\n  1. Params: {m['params']}\n  2. Return Type: {m['return_type']}\n  3. Body: {m['body']}")
    print("-" * 20)

for f in functions:
    print(f"FUNCTION: {f['name']}\n1. Params: {f['params']}\n2. Return Type: {f['return_type']}\n3. Body: {f['body']}")
    print("-" * 20)

print(f"4. Other parts of code: {' | '.join(others)}")