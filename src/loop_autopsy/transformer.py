"""Rewrite the loops in a notebook cell so they can be watched.


Every ``while``/``for`` loop gets two inserted calls::

    __loop_autopsy__.begin(ID)                 # before the loop
    try:
        while cond:
            if __loop_autopsy__.due(ID):       # first line of the body
                __loop_autopsy__.check(ID, locals(), globals())
            ...
    finally:                                   # the run is over: back to the outer run, if any
        if ID in __loop_autopsy__.stacks: ...

``due`` is a cheap counter; ``check`` runs the detectors (monitor.py). The
``finally`` block makes a loop that runs inside itself (a loop in a recursive
function) keep one record per run. It uses no function calls, so it still
works while Python is unwinding a ``RecursionError``.
"""

from __future__ import annotations

import ast
from collections import deque
from typing import Callable, Deque, Iterable, List, Optional, Set, Tuple

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


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _bound_by(comp: ast.AST) -> Set[str]:
    """The variables a comprehension's ``for`` clauses bind (``x`` in ``[x for x in data]``)."""
    return {n.id for gen in getattr(comp, "generators", ()) for n in ast.walk(gen.target)
            if isinstance(n, ast.Name)}


def names_in(nodes: Iterable[ast.AST]) -> List[str]:
    """All variable names (load or store) in ``nodes``, in first-seen order.

    A comprehension's own variable (``v`` in ``any(v > 0 for v in vals)``) is
    left out: it lives inside the comprehension, so the loop can never see it.
    """
    seen: List[str] = []
    queue: Deque[Tuple[ast.AST, frozenset]] = deque((node, frozenset()) for node in nodes)  # reading order
    while queue:
        node, hidden = queue.popleft()
        if isinstance(node, _SCOPES) or _is_inserted_check(node):
            continue
        if isinstance(node, ast.Name) and not node.id.startswith("__") and node.id not in hidden:
            if node.id not in seen:
                seen.append(node.id)
        if isinstance(node, _COMPREHENSIONS):
            hidden = hidden | _bound_by(node)
        queue.extend((child, hidden) for child in ast.iter_child_nodes(node))
    return seen


def _value_ids(nodes: Iterable[ast.AST]) -> Set[int]:
    return {id(n.value) for n in _walk_loop(nodes) if isinstance(n, ast.Attribute)}


def attr_chains(nodes: Iterable[ast.AST]) -> List[str]:
    """``base.attr`` for the plain attributes read or written (``self.n``, ``c.count``),
    in first-seen order. Method calls (``self.step()``) are not values and are left out."""
    nodes = list(nodes)
    funcs = {id(n.func) for n in _walk_loop(nodes) if isinstance(n, ast.Call)}
    found: List[str] = []
    for node in _walk_loop(nodes):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and id(node) not in funcs
                and node.value.id not in SAFE_MODULES and not node.value.id.startswith("__")):
            chain = f"{node.value.id}.{node.attr}"
            if chain not in found:
                found.append(chain)
    return found


def attribute_only(nodes: Iterable[ast.AST]) -> Set[str]:
    """Names used only in front of a dot, like ``self`` in ``while self.n < 10: self.n += 1``.
    Such an object is followed through its attributes instead of as a whole."""
    nodes = list(nodes)
    values = _value_ids(nodes)
    bases = {n.value.id for n in _walk_loop(nodes)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    bare = {n.id for n in _walk_loop(nodes) if isinstance(n, ast.Name) and id(n) not in values}
    return {b for b in bases - bare if b not in SAFE_MODULES}


def contains_yield(nodes: Iterable[ast.AST]) -> bool:
    """A ``yield``: the loop is a generator's, which pauses until someone asks for the next value."""
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _walk_loop(nodes))


def contains_await(nodes: Iterable[ast.AST]) -> bool:
    """An ``await`` (or ``async for`` / ``async with``): other tasks run while the loop waits."""
    return any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in _walk_loop(nodes))


