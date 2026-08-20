#include "scan.h"

uint64_t random_scan(const std::vector<uint8_t>& buf, uint64_t accesses, std::mt19937_64& rng) {
    uint64_t checksum = 0;
    const uint64_t size = buf.size();
    for (uint64_t i = 0; i < accesses; ++i) {
        const uint64_t idx = rng() % size;
        checksum += buf[idx];
    }
    return checksum;
}
