"""Notes about a loop that can be read from its code, before it runs.

Four common beginner mistakes are spotted here:

- the line that should change the loop's condition sits just *after* the loop
  (wrong indentation), so it only runs once the loop has ended;
- that line is inside the loop, but inside an ``if`` whose condition is False
  every round, so it never runs (checked when the loop is found stuck);
- a ``for`` loop over an iterator that never runs out (``itertools.count()``,
  ``itertools.cycle(...)``, ``itertools.repeat(x)``) with no way out;
- a bare ``except:`` (or ``except BaseException:``) that also catches the stop
  button and loop-autopsy's own stop signal.

``analyse`` reads a whole cell once, before the loops are rewritten, and the
transformer copies what it found into each loop's ``LoopInfo``. Nothing here
stops a loop: these only add a note or a more precise hint.
Owner: SETIAWAN Matthew Henson.
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_WATCHED_LOOPS = (ast.While, ast.For)


@dataclass
class LoopFacts:
    """What ``analyse`` found about one loop."""

    notes: List[str] = field(default_factory=list)
    outside_update: Optional[Tuple[str, int, str]] = None  # (name, line, code of that line)
    branch_updates: List["BranchUpdate"] = field(default_factory=list)


# --------------------------------------------------------------------------- whole cell
def analyse(tree: ast.AST) -> Dict[int, LoopFacts]:
    """``{id(loop node): LoopFacts}`` for the loops in a cell that have something to say."""
    found: Dict[int, LoopFacts] = {}
    _visit_block(getattr(tree, "body", []), None, found)
    return found


def _facts(found: Dict[int, LoopFacts], loop: ast.AST) -> LoopFacts:
    return found.setdefault(id(loop), LoopFacts())


def _visit_block(stmts: List[ast.stmt], loop: Optional[ast.AST], found: Dict[int, LoopFacts]) -> None:
    """Walk one block of statements. ``loop`` is the watched loop whose body this is."""
    for i, stmt in enumerate(stmts):
        if isinstance(stmt, ast.While):
            update = outside_update(stmt, stmts[i + 1] if i + 1 < len(stmts) else None)
            if update is not None:
                _facts(found, stmt).outside_update = update
            hidden = branch_updates(stmt)
            if hidden:
                _facts(found, stmt).branch_updates = hidden
        if isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
            _note_catch_all(stmt, loop, found)

        if isinstance(stmt, _SCOPES):
            _visit_block(stmt.body, None, found)  # a function body runs when called, not here
            continue
        if isinstance(stmt, _WATCHED_LOOPS):
            _visit_block(stmt.body, stmt, found)
            _visit_block(stmt.orelse, loop, found)  # the else block runs once, after the loop
            continue
        for block in _blocks(stmt):
            _visit_block(block, loop, found)


def _blocks(stmt: ast.stmt):
    """The blocks of statements inside ``if``, ``with``, ``try``, ``match`` and the like."""
    for name in ("body", "orelse", "finalbody"):
        block = getattr(stmt, name, None)
        if isinstance(block, list):
            yield block
    for handler in getattr(stmt, "handlers", ()):
        yield handler.body
    for case in getattr(stmt, "cases", ()):  # match statement
        yield case.body


def _walk(nodes: Iterable[ast.AST]):
    """Every node inside ``nodes``, without entering function or class bodies."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPES):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _src(node: ast.AST, width: int = 60) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - very old Python
        return "..."
    text = text.splitlines()[0] if text else ""
    return text if len(text) <= width else text[: width - 3] + "..."


# --------------------------------------------------------------------------- 1. update after the loop
def outside_update(loop: ast.While, after: Optional[ast.stmt]) -> Optional[Tuple[str, int, str]]:
    """``(name, line, code)`` when the statement right after a ``while`` loop
    changes a variable of its condition that nothing inside the loop changes.

    For example::

        while count <= 5:
            print(count)
        count += 1          # meant to be inside the loop

    The loop can only end through its condition (it has no ``break``,
    ``return`` or ``raise``) and calls no function of the user's that could
    change the variable behind its back, so the line after it must have been
    meant to be inside it.
    """
    from .transformer import always_true, has_exit  # here: transformer imports this module

    if after is None or always_true(loop.test) or has_exit(loop.body):
        return None
    cond = [n.id for n in _walk([loop.test]) if isinstance(n, ast.Name)]
    if not cond:
        return None
    hits = [name for name in _assigned(after) if name in cond]
    if not hits:
        return None
    changed, sure = _changes([loop.test, *loop.body])
    if not sure:
        return None
    for name in hits:
        if name not in changed:
            return name, after.lineno, _src(after)
    return None


