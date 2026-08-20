#pragma once
// Self-developed dense matmul kernel (blocked/unrolled). FP MAC work -> retiring-bound.
#include <cstdint>
#include <vector>

// C = A * B  (K x K), accumulate into C. Return a checksum of C for sink.
double matmul_checksum(const std::vector<double>& A,
                       const std::vector<double>& B,
                       int K);
