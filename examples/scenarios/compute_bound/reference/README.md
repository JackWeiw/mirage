# compute_bound reference demo

Dense matmul across N taskflow workers, retiring-bound-dominated Topdown target ~55-65.

## Build
```
cmake -S examples/scenarios/compute_bound/reference -B build/cmp
cmake --build build/cmp
```

## Capture the fitting target
```
python3 examples/scenarios/compute_bound/collect_reference.py \
    --binary build/cmp/compute_bound_ref --out-dir examples/scenarios/compute_bound
```
No LLC-miss gate for compute_bound; the collector just confirms the capture is
retiring-dominated before fitting.