def _receiver_name(node: ast.AST) -> Optional[str]:
    """``nums`` for ``nums.append``, ``self.items`` for ``self.items.append``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def method_receivers(nodes: Iterable[ast.AST]) -> Tuple[str, ...]:
    """Names used as ``name.method(...)``. Whether that is safe depends on what
    the name holds at run time (a list is fine, a module like ``random`` is not)."""
    found: List[str] = []
    for node in _walk_loop(nodes):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = _receiver_name(node.func.value)
            if name and name not in SAFE_MODULES and name != RUNTIME_NAME and name not in found:
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
            if _receiver_name(receiver) in watched:
                continue  # self.items.append(x): checked at run time, like a name
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


def changed_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Names the loop body can change: assigned (``x = ...``, ``x += 1``, ``for x in``),
    deleted, or changed in place (``x[i] = ...``, ``x.attr = ...``, ``x.append(...)``)."""
    found: Set[str] = set()
    for node in _walk_loop(nodes):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            found.add(node.id)
        elif isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            base = node.value
            while isinstance(base, (ast.Subscript, ast.Attribute)):
                base = base.value
            if isinstance(base, ast.Name):
                found.add(base.id)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name)):
            found.add(node.func.value.id)  # nums.append(x) changes nums
    return found


def _side(node: ast.AST):
    if isinstance(node, ast.Name):
        return ("name", node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id not in SAFE_MODULES:
        return ("name", f"{node.value.id}.{node.attr}")  # while self.n < 10
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return ("const", node.value)
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float))):
        return ("const", -node.operand.value)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"
            and len(node.args) == 1 and not node.keywords):
        inner = _side(node.args[0])
        if inner is not None and inner[0] == "name":
            return ("len", inner[1])  # while i < len(nums), while i < len(self.items)
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


def or_specs(test: ast.AST) -> Tuple[Tuple[tuple, str], ...]:
    """``(spec, source)`` for every simple comparison in ``a < 10 or b > 0``. The loop
    goes on while any part is True, so one part moving the wrong way keeps it going."""
    if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or)):
        return ()
    found = []
    for part in test.values:
        spec = compare_spec(part)
        if spec is not None:
            found.append((spec, _unparse(part)))
    return tuple(found)


def and_spec(test: ast.AST) -> Tuple[Optional[Tuple[tuple, str]], Tuple[str, ...]]:
    """For ``i < 10 and not done``: the comparison to follow, and the names in the
    other parts. That comparison counts only if the other parts use other variables
    and call nothing that could change by itself; then, while those variables stay
    the same, the loop can only end through the comparison. In ``0 <= i < n`` both
    parts use ``i``, so moving away from one end moves towards the other: no proof."""
    if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)):
        return None, ()
    for i, part in enumerate(test.values):
        spec = compare_spec(part)
        if spec is None:
            continue
        others = test.values[:i] + test.values[i + 1:]
        mine = set(names_in([part])) | set(attr_chains([part]))
        theirs = [n for n in names_in(others) if n not in attribute_only(others)] + attr_chains(others)
        if mine & set(theirs) or {c.split(".")[0] for c in mine} & set(theirs):
            continue
        safe, _ = classify_calls(others, set())
        if not safe:
            continue
        return (spec, _unparse(part)), tuple(n for n in theirs if n not in SAFE_FUNCS)
    return None, ()


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - very old Python
        return "..."


# --------------------------------------------------------------------------- transformer
# Runs when a loop run ends, however it ends: drop this run's record from the loop's
# stack, and make the run it interrupted (a loop in a recursive function) current again.
# Only subscripts, no calls: it must not fail while a RecursionError unwinds the stack.
_END = """
if {id} in {rt}.stacks:
    del {rt}.stacks[{id}][-1:]
    {rt}.states[{id}] = ({rt}.stacks[{id}][-1:] or [{rt}.states[{id}]])[0]
    {rt}.loops_running -= 1
"""


# Built-ins that take every item of what they are given: over an iterator that never
# runs out they never return (and list/set/... fill memory first).
FULL_CONSUMERS = frozenset({"list", "tuple", "set", "frozenset", "sorted", "sum", "min", "max", "dict", "deque"})
# Built-ins that may stop early (any finds a True item); still watched for time and memory.
EARLY_CONSUMERS = frozenset({"any", "all"})


