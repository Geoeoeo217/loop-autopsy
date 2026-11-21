"""Correct loops must never be stopped.

loop-autopsy promises to stop a loop only when it can prove the loop will not
end. Every loop below is correct, and several of them look suspicious on
purpose: a value that moves away before it comes back, a list that grows while
the loop runs, a float compared with ``!=``, a state that seems to repeat.

The rules' thresholds are lowered here (``direction_after=20``,
``jump_after=10``, ``growth_after=50``), so each loop runs well past the point
where a wrong rule would fire. A loop added here should come with a comment
saying what makes it look suspicious.
"""
import pytest

from loop_autopsy.config import LoopStopped

STRICT = "%autopsy config direction_after=20 jump_after=10 growth_after=50 estimate_after=0"

CORRECT_LOOPS = {
    # ---- counters and two pointers
    "binary search (both ends move)":
        "lo, hi = 0, 10**6\nwhile lo < hi:\n    mid = (lo + hi) // 2\n"
        "    if mid < 777:\n        lo = mid + 1\n    else:\n        hi = mid\nresult = lo",
    "two pointers meet in the middle":
        "lo, hi = 0, 1000\nwhile lo < hi:\n    lo += 1\n    hi -= 1\nresult = (lo, hi)",
    "countdown in steps of 2 with >=":
        "k = 999\nwhile k >= 0:\n    k -= 2\nresult = k",
    "!= reached exactly with whole numbers":
        "x = 0\nwhile x != 300:\n    x += 3\nresult = x",
    "float with < instead of !=":
        "x = 0.0\nwhile x < 1.0:\n    x += 0.1\nresult = round(x, 1)",
    "moves away for a while, then turns back":  # 15 rounds the wrong way, under direction_after
        "x, v = 0, -15\nwhile x < 100:\n    x += v\n    v += 1\nresult = x",
    "target moves, but slower than the counter":
        "i, end = 0, 50\nwhile i < end:\n    i += 3\n    end += 1\nresult = i",
    # ---- maths
    "Collatz (goes up and down)":
        "n = 97\nsteps = 0\nwhile n != 1:\n    n = n // 2 if n % 2 == 0 else 3 * n + 1\n    steps += 1\nresult = steps",
    "Euclid's gcd":
        "a, b = 1071, 462\nwhile b != 0:\n    a, b = b, a % b\nresult = a",
    "Newton's square root (condition is not a simple comparison)":
        "a, x = 2.0, 2.0\nwhile abs(x * x - a) > 1e-12:\n    x = (x + a / x) / 2\nresult = round(x, 6)",
    "convergence with a shrinking step":
        "total, term = 0.0, 1.0\nwhile term > 1e-9:\n    total += term\n    term /= 2\nresult = round(total, 6)",
    # ---- lists that change size
    "Fibonacci with len(...) <":
        "fib = [0, 1]\nwhile len(fib) < 40:\n    fib.append(fib[-1] + fib[-2])\nresult = fib[-1]",
    "queue that grows for a long time before it empties":  # breadth-first walk of a binary tree
        "queue = [0]\nseen = 0\nwhile len(queue) > 0:\n    node = queue.pop(0)\n    seen += 1\n"
        "    if node < 2047:\n        queue.append(2 * node + 1)\n        queue.append(2 * node + 2)\nresult = seen",
    "removing items while walking with an index":
        "nums = [0, 1, 0, 2, 0, 3] * 50\ni = 0\nwhile i < len(nums):\n    if nums[i] == 0:\n        nums.pop(i)\n"
        "    else:\n        i += 1\nresult = len(nums)",
    "list grows, but slower than the index":
        "nums = list(range(10))\ni = 0\nwhile i < len(nums):\n    if i % 3 == 0 and len(nums) < 200:\n"
        "        nums.append(i)\n    i += 1\nresult = i",
    "stack-based depth-first walk":
        "stack, count = [1], 0\nwhile stack:\n    v = stack.pop()\n    count += 1\n"
        "    if v < 500:\n        stack.extend([2 * v, 2 * v + 1])\nresult = count",
    "for loop that appends a bounded number of items":
        "nums = [0]\nfor n in nums:\n    if n < 40:\n        nums.append(n + 1)\nresult = len(nums)",
    # ---- flags, states and hidden state
    "flag set by the body":
        "data = list(range(200, 0, -1))\nswapped = True\nwhile swapped:\n    swapped = False\n"
        "    for k in range(len(data) - 1):\n        if data[k] > data[k + 1]:\n"
        "            data[k], data[k + 1] = data[k + 1], data[k]\n            swapped = True\nresult = data[:3]",
    "state machine that visits states again before it ends":
        "state, turns = 'red', 0\nwhile state != 'done':\n    turns += 1\n"
        "    state = {'red': 'green', 'green': 'yellow', 'yellow': 'red'}[state] if turns < 30 else 'done'\nresult = turns",
    "update inside an if that only runs some rounds":
        "i, tick = 0, 0\nwhile i < 50:\n    tick += 1\n    if tick % 3 == 0:\n        i += 1\nresult = i",
    "while True with a break":
        "i = 0\nwhile True:\n    i += 1\n    if i == 500:\n        break\nresult = i",
    "same variables every round, iterator moves underneath":
        "it = iter(range(100))\nwhile True:\n    v = next(it, None)\n    if v is None:\n        break\nresult = v",
    "waits on the clock":
        "import time\nstart = time.perf_counter()\nticks = 0\nwhile time.perf_counter() - start < 0.2:\n"
        "    ticks += 1\nresult = ticks > 0",
    "ends with an exception the loop catches":
        "s, i, total = '12345x', 0, 0\nwhile True:\n    try:\n        total += int(s[i])\n"
        "    except ValueError:\n        break\n    i += 1\nresult = total",
    "updates a variable through a function":
        "def step(v):\n    return v + 1\nk = 0\nwhile k < 300:\n    k = step(k)\nresult = k",
    "infinite generator, consumed a little":
        "def naturals():\n    n = 0\n    while True:\n        yield n\n        n += 1\ng = naturals()\n"
        "result = [next(g) for _ in range(5)]",
    "itertools.count with a break":
        "import itertools\nfor n in itertools.count():\n    if n * n > 2000:\n        break\nresult = n",
}


