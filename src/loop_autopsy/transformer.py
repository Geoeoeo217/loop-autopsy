"""Rewrite the loops in a notebook cell so they can be watched.


Every ``while``/``for`` loop gets two inserted calls::

    __loop_autopsy__.begin(ID)                 # before the loop
    while cond:
        if __loop_autopsy__.due(ID):           # first line of the body
            __loop_autopsy__.check(ID, locals())
        ...

``due`` is a cheap counter; ``check`` runs the detectors (monitor.py).
"""

from __future__ import annotations

import ast
from collections import deque
from typing import Callable, Iterable, List, Optional, Set, Tuple

from . import loop_notes
from .config import LoopInfo

RUNTIME_NAME = "__loop_autopsy__"

SAFE_FUNCS = frozenset(
    """abs all any bin bool chr dict divmod enumerate float format frozenset hex
    int isinstance len list max min oct ord pow print range repr reversed round
    set sorted str sum tuple type zip""".split()
)
SAFE_MODULES = frozenset({"math"})
COMPARE_OPS = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.NotEq: "!="}


# --------------------------------------------------------------------------- analysis
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _is_inserted_check(node: ast.AST) -> bool:
    """The ``if __loop_autopsy__.due(ID): ...`` block added to an inner loop."""
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Attribute)
            and isinstance(node.test.func.value, ast.Name) and node.test.func.value.id == RUNTIME_NAME)


def _walk_loop(nodes: Iterable[ast.AST]):
    """Walk the given nodes, skipping nested function/class bodies (their names are not the
    loop's variables) and the checks already inserted into inner loops."""
    queue = deque(nodes)  # breadth-first, so names come out in reading order
    while queue:
        node = queue.popleft()
        if isinstance(node, _SCOPES) or _is_inserted_check(node):
            continue
        yield node
        queue.extend(ast.iter_child_nodes(node))


def names_in(nodes: Iterable[ast.AST]) -> List[str]:
    """All variable names (load or store) in ``nodes``, in first-seen order."""
    seen: List[str] = []
    for node in _walk_loop(nodes):
        if isinstance(node, ast.Name) and not node.id.startswith("__"):
            if node.id not in seen:
                seen.append(node.id)
    return seen


def contains_yield(nodes: Iterable[ast.AST]) -> bool:
    return any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)) for n in _walk_loop(nodes))


def method_receivers(nodes: Iterable[ast.AST]) -> Tuple[str, ...]:
    """Names used as ``name.method(...)``. Whether that is safe depends on what
    the name holds at run time (a list is fine, a module like ``random`` is not)."""
    found: List[str] = []
    for node in _walk_loop(nodes):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)):
            name = node.func.value.id
            if name not in SAFE_MODULES and name != RUNTIME_NAME and name not in found:
                found.append(name)
    return tuple(found)


def classify_calls(nodes: Iterable[ast.AST], watched: Set[str]) -> Tuple[bool, bool]:
    """Return ``(repeat_safe, waits_for_user)`` for the calls inside ``nodes``.

    Method calls on watched names are allowed here; ``method_receivers`` lists
    them so the detector can confirm at run time that they hold built-in values.
    """
    repeat_safe, waits = True, False
    for node in _walk_loop(nodes):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == RUNTIME_NAME:
            continue  # our own calls in an already-instrumented nested loop
        if isinstance(func, ast.Name) and func.id == "locals":
            continue
        if isinstance(func, ast.Name):
            if func.id == "input":
                waits = True
                repeat_safe = False
            elif func.id not in SAFE_FUNCS:
                repeat_safe = False
        elif isinstance(func, ast.Attribute):
            receiver = func.value
            if isinstance(receiver, ast.Name) and (receiver.id in SAFE_MODULES or receiver.id in watched):
                continue
            if isinstance(receiver, ast.Constant):
                continue  # e.g. ", ".join(...)
            repeat_safe = False
        else:
            repeat_safe = False
    return repeat_safe, waits


_EXIT_FUNCS = frozenset({"exit", "quit"})  # exit() / quit() in a notebook
_EXIT_METHODS = frozenset({("sys", "exit"), ("os", "_exit")})
_LOOPS = (ast.While, ast.For, ast.AsyncFor)


def always_true(test: ast.AST) -> bool:
    """``while True:`` or ``while 1:``: a condition that can never become False."""
    return isinstance(test, ast.Constant) and bool(test.value)


def has_exit(body: Iterable[ast.AST]) -> bool:
    """Can the loop with this body end from the inside?

    A ``break`` that belongs to it counts (one inside a nested loop only ends
    that loop), and so does ``return``, ``raise``, ``assert`` or a call to
    ``exit()``, ``quit()``, ``sys.exit()`` or ``os._exit()`` anywhere in it.
    Nested function and class bodies do not run as part of the loop.
    """
    stack = [(node, True) for node in body]  # (node, a break here ends our loop)
    while stack:
        node, own = stack.pop()
        if isinstance(node, _SCOPES):
            continue
        if isinstance(node, (ast.Return, ast.Raise, ast.Assert)) or (own and isinstance(node, ast.Break)):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _EXIT_FUNCS:
                return True
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and (func.value.id, func.attr) in _EXIT_METHODS):
                return True
        if isinstance(node, _LOOPS):
            stack.extend((child, False) for child in node.body)
            stack.extend((child, own) for child in node.orelse)  # the else block runs as part of us
            header = node.test if isinstance(node, ast.While) else node.iter
            stack.append((header, own))
            continue
        stack.extend((child, own) for child in ast.iter_child_nodes(node))
    return False


