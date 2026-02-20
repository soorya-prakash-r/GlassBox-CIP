import tree_sitter_c as tsc
from tree_sitter import Language, Parser

# Initialize C Parser
C_LANGUAGE = Language(tsc.language())
parser = Parser(C_LANGUAGE)

def extract_c_summary(source_code):
    code_bytes = source_code.encode('utf8')
    tree = parser.parse(code_bytes)
    root = tree.root_node

    functions = []
    structs = []
    others = []

    for child in root.children:
        # 1. Handle Functions
        if child.type == "function_definition":
            # C function names are deeper: function_definition -> function_declarator -> identifier
            decl = child.child_by_field_name("declarator")
            
            # Extract Name
            name_node = decl.child_by_field_name("declarator") if decl else None
            name = code_bytes[name_node.start_byte:name_node.end_byte].decode('utf8') if name_node else "unknown"

            # Extract Parameters
            params_node = decl.child_by_field_name("parameters") if decl else None
            params = []
            if params_node:
                # In C, we look for parameter_declaration nodes
                for p in params_node.children:
                    if p.type == "parameter_declaration":
                        # Get the identifier inside the declaration (e.g., 'x' in 'int x')
                        p_name = p.child_by_field_name("declarator")
                        if p_name:
                            params.append(code_bytes[p_name.start_byte:p_name.end_byte].decode('utf8'))
            
            # Extract Body
            body_node = child.child_by_field_name("body")
            body = code_bytes[body_node.start_byte:body_node.end_byte].decode('utf8').strip().replace('\n', ' \\n ') if body_node else ""

            functions.append({"name": name, "params": ",".join(params), "body": body})

        # 2. Handle Structs (C equivalent of basic classes)
        elif child.type == "declaration" and "struct_specifier" in [n.type for n in child.children]:
            struct_text = code_bytes[child.start_byte:child.end_byte].decode('utf8').strip()
            structs.append(struct_text)

        # 3. Others (Global variables, includes, etc.)
        elif child.type not in ["comment", "preproc_include", "preproc_def"]:
            text = code_bytes[child.start_byte:child.end_byte].decode('utf8').strip()
            # Filter out empty strings and lone semicolons
            if text and text != ";":
                others.append(text)

    return functions, structs, others

# --- Testing with C Code ---
c_input = """
#include <stdio.h>

struct Point {
    int x;
    int y;
};

int add(int a, int b) {
    return a + b;
}

int main() {
    int result = add(5, 10);
    printf("%d", result);
    return 0;
}
"""

funcs, structs, others = extract_c_summary(c_input)

# Display Output
for s in structs:
    print(f"STRUCT FOUND: {s}")
    print("-" * 20)

for f in funcs:
    print(f"FUNCTION: {f['name']}")
    print(f"1. Input parameters: {f['params']}")
    print(f"2. Function body: {f['body']}")
    print("-" * 20)

print(f"3. Other: {' | '.join(others)}")