cmake_minimum_required(VERSION 3.16)
project(compute_bound_ref CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(compute_bound_ref main.cpp matmul.cpp)
target_compile_options(compute_bound_ref PRIVATE -O2 -march=armv8.2-a)
target_include_directories(compute_bound_ref PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../../third_party/taskflow)
