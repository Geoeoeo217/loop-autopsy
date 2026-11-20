# Find the notebook line that accumulated memory

Load the extension and start an allocation checkpoint in a separate cell:

```python
%load_ext loop_autopsy.extras.growth_trace
%growthtrace mark
```

Run a small loop in the next cell:

```python
chunks = []
for i in range(4):
    chunks.append(bytearray(65536))
```

Run `%growthtrace show 3` in a third cell. The append line should rank near the
top because all four buffers are still alive. Each row shows the net increase
in live traced bytes for a notebook source line, followed by the source text
when available. The default is five lines. After a loop-autopsy stop, run the
same command in a fresh cell to inspect the surviving allocations.

Run `%growthtrace mark` again to replace the baseline, or `%growthtrace stop`
to discard it and end tracing started by this extension. A pre-existing
tracemalloc session stays active when the checkpoint is cleared.

Tracing adds overhead while active. The report covers allocations attributed
directly to standard IPython or ipykernel cell filenames. Temporary allocations
freed before the report, allocations attributed to imported modules, and native
buffers outside Python's tracing hooks can be absent. The result measures live
allocation growth and does not predict whether a loop will terminate.
