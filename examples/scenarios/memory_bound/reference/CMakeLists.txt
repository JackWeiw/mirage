cmake_minimum_required(VERSION 3.16)
project(memory_bound_ref CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(memory_bound_ref main.cpp scan.cpp)
target_compile_options(memory_bound_ref PRIVATE -O2 -march=armv8.2-a -fno-inline-small-functions)
# include dir -> examples/third_party/taskflow  so <taskflow/taskflow.hpp> resolves
target_include_directories(memory_bound_ref PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../../third_party/taskflow)