def _side(node: ast.AST):
    if isinstance(node, ast.Name):
        return ("name", node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return ("const", node.value)
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float))):
        return ("const", -node.operand.value)
    return None


def compare_spec(test: ast.AST) -> Optional[tuple]:
    """``("name","i"), "<", ("const",10)`` for ``i < 10``; ``None`` if not simple."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return None
    op = COMPARE_OPS.get(type(test.ops[0]))
    left, right = _side(test.left), _side(test.comparators[0])
    if op is None or left is None or right is None:
        return None
    if left[0] == "const" and right[0] == "const":
        return None
    return (left, op, right)


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - very old Python
        return "..."


# --------------------------------------------------------------------------- transformer
class LoopTransformer(ast.NodeTransformer):
    """IPython AST transformer. ``register`` stores each LoopInfo and returns its id."""

    def __init__(self, register: Callable[[LoopInfo], int], max_watched: Callable[[], int] = lambda: 25,
                 is_paused: Callable[[], bool] = lambda: False):
        super().__init__()
        self.register = register
        self.max_watched = max_watched
        self.is_paused = is_paused
        self._facts: dict = {}

    # IPython calls transformer.visit(module) once per cell.
    def visit_Module(self, node: ast.Module):
        if self.is_paused():
            return node
        self._facts = loop_notes.analyse(node)  # read the cell before any loop is rewritten
        self.generic_visit(node)
        return ast.fix_missing_locations(node)

    def visit_While(self, node: ast.While):
        self.generic_visit(node)  # nested loops first
        if contains_yield([node.test, *node.body]):
            return node
        cond = names_in([node.test])
        watched = self._watched(cond, node.body)
        safe, waits = classify_calls([node.test, *node.body], set(watched))
        facts = self._facts.get(id(node), loop_notes.LoopFacts())
        info = LoopInfo(
            loop_id=-1, kind="while", lineno=node.lineno,
            header=f"while {_unparse(node.test)}:", test_src=_unparse(node.test),
            cond_names=tuple(cond), watched=tuple(watched), compare=compare_spec(node.test),
            receivers=method_receivers([node.test, *node.body]),
            repeat_safe=safe, waits_for_user=waits,
            always_true=always_true(node.test), exits=has_exit(node.body),
            notes=tuple(facts.notes), outside_update=facts.outside_update,
        )
        return self._instrument(node, info)

    def visit_For(self, node: ast.For):
        self.generic_visit(node)
        if contains_yield([node.iter, *node.body]):
            return node
        cond = names_in([node.iter])
        watched = self._watched(cond, node.body)
        _, waits = classify_calls([node.iter, *node.body], set(watched))
        info = LoopInfo(
            loop_id=-1, kind="for", lineno=node.lineno,
            header=f"for {_unparse(node.target)} in {_unparse(node.iter)}:",
            cond_names=tuple(cond), watched=tuple(watched),
            iter_name=node.iter.id if isinstance(node.iter, ast.Name) else None,
            repeat_safe=False, waits_for_user=waits, exits=has_exit(node.body),
            notes=tuple(self._facts.get(id(node), loop_notes.LoopFacts()).notes),
        )
        result = self._instrument(node, info)
        self._measure_iterable(node, info.loop_id)
        return result

    visit_AsyncFor = ast.NodeTransformer.generic_visit  # never instrument async loops

    # -- helpers
    def _watched(self, cond: List[str], body: List[ast.stmt]) -> List[str]:
        names = list(cond)
        for name in names_in(body):
            if name not in names:
                names.append(name)
        return names[: max(1, self.max_watched())]

    def _instrument(self, node, info: LoopInfo):
        loop_id = self.register(info)
        rt = lambda: ast.Name(id=RUNTIME_NAME, ctx=ast.Load())  # noqa: E731
        begin = ast.Expr(ast.Call(ast.Attribute(rt(), "begin", ast.Load()), [ast.Constant(loop_id)], []))
        check = ast.If(
            test=ast.Call(ast.Attribute(rt(), "due", ast.Load()), [ast.Constant(loop_id)], []),
            body=[ast.Expr(ast.Call(
                ast.Attribute(rt(), "check", ast.Load()),
                [ast.Constant(loop_id), ast.Call(ast.Name("locals", ast.Load()), [], [])], [],
            ))],
            orelse=[],
        )
        for new in (begin, check):
            ast.copy_location(new, node)
            for child in ast.walk(new):
                ast.copy_location(child, node)
        node.body.insert(0, check)
        return [begin, node]

    def _measure_iterable(self, node: ast.For, loop_id: int) -> None:
        """``for x in data:`` becomes ``for x in __loop_autopsy__.sized(ID, data):``.

        ``sized`` notes how many rounds the loop will run (when ``data`` has a
        length) and returns ``data`` itself, so the loop behaves exactly as before.
        In ``enumerate(data)`` it is ``data`` that is measured.
        """
        target = node.iter
        if (isinstance(target, ast.Call) and isinstance(target.func, ast.Name) and target.func.id == "enumerate"
                and target.args and not isinstance(target.args[0], ast.Starred)):
            parent, target = target, target.args[0]
        else:
            parent = None
        call = ast.Call(ast.Attribute(ast.Name(id=RUNTIME_NAME, ctx=ast.Load()), "sized", ast.Load()),
                        [ast.Constant(loop_id), target], [])
        for child in ast.walk(call):
            if child is not target and not hasattr(child, "lineno"):
                ast.copy_location(child, target)
        if parent is None:
            node.iter = call
        else:
            parent.args[0] = call