def _changes(nodes: Iterable[ast.AST]) -> Tuple[set, bool]:
    """``(names, sure)``: the variables that ``nodes`` may change, and whether
    that list is complete. It is not when they call a function of the user's
    (which might change a variable with ``global``) or use ``global`` themselves."""
    from .transformer import SAFE_FUNCS

    changed: set = set()
    sure = True
    for node in _walk(nodes):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            changed.add(node.id)
        elif isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            base = _base_name(node)
            if base:
                changed.add(base)  # count[0] = ... or obj.n = ... changes count / obj
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = _base_name(func.value)
                if base:
                    changed.add(base)  # items.pop() may change items
            elif not (isinstance(func, ast.Name) and func.id in SAFE_FUNCS):
                sure = False
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            sure = False
    return changed, sure


def _assigned(stmt: ast.stmt) -> List[str]:
    """Plain variable names a statement assigns to: ``x = ...``, ``x += ...``, ``a, b = ...``."""
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, ast.AugAssign) or (isinstance(stmt, ast.AnnAssign) and stmt.value is not None):
        targets = [stmt.target]
    else:
        return []
    names: List[str] = []
    for target in targets:
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id not in names:
                names.append(node.id)
    return names


def _base_name(node: ast.AST) -> Optional[str]:
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def indent_hint(update: Tuple[str, int, str]) -> str:
    """The hint for a loop whose update line sits after it."""
    name, line, code = update
    return (f"Line {line} (`{code}`) changes `{name}`, but it comes after the loop, so it only runs once "
            f"the loop has ended, which never happens. Did you mean to indent it so it is inside the loop?")


# --------------------------------------------------------------------------- 1b. update in a branch that never runs
@dataclass(frozen=True)
class Branch:
    """One ``if`` on the way to an update line."""

    test: str  # source of the condition, e.g. "y > 3"
    line: int  # line of the ``if`` (or ``elif``)
    keyword: str  # "if" or "elif"
    runs_when: bool  # True: the update is in the body; False: in the elif/else part
    part: str  # "" for the body, else "elif" or "else": the part the update is in
    stable: bool  # the condition only reads variables the loop never changes, and can be worked out safely


@dataclass(frozen=True)
class BranchUpdate:
    """A line inside the loop, inside one or more ``if`` blocks, that changes a
    variable of the loop's condition."""

    name: str
    line: int
    code: str
    path: Tuple[Branch, ...]


def branch_updates(loop: ast.While) -> List[BranchUpdate]:
    """The lines that change the condition's variables, for the variables that
    are only ever changed inside an ``if``. For example::

        while i < 10:
            if y > 3:       # y is never changed, and is 2
                i += 1

    Whether the ``if`` really never runs is decided only when the loop is found
    stuck (``branch_hint``): then every round starts with the same values, so
    a condition that reads nothing changed earlier in the round gives the same
    answer every round. That holds for ``if i > 3: i += 1`` too: ``i`` only
    changes after its condition was checked. A variable that is changed anywhere else (outside an ``if``, inside
    a nested loop, ``try`` or ``with``) is left out: that line may run.
    """
    cond = [n.id for n in _walk([loop.test]) if isinstance(n, ast.Name)]
    if not cond:
        return []
    if not _changes([loop.test, *loop.body])[1]:
        return []
    names = set(cond)
    sites: Dict[str, List[BranchUpdate]] = {}
    spoiled: set = set()

    def visit(stmts: List[ast.stmt], path: Tuple[Branch, ...], elif_of: Optional[ast.If], prior: set) -> None:
        # ``prior``: what may have changed earlier in the same round, before these statements.
        # A condition that reads none of it sees the values the round started with.
        prior = set(prior)
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                in_test = _changes([stmt.test])[0]  # a walrus in the condition
                spoiled.update(names & in_test)
                is_elif = elif_of is not None and len(stmts) == 1 and stmt.col_offset == elif_of.col_offset
                test, keyword = ast.unparse(stmt.test), ("elif" if is_elif else "if")
                stable = _evaluable(stmt.test) and not (_loaded(stmt.test) & (prior | in_test))
                visit(stmt.body, path + (Branch(test, stmt.lineno, keyword, True, "", stable),), None,
                      prior | in_test)
                if stmt.orelse:
                    part = "elif" if (len(stmt.orelse) == 1 and isinstance(stmt.orelse[0], ast.If)
                                      and stmt.orelse[0].col_offset == stmt.col_offset) else "else"
                    visit(stmt.orelse, path + (Branch(test, stmt.lineno, keyword, False, part, stable),), stmt,
                          prior | in_test)
                prior |= _changes([stmt])[0]
                continue
            prior |= _changes([stmt])[0]
            hit = names & _changes([stmt])[0]
            simple = path and not any(isinstance(getattr(stmt, b, None), list) for b in ("body", "handlers", "cases"))
            for name in hit:
                if simple and name in _assigned(stmt):
                    sites.setdefault(name, []).append(BranchUpdate(name, stmt.lineno, _src(stmt), path))
                else:
                    spoiled.add(name)  # changed outside an if, or somewhere this does not follow

    visit(loop.body, (), None, _changes([loop.test])[0])
    return [u for name in cond if name not in spoiled for u in sites.pop(name, [])]


