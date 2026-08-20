#pragma once
// Self-developed LLC-missing random-access scan. Each worker owns a private buffer
// sized >= 2-3x the per-NUMA-node LLC; random access into it defeats the cache.
#include <cstdint>
#include <random>
#include <vector>

// Random-access `accesses` reads over `buf`; returns a checksum so the optimizer
// cannot elide the loads. `rng` is per-worker (no shared state).
uint64_t random_scan(const std::vector<uint8_t>& buf, uint64_t accesses, std::mt19937_64& rng);
