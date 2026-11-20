"""Preview notebook loops for direct changes to the collection being traversed."""

import ast

from IPython.core.error import UsageError


class _BodyChanges(ast.NodeVisitor):
    """Inspect statements in the current scope for writes to one source name."""

    def __init__(self, name):
        self.name = name
        self.hits = []

    def visit_Call(self, node):
        method = node.func
        if (isinstance(method, ast.Attribute)
                and isinstance(method.value, ast.Name)
                and method.value.id == self.name
                and method.attr in ("append", "extend", "insert")):
            self.hits.append((node.lineno, method.attr + "()"))
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if (isinstance(node.target, ast.Name) and node.target.id == self.name
                and isinstance(node.op, ast.Add)):
            self.hits.append((node.lineno, "+="))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef
    visit_GeneratorExp = visit_FunctionDef
    # Comprehensions bind targets in their own scope.
    visit_ListComp = visit_FunctionDef
    visit_SetComp = visit_FunctionDef
    visit_DictComp = visit_FunctionDef


def check_source(source):
    """Return advisory findings for direct 'for ... in name' loops."""
    tree = ast.parse(source)
    findings = []
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.For) or not isinstance(loop.iter, ast.Name):
            continue
        name = loop.iter.id
        visitor = _BodyChanges(name)
        for statement in loop.body:
            visitor.visit(statement)
        for line, operation in visitor.hits:
            findings.append((loop.lineno, line, name, operation))
    return sorted(findings)


def load_ipython_extension(ip):
    if ip.find_cell_magic("growthcheck") is not None:
        raise UsageError("A %%growthcheck magic is already registered.")

    def growthcheck(line, cell):
        if line.strip():
            raise UsageError("Put %%growthcheck alone on the first line of the cell.")
        try:
            findings = check_source(cell)
        except SyntaxError as error:
            raise UsageError(f"Python syntax error on body line {error.lineno}: {error.msg}") from None
        if not findings:
            print("No matching growth patterns found. Cell was inspected without running it.")
            return
        for loop_line, edit_line, name, operation in findings:
            print(f"Body line {loop_line} iterates over {name}. "
                  f"Line {edit_line} uses {operation} on the same name.")
        print("If the name still refers to the list being traversed, adding items can "
              "extend the loop. To process only the original items, iterate over a copy.")

    ip.register_magic_function(growthcheck, magic_kind="cell", magic_name="growthcheck")
    ip._loop_autopsy_growthcheck = growthcheck


def unload_ipython_extension(ip):
    magic = getattr(ip, "_loop_autopsy_growthcheck", None)
    if magic is not None:
        if ip.find_cell_magic("growthcheck") is magic:
            del ip.magics_manager.magics["cell"]["growthcheck"]
        del ip._loop_autopsy_growthcheck