def _loaded(node: ast.AST) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


# Only these can appear in a condition that loop-autopsy works out itself.
_EVAL_CALLS = {"len": len, "abs": abs, "min": min, "max": max, "round": round, "sum": sum,
               "int": int, "float": float, "str": str, "bool": bool}
_EVAL_NODES = (ast.Expression, ast.Name, ast.Constant, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.BinOp,
               ast.Subscript, ast.Slice, ast.Tuple, ast.List, ast.Call, ast.cmpop, ast.boolop,
               ast.unaryop, ast.operator, ast.expr_context)


def _evaluable(test: ast.AST) -> bool:
    """Plain names, numbers, comparisons, arithmetic, indexing and a few safe
    built-ins: no attribute, no call of the user's code, no ``:=``."""
    for node in ast.walk(test):
        if not isinstance(node, _EVAL_NODES):
            return False
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _EVAL_CALLS
                                               and not node.keywords):
            return False
    return True


def _evaluate(test: str, values: Dict[str, Any]) -> Optional[bool]:
    """Whether ``test`` is true with ``values``, or ``None`` when that cannot be
    worked out safely. Only built-in values are used (``plain``), so no
    ``__eq__`` or ``__bool__`` of the user's runs here."""
    from .detectors import OPAQUE, full_fp  # here: detectors imports this module

    try:
        tree = ast.parse(test, mode="eval")
    except SyntaxError:
        return None
    scope = {}
    for name in _loaded(tree):
        if name in _EVAL_CALLS and name not in values:
            continue
        if name not in values or full_fp(values[name]) is OPAQUE:
            return None
        scope[name] = values[name]
    try:
        return bool(eval(compile(tree, "<loop-autopsy>", "eval"), {"__builtins__": dict(_EVAL_CALLS)}, scope))
    except Exception:
        return None


def _skipped_by(update: BranchUpdate, values: Dict[str, Any]) -> Optional[Tuple[Branch, bool]]:
    """The ``if`` that keeps ``update`` from running, with the value of its condition."""
    for branch in update.path:
        if not branch.stable:
            continue
        result = _evaluate(branch.test, values)
        if result is not None and result != branch.runs_when:
            return branch, result
    return None


def branch_hint(updates: Iterable[BranchUpdate], values: Dict[str, Any]) -> Optional[str]:
    """For a stuck loop: the hint when every line that changes a variable of
    its condition sits in an ``if`` whose condition is never met. ``values``
    are the watched variables; the loop is stuck, so they are the same every
    round, and so is the answer of each condition."""
    by_name: Dict[str, List[BranchUpdate]] = {}
    for update in updates:
        by_name.setdefault(update.name, []).append(update)
    for name, group in by_name.items():
        reasons = [_skipped_by(u, values) for u in group]
        if not all(reasons):
            continue
        update, (branch, result) = group[0], reasons[0]
        test = _src(ast.parse(branch.test, mode="eval").body, 50)
        shown = [f"`{n} = {_short(values[n])}`" for n in sorted(_loaded(ast.parse(branch.test, mode="eval")))
                 if n in values]
        state = f"{'True' if result else 'False'} every round"
        if shown:
            state += f" ({', '.join(shown)} never {'changes' if len(shown) == 1 else 'change'})"
        if branch.part:
            where = f"in the `{branch.part}` part of `{branch.keyword} {test}:` (line {branch.line})"
            why = f"`{test}` is {state}, so that part is always skipped"
        else:
            where = f"inside `{branch.keyword} {test}:` (line {branch.line})"
            why = f"`{test}` is {state}"
        others = " The other lines that change it are skipped the same way." if len(group) > 1 else ""
        return (f"Line {update.line} (`{update.code}`) changes `{name}`, but it never runs: it is {where}, "
                f"and {why}.{others} Check that condition, or move the line out of the `if` "
                f"if it should run every round.")
    return None


