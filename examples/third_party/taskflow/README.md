# taskflow (vendored)

- **Upstream:** https://github.com/taskflow/taskflow
- **Pinned tag:** v3.9.0 (last v3.x; stays C++17 — v4.x requires C++20)
- **Source archive:** https://github.com/taskflow/taskflow/archive/refs/tags/v3.9.0.tar.gz
- **Archive SHA-256:** d872a19843d12d437eba9b8664835b7537b92fe01fdb33ed92ca052d2483be2d
- **License:** MIT (see `LICENSE`)

## Per-file SHA-256 manifest

See `manifest.sha256`. Re-verify with:
```
cd examples/third_party/taskflow && sha256sum -c manifest.sha256
```

## How it was vendored

Fetched the v3.9.0 tarball, copied `taskflow/` (the header set) + `LICENSE`. No patches. The C++17
reference demos include it via `#include <taskflow/taskflow.hpp>` with the include directory pointed
at `examples/third_party/taskflow`.
