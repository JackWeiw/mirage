#include "matmul.h"
#include <cmath>

double matmul_checksum(const std::vector<double>& A,
                      const std::vector<double>& B,
                      int K) {
    std::vector<double> C(static_cast<size_t>(K) * K, 0.0);
    const int BS = 64;  // block size
    for (int ii = 0; ii < K; ii += BS) {
        for (int jj = 0; jj < K; jj += BS) {
            for (int kk = 0; kk < K; kk += BS) {
                int i_end = std::min(ii + BS, K);
                int j_end = std::min(jj + BS, K);
                int k_end = std::min(kk + BS, K);
                for (int i = ii; i < i_end; ++i) {
                    for (int j = jj; j < j_end; ++j) {
                        double acc = C[i * K + j];
                        for (int k = kk; k < k_end; ++k) {
                            acc += A[i * K + k] * B[k * K + j];
                        }
                        C[i * K + j] = acc;
                    }
                }
            }
        }
    }
    double checksum = 0.0;
    for (double v : C) checksum += v;
    return checksum;
}