def _short(value: Any, width: int = 30) -> str:
    text = repr(value)
    return text if len(text) <= width else text[: width - 3] + "..."


# --------------------------------------------------------------------------- 2. endless iterators
_ENDLESS_TYPES = {itertools.count: "itertools.count", itertools.cycle: "itertools.cycle"}


def endless_iterable(obj: Any) -> Optional[str]:
    """The name of an iterator that never runs out (``"itertools.count"``), or ``None``.

    Only iterators that are endless by construction count. ``repeat(x, 3)``
    stops after 3 items, so only ``repeat(x)`` without a count is endless.
    """
    t = type(obj)
    if t in _ENDLESS_TYPES:
        return _ENDLESS_TYPES[t]
    if t is itertools.repeat:
        try:
            obj.__length_hint__()  # repeat(x, n) knows its length; repeat(x) raises TypeError
        except TypeError:
            return "itertools.repeat"
    return None


def endless_iterator_note(header: str, lineno: int, kind: str, time_limit: float) -> str:
    """Shown when a ``for`` loop over an endless iterator, with no way out, starts."""
    if time_limit > 0:
        until = f"loop-autopsy stops it after the {time_limit:.0f} s time limit, or press ■"
    else:
        until = "press ■ to stop it"
    return (f"[loop-autopsy] Note: `{header}` (line {lineno}) loops over `{kind}`, which never runs out, "
            f"and has no `break`, `return` or `raise` inside, so it can only end with an error. "
            f"Add a `break` for when the work is done; until then {until}.")


# --------------------------------------------------------------------------- 3. catch-all except
def _catches_stop(handler: ast.ExceptHandler) -> bool:
    """``except:`` or ``except BaseException:``: also catches ■ and loop-autopsy's stop."""
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(t, ast.Name) and t.id == "BaseException" for t in types)


def _except_src(handler: ast.ExceptHandler) -> str:
    return "except:" if handler.type is None else f"except {_src(handler.type, 40)}:"


def _first_loop(stmts: List[ast.stmt]) -> Optional[ast.AST]:
    """The first watched loop in reading order, outside function bodies."""
    for stmt in stmts:
        if isinstance(stmt, _SCOPES):
            continue
        if isinstance(stmt, _WATCHED_LOOPS):
            return stmt
        for block in _blocks(stmt):
            found = _first_loop(block)
            if found is not None:
                return found
    return None


def _note_catch_all(stmt: ast.AST, loop: Optional[ast.AST], found: Dict[int, LoopFacts]) -> None:
    """Attach one note per catch-all ``except`` to the loop it affects.

    Inside a loop's body, the ``except`` catches ■, so pressing ■ only skips
    to the ``except`` block. Around a loop, it also catches loop-autopsy's
    stop signal, so a stuck loop is stopped without its report.
    """
    from .transformer import has_exit

    for handler in stmt.handlers:
        if not _catches_stop(handler):
            continue
        if any(isinstance(n, ast.Raise) for n in _walk(handler.body)):
            continue  # the stop goes on after the except block: nothing is lost
        code = _except_src(handler)
        if loop is not None:
            if has_exit(handler.body):
                continue  # ■ ends up leaving the loop anyway
            note = (f"[loop-autopsy] Note: the `{code}` on line {handler.lineno}, inside this loop, also catches "
                    f"the stop button ■, so pressing ■ usually only jumps to that `except` block and the loop carries on. "
                    f"Write `except Exception:` instead: it still catches errors but lets ■ through.")
            _facts(found, loop).notes.append(note)
            continue
        target = _first_loop(stmt.body)
        if target is None:
            continue
        note = (f"[loop-autopsy] Note: this loop runs inside a `try` whose `{code}` (line {handler.lineno}) also "
                f"catches loop-autopsy's stop signal and the stop button ■, so if the loop is stopped you will "
                f"not see why. Write `except Exception:` instead: it still catches errors but lets a stop "
                f"through. `%autopsy last` shows a report that was hidden this way.")
        _facts(found, target).notes.append(note)