def _short(text: str, width: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


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
            return node  # a generator's loop: it only runs when asked for a value
        hidden = attribute_only([node.test, *node.body])
        cond = [n for n in names_in([node.test]) if n not in hidden] + attr_chains([node.test])
        watched, unwatched = self._watched(cond, node.body, hidden)
        safe, waits = classify_calls([node.test, *node.body], set(watched))
        facts = self._facts.get(id(node), loop_notes.LoopFacts())
        both, guard = and_spec(node.test)
        info = LoopInfo(
            loop_id=-1, kind="while", lineno=node.lineno,
            header=f"while {_unparse(node.test)}:", test_src=_unparse(node.test),
            cond_names=tuple(cond), watched=tuple(watched), compare=compare_spec(node.test),
            receivers=method_receivers([node.test, *node.body]),
            repeat_safe=safe, waits_for_user=waits,
            always_true=always_true(node.test), exits=has_exit(node.body),
            notes=tuple(facts.notes), outside_update=facts.outside_update,
            branch_updates=tuple(facts.branch_updates), unwatched_changes=unwatched,
            suspends=contains_await([node.test, *node.body]), attr_only=tuple(sorted(hidden)),
            compare_or=or_specs(node.test), compare_and=both, and_guard=guard,
        )
        return self._instrument(node, info)

    def visit_For(self, node):
        self.generic_visit(node)
        if contains_yield([node.iter, *node.body]):
            return node
        hidden = attribute_only([node.iter, *node.body])
        cond = [n for n in names_in([node.iter]) if n not in hidden] + attr_chains([node.iter])
        watched, _ = self._watched(cond, node.body, hidden)
        _, waits = classify_calls([node.iter, *node.body], set(watched))
        is_async = isinstance(node, ast.AsyncFor)
        info = LoopInfo(
            loop_id=-1, kind="for", lineno=node.lineno,
            header=f"{'async ' if is_async else ''}for {_unparse(node.target)} in {_unparse(node.iter)}:",
            cond_names=tuple(cond), watched=tuple(watched), iter_name=self._iter_name(node.iter),
            repeat_safe=False, waits_for_user=waits, exits=has_exit(node.body),
            notes=tuple(self._facts.get(id(node), loop_notes.LoopFacts()).notes),
            suspends=is_async or contains_await(node.body), attr_only=tuple(sorted(hidden)),
        )
        result = self._instrument(node, info)
        self._measure_iterable(node, info.loop_id)
        return result

    visit_AsyncFor = visit_For  # async for: watched for time and memory (other tasks run while it waits)

    # -- comprehensions: [f(x) for x in data], (x for x in data), {...}
    def visit_ListComp(self, node):
        return self._comprehension(node, lazy=False)

    visit_SetComp = visit_DictComp = visit_ListComp

    def visit_GeneratorExp(self, node):
        # A generator expression is lazy: whoever uses it may stop early (any, next, zip),
        # unless it is handed straight to list(), sum() and the like (see visit_Call).
        return self._comprehension(node, lazy=not getattr(node, "_la_consumed", False))

    def _comprehension(self, node, lazy: bool):
        self.generic_visit(node)
        if any(gen.is_async for gen in node.generators):
            return node
        header = _short(_unparse(node))
        for gen in node.generators:
            info = LoopInfo(loop_id=-1, kind="comprehension", lineno=node.lineno, header=header,
                            repeat_safe=False, exits=lazy, lazy=lazy)
            gen.iter = self._runtime_call("watch", self.register(info), [gen.iter])
        return node

    def visit_Call(self, node: ast.Call):
        """``list(it)``, ``sum(it)``, ``", ".join(it)``...: these loop over ``it`` in C,
        where no check can run, so ``it`` is handed over through ``feed``: it stops
        at once an iterator that is proven never to run out, and watches any other
        iterator for time and memory. Lists, ranges and other sized values pass
        through untouched."""
        func = node.func
        name = func.id if isinstance(func, ast.Name) else None
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in ("collections", "builtins")):
            name = func.attr  # collections.deque(it), builtins.list(it)
        is_join = (isinstance(func, ast.Attribute) and func.attr == "join"
                   and isinstance(func.value, ast.Constant) and isinstance(func.value.value, str))
        consumer = name in FULL_CONSUMERS or name in EARLY_CONSUMERS or is_join
        # min(a, b) compares its arguments: only a single iterable is looped over (sum may add a start)
        consumer = consumer and len(node.args) <= (2 if name == "sum" else 1)
        if consumer and node.args and not isinstance(node.args[0], ast.Starred):
            first = node.args[0]
            if isinstance(first, ast.GeneratorExp) and (name in FULL_CONSUMERS or is_join):
                first._la_consumed = True  # type: ignore[attr-defined]  # every item is taken
        self.generic_visit(node)
        if not consumer or not node.args or isinstance(node.args[0], (ast.Starred, ast.GeneratorExp)):
            return node  # a generator expression is watched by its own clauses
        info = LoopInfo(loop_id=-1, kind="comprehension", lineno=node.lineno, header=_short(_unparse(node)),
                        repeat_safe=False, exits=not (name in FULL_CONSUMERS or is_join),
                        lazy=not (name in FULL_CONSUMERS or is_join))
        if is_join or name is None:
            target: ast.expr = ast.Constant("join")
        elif isinstance(func, ast.Attribute):
            target = ast.Attribute(ast.Name(id=func.value.id, ctx=ast.Load()), func.attr, ast.Load())  # type: ignore[attr-defined]
        else:
            target = ast.Name(id=name, ctx=ast.Load())
        node.args[0] = self._runtime_call("feed", self.register(info), [target, node.args[0]])
        return node

    # -- helpers
    @staticmethod
    def _runtime_call(method: str, loop_id: int, args: List[ast.expr]) -> ast.Call:
        call = ast.Call(ast.Attribute(ast.Name(id=RUNTIME_NAME, ctx=ast.Load()), method, ast.Load()),
                        [ast.Constant(loop_id), *args], [])
        anchor = args[-1]
        for child in ast.walk(call):
            if not hasattr(child, "lineno"):
                ast.copy_location(child, anchor)
        return call

    @staticmethod
    def _iter_name(node: ast.AST) -> Optional[str]:
        """The list a for loop goes through: ``nums`` in ``for x in nums`` and in
        ``for i, x in enumerate(nums)``, ``self.items`` in ``for x in self.items``."""
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enumerate"
                and node.args and not isinstance(node.args[0], ast.Starred)):
            node = node.args[0]
        return _receiver_name(node)

    def _watched(self, cond: List[str], body: List[ast.stmt], hidden=frozenset()) -> Tuple[List[str], bool]:
        """The names to watch, in reading order, and whether a name the body changes
        had to be left out. Over ``max_watched``, the condition's names and the names
        the body changes are kept first: a name that is only read cannot change.
        Attributes (``self.n``) are watched too; an object used only through its
        attributes (``self``) is followed through them instead of as a whole."""
        names = list(cond)
        for name in [n for n in names_in(body) if n not in hidden] + attr_chains(body):
            if name not in names:
                names.append(name)
        limit = max(1, self.max_watched())
        if len(names) <= limit:
            return names, False
        changed = changed_names(body)
        changed |= {n for n in names if "." in n and n.split(".")[0] in changed}
        first = [n for n in names if n in cond or n in changed]
        rest = [n for n in names if n not in first]
        keep = set((first + rest)[:limit])
        return [n for n in names if n in keep], any(n in changed and n not in keep for n in names)

    def _instrument(self, node, info: LoopInfo):
        loop_id = self.register(info)
        rt = lambda: ast.Name(id=RUNTIME_NAME, ctx=ast.Load())  # noqa: E731
        begin = ast.Expr(ast.Call(ast.Attribute(rt(), "begin", ast.Load()), [ast.Constant(loop_id)], []))
        check = ast.If(
            test=ast.Call(ast.Attribute(rt(), "due", ast.Load()), [ast.Constant(loop_id)], []),
            body=[ast.Expr(ast.Call(
                ast.Attribute(rt(), "check", ast.Load()),
                [ast.Constant(loop_id), ast.Call(ast.Name("locals", ast.Load()), [], []),
                 ast.Call(ast.Name("globals", ast.Load()), [], [])], [],
            ))],
            orelse=[],
        )
        end = ast.parse(_END.format(id=loop_id, rt=RUNTIME_NAME)).body
        for new in (begin, check, *end):
            ast.copy_location(new, node)
            for child in ast.walk(new):
                ast.copy_location(child, node)
        node.body.insert(0, check)
        wrapped = ast.Try(body=[node], handlers=[], orelse=[], finalbody=end)
        ast.copy_location(wrapped, node)
        return [begin, wrapped]

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
