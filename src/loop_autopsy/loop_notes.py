"""Notes about a loop that can be read from its code, before it runs.

Three common beginner mistakes are spotted here:

- the line that should change the loop's condition sits just *after* the loop
  (wrong indentation), so it only runs once the loop has ended;
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
    from .transformer import SAFE_FUNCS, always_true, has_exit  # here: transformer imports this module

    if after is None or always_true(loop.test) or has_exit(loop.body):
        return None
    cond = [n.id for n in _walk([loop.test]) if isinstance(n, ast.Name)]
    if not cond:
        return None
    hits = [name for name in _assigned(after) if name in cond]
    if not hits:
        return None
    changed = set()
    for node in _walk([loop.test, *loop.body]):
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
                return None  # a function of the user's might change the variable with `global`
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            return None
    for name in hits:
        if name not in changed:
            return name, after.lineno, _src(after)
    return None


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
