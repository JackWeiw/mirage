// compute_bound reference demo: a taskflow graph of N workers, each running dense
// matmul repeatedly for the measurement window. retiring-dominated Topdown ~55-65.
#include <taskflow/taskflow.hpp>
#include <chrono>
#include <iostream>
#include <random>
#include <thread>
#include <vector>
#include "matmul.h"

int main(int argc, char** argv) {
    const unsigned n_workers = std::thread::hardware_concurrency() ?: 1;
    const int K = argc > 2 ? std::stoi(argv[2]) : 256;
    const int warmup_seconds = argc > 3 ? std::stoi(argv[3]) : 5;
    const int measurement_seconds = argc > 4 ? std::stoi(argv[4]) : 20;

    std::vector<double> sums(n_workers, 0.0);
    auto run_workers = [&](int seconds) {
        tf::Executor executor;
        tf::Taskflow tf;
        for (unsigned w = 0; w < n_workers; ++w) {
            tf.emplace([&, w] {
                std::mt19937_64 rng(0x600d + w);
                std::vector<double> A(static_cast<size_t>(K) * K), B(static_cast<size_t>(K) * K);
                for (auto& v : A) v = static_cast<double>(rng()) / rng.max();
                for (auto& v : B) v = static_cast<double>(rng()) / rng.max();
                double local = 0.0;
                auto deadline = std::chrono::steady_clock::now()
                                + std::chrono::seconds(seconds);
                while (std::chrono::steady_clock::now() < deadline) {
                    local += matmul_checksum(A, B, K);
                }
                sums[w] = local;
            });
        }
        executor.run(tf).wait();
    };

    run_workers(warmup_seconds);
    std::cout << "__MEASUREMENT_WINDOW_START__" << std::endl;
    run_workers(measurement_seconds);

    double total = 0.0;
    for (double s : sums) total += s;
    std::cerr << "checksum_total=" << total << std::endl;
    return 0;
}
