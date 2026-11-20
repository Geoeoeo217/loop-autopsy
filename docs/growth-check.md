# Preview a growing-list loop

Load the optional extension once in a notebook:

```python
%load_ext loop_autopsy.growth_check
```

Put the preview command at the top of a cell containing ordinary Python:

```python
%%growthcheck
queue = [1, 2]
for item in queue:
    queue.append(item + 1)
```

The preview points to body line 2, where the loop traverses `queue`, and body
line 3, where it calls `queue.append()`. Body line numbers start just below
the magic. The preview parses the cell without running any of its statements,
so even the assignment to `queue` is skipped. If the intent is to process the
original two items once each, change the loop header to `for item in queue.copy():`.

The check looks for `append`, `extend`, `insert`, and `+=` on the same name in
a direct `for item in name` loop. Each finding is a clue to review. Conditional
updates, a later `break`, or rebinding the name can still allow the loop to finish.
Aliases, helper calls, comprehensions, and iterators such as `enumerate(queue)`
are outside this check. Use the main loop-autopsy extension for runtime monitoring.
