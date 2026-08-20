// memory_bound reference demo: a taskflow graph of N workers, each performing
// LLC-missing random-access reads over its OWN buffer (>= 2-3x per-NUMA-LLC).
// Prints __MEASUREMENT_WINDOW_START__ when warmup ends so collect_reference.py
// aligns perf/devkit collection to the steady-state window only.
#include <taskflow/taskflow.hpp>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>
#include "scan.h"

int main(int argc, char** argv) {
    // config.json path is argv[1] (mirage runtime contract); the reference demo
    // also accepts overrides via env so it can run standalone on the ARM box.
    const unsigned cores = std::thread::hardware_concurrency();
    const unsigned n_workers = cores > 0 ? cores : 1;
    const uint64_t per_worker_mb =
        argc > 2 ? std::stoull(argv[2]) : 64;  // default 64MB (>= 2-3x a 16MB LLC)
    const uint64_t accesses = 50'000'000ULL;   // work-per-call, time-boxed below
    const int warmup_seconds = argc > 3 ? std::stoi(argv[3]) : 5;
    const int measurement_seconds = argc > 4 ? std::stoi(argv[4]) : 20;

    // First-touch: allocate per-worker buffers NOW (numactl has already bound
    // memory to the local node, so first-touch placement is correct).
    std::vector<std::vector<uint8_t>> buffers(n_workers);
    std::vector<uint64_t> sums(n_workers, 0);
    for (auto& b : buffers) {
        b.resize(per_worker_mb * 1024ULL * 1024ULL);
        std::fill(b.begin(), b.end(), 0xA5);
    }

    auto run_workers = [&](int seconds) {
        tf::Executor executor;
        tf::Taskflow tf;
        for (unsigned w = 0; w < n_workers; ++w) {
            tf.emplace([&, w] {
                std::mt19937_64 rng(0x600d + w);
                sums[w] += random_scan(buffers[w], accesses, rng);
            });
        }
        // Run for `seconds`: loop the graph until the window elapses.
        auto deadline = std::chrono::steady_clock::now()
                        + std::chrono::seconds(seconds);
        while (std::chrono::steady_clock::now() < deadline) {
            executor.run(tf).wait();
        }
    };

    run_workers(warmup_seconds);
    std::cout << "__MEASUREMENT_WINDOW_START__" << std::endl;
    run_workers(measurement_seconds);

    // Sink the sums so nothing is optimized out.
    uint64_t total = 0;
    for (auto s : sums) total += s;
    std::cerr << "checksum_total=" << total << std::endl;
    return 0;
}