@pytest.fixture
def strict(shell):
    shell.run_cell(STRICT)
    yield shell
    shell.run_cell("%autopsy reset")


@pytest.mark.parametrize("name", sorted(CORRECT_LOOPS))
def test_correct_loop_is_never_stopped(strict, name, capsys):
    capsys.readouterr()
    strict.user_ns.pop("result", None)
    result = strict.run_cell(CORRECT_LOOPS[name])
    out = capsys.readouterr().out
    error = result.error_in_exec
    assert not isinstance(error, LoopStopped), f"{name}: wrongly stopped as {error.finding.kind}"
    assert error is None, f"{name}: raised {error!r}"
    assert "Loop autopsy" not in out
    assert "result" in strict.user_ns


# The same strict settings still catch the broken versions, so the corpus is
# not passing only because the rules were switched off.
BROKEN_LOOPS = {
    "index walks away from len(nums)": ("nums = [1, 2, 3]\ni = 0\nwhile i < len(nums):\n    i -= 1\n",
                                        "wrong_direction"),
    "index jumps over len(nums)": ("nums = 'abcdefghij'\ni = 0\nwhile i != len(nums):\n    i += 3\n",
                                   "jumped_over"),
    "index never moves": ("nums = [1, 2, 3]\ni = 0\nwhile i < len(nums):\n    print(nums[i])\n", "stuck"),
    "counter goes the wrong way": ("k = 0\nwhile k < 10:\n    k -= 1\n", "wrong_direction"),
}


@pytest.mark.parametrize("name", sorted(BROKEN_LOOPS))
def test_broken_loop_is_still_stopped(strict, name, capsys):
    code, kind = BROKEN_LOOPS[name]
    error = strict.run_cell(code).error_in_exec
    assert isinstance(error, LoopStopped) and error.finding.kind == kind, name
    assert "Loop autopsy" in capsys.readouterr().out
