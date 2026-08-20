# memory_bound reference demo

LLC-missing random-access scan over per-worker buffers (>= 2-3x per-NUMA-LLC),
backend_bound-dominated Topdown target ~65-75.

## Build (on the Kunpeng ARM box)
```
cmake -S examples/scenarios/memory_bound/reference -B build/mem
cmake --build build/mem
```

## Run standalone (sanity)
```
numactl --cpunodebind=0 --membind=0 taskset -c 0-63 ./build/mem/memory_bound_ref
```

## Capture the fitting target
```
python3 examples/scenarios/memory_bound/collect_reference.py \
    --binary build/mem/memory_bound_ref --out-dir examples/scenarios/memory_bound
```
The collector watches stdout for `__MEASUREMENT_WINDOW_START__`, then starts perf +
devkit topdown (steady-state only). It gates on LLC-miss > 90%; if the per-worker
buffer is too small for this box's LLC, it warns — fix `collection.yaml`'s
`per_worker_buffer_mb` and re-capture.
