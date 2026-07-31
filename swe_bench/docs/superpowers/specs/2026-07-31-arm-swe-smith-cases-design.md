# ARM SWE-smith Case Environments — Design Spec

**Date:** 2026-07-31
**Status:** Approved (user signed off each section)
**Goal:** For every case in the SWE-smith HF dataset family (`SWE-smith-py`, `-go`, `-cpp`, `-js`, `-ts`, …), produce a self-contained **ARM64-native** Dockerfile that builds the case environment, runs the gold-patch self-check, and restores a clean checkout ready for an agent — on a native ARM64 Linux server.

---

## 1. Context & Key Findings

- The local `SWE-bench/swebench/harness/` is the **multilingual** harness and already supports arm64 natively (`make_test_spec(arch="arm64")`, `FROM --platform=linux/arm64/v8`, arch-aware toolchain downloads in `dockerfiles/go.py`). That path is **not** what we take here (see Approach B below), but it remains a reference for toolchain install commands and test conventions.
- The existing `setup_vue_case.sh` is a **host-based** (non-Docker) per-case environment builder for `vuejs__core-11915`. This spec Docker-izes and ARM-izes that same per-case mental model.
- The multilingual dataset (300 cases locally) carries fields: `instance_id`, `repo`, `base_commit`, `patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `version`. SWE-smith per-language datasets are assumed schema-compatible; see §6 for the two assumptions that must be confirmed against real SWE-smith data.

## 2. Chosen Approach: B — Per-case independent Dockerfile

**User decision.** Each case gets its own self-contained `Dockerfile` + `setup_repo.sh` + `verify_gold.sh`, produced by a generator. The SWE-bench harness stays as a reference only.

**Trade-off accepted:** losing the harness's base/env image cache layering (each case reinstalls its toolchain). Justified by: independently-debuggable per case, matches the `setup_vue_case.sh` model the user already runs, and SWE-smith schema compatibility with the harness is unconfirmed.

Rejected alternatives:
- **A (reuse harness arm64 path + adapter):** leverages existing arm64 support and base/env caching, lowest effort — but couples us to harness internals and to SWE-smith being schema-compatible. User chose B for control.
- **C (generate text only, never build):** fastest to deliver but does not verify runnability.

## 3. Architecture

```
swe_bench/
├── cases/<instance_id>/            # per-case outputs (existing dir, reused)
│   ├── Dockerfile                   # generated — self-contained arm64
│   ├── setup_repo.sh                # generated — clone + checkout + deps (build-time)
│   ├── verify_gold.sh               # generated — apply gold + test + restore (run-time)
│   ├── GOLD_patch.txt               # dataset `patch`
│   ├── GOLD_test_patch.txt          # dataset `test_patch`
│   └── FAIL_TO_PASS.txt             # dataset `FAIL_TO_PASS`
├── dataset_raw/                     # SWE-smith per-lang parquet (user pulls; generator reads here)
│   ├── swe-smith-go/*.parquet
│   ├── swe-smith-cpp/*.parquet
│   └── ...
├── gen_arm_case.py                  # NEW — generator
├── arm_image_specs.yaml             # NEW — language → arm64 toolchain spec (single source of truth)
├── templates/                       # NEW — Jinja2 templates per language
│   ├── go.Dockerfile.j2 / go.setup.sh.j2
│   ├── js.Dockerfile.j2 / js.setup.sh.j2
│   ├── cpp.Dockerfile.j2 / cpp.setup.sh.j2
│   └── common.verify_gold.sh.j2     # shared by all languages
└── docs/superpowers/specs/          # this file
```

**Data flow:** `dataset_raw/*.parquet` → `gen_arm_case.py` reads one row → identifies language from dataset directory name → picks toolchain spec from `arm_image_specs.yaml` → renders Jinja2 templates → writes `cases/<id>/{Dockerfile, setup_repo.sh, verify_gold.sh}` + gold patch files → user runs `docker build` + `docker run` on ARM64 server.

**Three-layer responsibility split:**

| File | Runs at | Responsibility | Idempotent? |
|---|---|---|---|
| `Dockerfile` | build | FROM arm64 base, install toolchain, COPY scripts, run `setup_repo.sh` | yes (cacheable; does NOT apply patches or run tests) |
| `setup_repo.sh` | build (inside Dockerfile `RUN`) | clone repo @ `base_commit`, checkout, install deps | yes |
| `verify_gold.sh` | run (container `CMD`) | apply gold patch + test_patch → run `FAIL_TO_PASS` → **restore** clean checkout | yes (cleans first, can re-run) |

**Key decision:** gold self-check runs at **run-time** (`docker run`), not build-time. Build only stands up the environment so it stays cacheable and idempotent; the self-check + restore only make sense at run-time. Mirrors `setup_vue_case.sh` steps [5]/[6].

## 4. Language Identification & Toolchain Templates

### Language identification (two-tier)

1. **Dataset directory name** (primary): `dataset_raw/swe-smith-go/*.parquet` → `"go"`; `swe-smith-js` → `"js"`; `-cpp` → `"cpp"`; `-ts` → `"ts"`; `-py` → `"py"`. Most reliable — the dataset name *is* the language.
2. **`repo` pattern match** (fallback, only if dataset name has no language token): `vuejs/core`/`axios/axios` → `js`; `caddyserver/caddy`/`gohugoio/hugo` → `go`; `fmtlib/fmt`/`uutils/coreutils` → `cpp`/`rust` (ambiguous → require explicit).

Generator reads parquet grouped by dataset directory, so each row carries its language tag directly — `axios/axios` is never misclassified by name.

### Toolchain mapping — `arm_image_specs.yaml`

Single source of truth; one entry per language. Toolchain **version is NOT hardcoded here** — version comes from the case (case field if present, else inferred from repo at clone time; see §6). The yaml holds the install *method* and defaults only.

```yaml
go:
  base_image: arm64v8/ubuntu:22.04
  platform: linux/arm64/v8
  toolchain_install: |
    RUN apt-get update && apt-get install -y wget git build-essential ca-certificates
    RUN arch=$(dpkg --print-architecture) && \
        wget -qO go.tgz "https://dl.google.com/go/go{{go_version}}.linux-${arch}.tar.gz" && \
        tar -C /usr/local -xzf go.tgz && rm go.tgz
    ENV PATH=/usr/local/go/bin:$PATH
  deps_install: "go mod download"
  test_command: "go test {{test_packages}} -run '{{test_pattern}}'"
  notes: "No Chrome dependency; arm64-native cleanest path."

js:
  base_image: arm64v8/ubuntu:22.04
  platform: linux/arm64/v8
  toolchain_install: |
    RUN apt-get update && apt-get install -y curl git build-essential ca-certificates
    ENV NVM_DIR /usr/local/nvm
    RUN curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    RUN . $NVM_DIR/nvm.sh && nvm install {{node_version}}
    ENV PATH $NVM_DIR/versions/node/v{{node_version}}/bin:$PATH
    ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
  deps_install: "npm install || pnpm install"
  test_command: "{{test_runner}} {{test_file}} -t '{{test_pattern}}'"
  notes: "Puppeteer cases skip Chrome download; true browser tests are arm-unsupported (see §7)."

cpp:
  base_image: arm64v8/ubuntu:22.04
  platform: linux/arm64/v8
  toolchain_install: "RUN apt-get install -y cmake g++ make"
  deps_install: "cmake -B build && cmake --build build"
  test_command: "ctest --test-dir build -R '{{test_pattern}}' --output-on-failure"
  notes: ""
```

### Template rendering

One Jinja2 template per language (`templates/<lang>.Dockerfile.j2`, `templates/<lang>.setup.sh.j2`) plus a shared `common.verify_gold.sh.j2`. Variables injected from yaml spec + case data. Adding a new language = one yaml entry + one template pair; generator main logic unchanged.

## 5. Three Generated Artifacts — Responsibilities & Restore Semantics

(Concretely illustrated with `caddyserver__caddy-6345`, a Go case whose test is the caddyfile-adapt test `TestCaddyfileAdaptToJSON`, not a plain `go test -run`.)

### `Dockerfile` (build-time, idempotent)

```dockerfile
# ARM64 native — build on an ARM64 Linux server (no QEMU emulation)
FROM --platform=linux/arm64/v8 arm64v8/ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=noninteractive

# Toolchain (version injected from case/inference)
RUN apt-get update && apt-get install -y wget git build-essential ca-certificates
RUN arch=$(dpkg --print-architecture) && \
    wget -qO go.tgz "https://dl.google.com/go/go{{go_version}}.linux-${arch}.tar.gz" && \
    tar -C /usr/local -xzf go.tgz && rm go.tgz
ENV PATH=/usr/local/go/bin:$PATH
RUN go version

# Copy scripts + gold patches into the image
COPY setup_repo.sh verify_gold.sh /opt/case/
COPY GOLD_patch.txt GOLD_test_patch.txt FAIL_TO_PASS.txt /opt/case/

# Build-time: stand up environment only — NO patches applied, NO tests run (cacheable)
RUN /bin/bash /opt/case/setup_repo.sh

WORKDIR /testbed
# Run-time entry: gold self-check (which restores internally)
CMD ["/bin/bash", "/opt/case/verify_gold.sh"]
```

### `setup_repo.sh` (build-time, environment only)

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_DIR=/testbed
git clone https://github.com/{{repo}} "$REPO_DIR"
cd "$REPO_DIR"
git checkout {{base_commit}}
go mod download
echo "[setup_repo] clean checkout @ {{base_commit}} ready"
```

### `verify_gold.sh` (run-time, self-check + restore)

```bash
#!/usr/bin/env bash
set -uo pipefail   # NOTE: not -e; we control flow to always restore

CASE_DIR=/opt/case
cd /testbed

restore() {
  git checkout -- . 2>/dev/null || true
  git clean -fd 2>/dev/null || true
}
trap restore EXIT   # guarantee restore even on early exit / failure

echo "[1] clean starting point"
restore

echo "[2] LF-normalize + apply gold patch + test_patch"
sed -i 's/\r$//' "$CASE_DIR/GOLD_patch.txt" "$CASE_DIR/GOLD_test_patch.txt"
git apply "$CASE_DIR/GOLD_patch.txt"
git apply "$CASE_DIR/GOLD_test_patch.txt"

echo "[3] run FAIL_TO_PASS (expect PASS)"
# test_command injected per language; for caddy this is:
#   go test ./caddytest/integration/... -run 'TestCaddyfileAdaptToJSON'
{{test_command}} 2>&1 | tail -40
TEST_RC=${PIPESTATUS[0]:-$?}
echo "[verify] test exit=$TEST_RC"

# [4] restore happens via trap regardless of TEST_RC
if [ "$TEST_RC" -ne 0 ]; then
  echo "[verify] GOLD self-check FAILED" >&2
  exit "$TEST_RC"
fi
echo "[verify] GOLD self-check passed; environment restored to clean base_commit; ready for agent"
```

### Restore semantics (key design points)

| Concern | Decision |
|---|---|
| Restore timing | `verify_gold.sh` `trap restore EXIT` — runs on every exit path |
| Restore thoroughness | `git checkout -- . && git clean -fd` (double guarantee incl. untracked) |
| Test failure vs build | Build never runs tests (cacheable); run-time failure exits non-zero, container preserved for debugging |
| Repeatability | Step [1] cleans before apply → `docker run` can be re-run to re-verify |
| `set -e` safety | Uses `set -uo pipefail` (no `-e`); explicit `trap` handles early exits |

## 6. Generator Implementation & Input Schema

### `gen_arm_case.py` structure

```python
import argparse, pathlib, io, json
import pandas as pd, yaml
from jinja2 import Environment, FileSystemLoader

SPECS = yaml.safe_load(open("arm_image_specs.yaml"))
env = Environment(loader=FileSystemLoader("templates"), keep_trailing_newline=True)

def detect_language(dataset_path, row):
    name = dataset_path.parent.name.lower()
    for lang in SPECS:
        if f"-{lang}" in name or f"smith-{lang}" in name:
            return lang
    return match_repo_to_lang(row["repo"])   # fallback

def load_cases(parquet_path):
    df = pd.read_parquet(parquet_path)
    for _, row in df.iterrows():
        yield row.to_dict()

def resolve_version(case, spec, lang):
    # case field if present, else None → setup script infers from repo file at clone
    for key, default in [("go_version","default_go_version"),
                         ("node_version","default_node_version")]:
        if case.get(key): return case[key]
    return spec.get(default)   # may be None → inference path

def render_case(case, lang, out_dir):
    spec = SPECS[lang]
    required = ["instance_id","repo","base_commit","patch","test_patch"]
    missing = [f for f in required if not case.get(f)]
    if missing:
        raise ValueError(f"case {case.get('instance_id','?')} missing {missing}")
    ctx = {
        "instance_id": case["instance_id"],
        "repo":        case["repo"],
        "base_commit": case["base_commit"],
        "patch":       case["patch"],
        "test_patch":  case["test_patch"],
        "fail_to_pass": case.get("FAIL_TO_PASS") or [],
        "go_version":   resolve_version(case, spec, "go"),
        "node_version": resolve_version(case, spec, "node"),
        # test_command built by per-lang template from spec + fail_to_pass
    }
    (out_dir/"Dockerfile").write_text(env.get_template(f"{lang}.Dockerfile.j2").render(**ctx))
    (out_dir/"setup_repo.sh").write_text(env.get_template(f"{lang}.setup.sh.j2").render(**ctx))
    (out_dir/"verify_gold.sh").write_text(env.get_template("common.verify_gold.sh.j2").render(**ctx))
    (out_dir/"GOLD_patch.txt").write_text(ctx["patch"])
    (out_dir/"GOLD_test_patch.txt").write_text(ctx["test_patch"])
    (out_dir/"FAIL_TO_PASS.txt").write_text("\n".join(ctx["fail_to_pass"]))
```

### Input schema (SWE-smith — two assumptions, see §6.1)

Generator depends on these fields (SWE-bench-compatible); all others ignored:

| Field | Use | Required |
|---|---|---|
| `instance_id` | dir name + image tag | yes |
| `repo` | clone URL (`github.com/{repo}`) + lang fallback | yes |
| `base_commit` | checkout commit | yes |
| `patch` | gold patch → `GOLD_patch.txt` | yes |
| `test_patch` | test patch → `GOLD_test_patch.txt` | yes |
| `FAIL_TO_PASS` | tests to run for self-check | yes (else fallback path) |
| `go_version`/`node_version`/... | toolchain version (injected) | optional → infer |

#### 6.1 Two schema assumptions (resolved with robust defaults; user can override)

1. **`FAIL_TO_PASS` presence.** Default: assume SWE-smith carries `FAIL_TO_PASS`. Fallback if absent: generator parses affected test files from `test_patch` and uses those as the self-check target. Both paths implemented; run-time auto-selects by field presence.
2. **Toolchain version source.** Default: infer from repo + `base_commit` (read `go.mod`/`package.json`/`CMakeLists.txt` after clone) — not hardcoded in generator. If a case explicitly carries a version field, it overrides the inference. This decouples from whether SWE-smith ships version fields.

### CLI

```bash
python gen_arm_case.py --dataset dataset_raw/swe-smith-go/ --instance caddyserver__caddy-6345   # one (debug)
python gen_arm_case.py --dataset dataset_raw/swe-smith-go/                                       # one language
python gen_arm_case.py --dataset dataset_raw/                                                   # all languages

# build + self-check on ARM64 server
cd cases/caddyserver__caddy-6345 && docker build -t swe-arm:caddy-6345 . && docker run --rm swe-arm:caddy-6345
```

### Error handling

- Missing required field → `ValueError` naming `instance_id` + missing field; **no silent skip**. All failures collected to `gen_errors.log` for one-shot triage.
- Language identification fails (dataset name + repo both unmatched) → error prompting user to add an `arm_image_specs.yaml` entry; no broken Dockerfile written.
- Jinja render failure → exception carries `instance_id`.

## 7. Testing & Verification Strategy

### Generator self-tests (Python, TDD, fake case data — no real datasets)

- `test_detect_language`: `swe-smith-go` → `go`; `swe-smith-js` → `js`; unknown → raises.
- `test_render_go_case`: fake Go case → Dockerfile contains `linux/arm64/v8`, `go version`, `go mod download`, `COPY verify_gold.sh`.
- `test_render_js_case`: contains `PUPPETEER_SKIP_CHROMIUM_DOWNLOAD`, `nvm install`.
- `test_missing_required_field`: missing `base_commit` → `ValueError` carrying `instance_id`.
- `test_version_fallback`: no version field → setup script reads `go.mod` (mock clone).
- `test_fail_to_pass_absent`: no `FAIL_TO_PASS` → verify_gold uses test_patch-parsed targets.

### End-to-end (ARM64 server, real local cases)

Two-batch closed-loop regression using the 5 local cases:

| Batch | Case | Verifies |
|---|---|---|
| Go | caddyserver__caddy-6345, gohugoio__hugo-12768 | arm64-native cleanest; expect pass |
| JS/TS | vuejs__core-11915, vuejs__core-11589, axios__axios-6539 | puppeteer Chrome-skip on arm |

Per-case acceptance (all four pass = case ready):

1. `docker build` succeeds (toolchain + deps + clone).
2. `docker run` → `verify_gold.sh` → all `FAIL_TO_PASS` **PASS**.
3. After run, `git status` in container is **clean** (no patch residue, no untracked).
4. Second `docker run` (idempotent restore) → still applies + PASS + restores.

### Known risks & handling

| Risk | Handling |
|---|---|
| JS cases need Chrome/puppeteer; Chrome on arm has historical issues | `PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true`; if a test truly needs a browser, mark case arm-unsupported → `arm_unsupported.log`, not silent failure |
| Version inference hits network failure reading repo file | clone-then-read failure → error with case id; no broken Dockerfile |
| Restore incomplete under `set -e` early exit | `trap restore EXIT` + `set -uo pipefail` (no `-e`) |
| Dataset schema != assumptions | generator schema validation rejects, lists in `gen_errors.log`; no half-baked Dockerfile |

## 8. Scope Boundaries

- **In scope:** per-case arm64 Dockerfile + setup_repo + verify_gold generation; gold self-check + restore; Go/JS/TS/CPP/Python/Ruby/PHP/Java/Rust language coverage via templates.
- **Out of scope (YAGNI):** harness base/env image layering (that was Approach A, rejected); agent/orchestrator integration (the `AGENTS.md` loop is separate); CI/registry publishing; building on x86 via QEMU (user is on native arm64).
- **Pilot:** generate `caddyserver__caddy-6345` first as a 试水 (water-test) before scaling.

## 9. Open Items (to confirm against real SWE-smith data)

- SWE-smith per-language dataset exact schema (field names, esp. `FAIL_TO_PASS` and version fields) — §6.1 defaults are robust to both answers.
- Whether any SWE-smith case carries pre-built image references we should reuse — assumed no; revisit if datasets include an `env_image` field.
