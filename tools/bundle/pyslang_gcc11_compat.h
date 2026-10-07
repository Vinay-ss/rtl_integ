// Force-included (-include) when building pyslang with GCC 11 for glibc 2.17
// (build_pyslang_wheel.sh): slang hashes std::filesystem::path, whose
// std::hash specialization libstdc++ only provides from GCC 12 (LWG 3657).
// This adds the same specialization for older libstdc++.
#pragma once

#if defined(__cplusplus) && __cplusplus >= 201703L
#    include <filesystem>
#    include <functional>

#    if defined(_GLIBCXX_RELEASE) && _GLIBCXX_RELEASE < 12
namespace std {
template<>
struct hash<filesystem::path> {
    size_t operator()(const filesystem::path& p) const noexcept { return filesystem::hash_value(p); }
};
} // namespace std
#    endif
#endif
