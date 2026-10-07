#!/bin/bash
# Build a pyslang wheel for glibc 2.17 (manylinux2014) from PyPI's sdist, so
# the Linux bundle runs on CentOS/RHEL 7 and later (PyPI's own Linux wheels
# need glibc 2.28).  Runs inside the manylinux2014 image:
#
#   docker run --rm -v "$PWD:/io" quay.io/pypa/manylinux2014_x86_64 \
#       bash /io/tools/bundle/build_pyslang_wheel.sh /io/wheelhouse
#
# The sdist URL and sha256 come from versions.toml.  slang needs a C++20
# compiler (GCC 11+); the image ships an older devtoolset, so devtoolset-11
# (the newest in the CentOS 7 SCL repositories) is installed first, and
# pyslang_gcc11_compat.h supplies the one library piece GCC 11 lacks.
set -euo pipefail

out=${1:-wheelhouse}
mkdir -p "$out"
out=$(cd "$out" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
py=/opt/python/cp312-cp312/bin/python

read -r url sum < <("$py" - "$here/versions.toml" <<'EOF'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    s = tomllib.load(fh)["pyslang"]["sdist"]
print(s["url"], s["sha256"])
EOF
)

echo "image compiler: $(gcc --version | head -1)"
if [ "$(gcc -dumpversion | cut -d. -f1)" -lt 11 ]; then
    yum install -y -q devtoolset-11-gcc-c++ 2>&1 | grep -v -e '^install-info' -e SELinux || true
    # shellcheck disable=SC1091
    [ -f /opt/rh/devtoolset-11/enable ] && source /opt/rh/devtoolset-11/enable
fi
major=$(gcc -dumpversion | cut -d. -f1)
if [ "$major" -lt 11 ]; then
    echo "build_pyslang_wheel: slang needs GCC 11 or newer, found $(gcc -dumpversion)" >&2
    exit 1
fi
if [ "$major" -lt 12 ]; then
    export CXXFLAGS="${CXXFLAGS:-} -include $here/pyslang_gcc11_compat.h"
fi
echo "compiler: $(gcc --version | head -1)  CXXFLAGS=${CXXFLAGS:-}"

work=$(mktemp -d)
curl -fsSL "$url" -o "$work/pyslang.tar.gz"
echo "$sum  $work/pyslang.tar.gz" | sha256sum -c -
CMAKE_BUILD_PARALLEL_LEVEL=$(nproc) "$py" -m pip wheel --no-deps -w "$work/raw" "$work/pyslang.tar.gz"
auditwheel repair --plat manylinux2014_x86_64 -w "$out" "$work"/raw/pyslang-*.whl
auditwheel show "$out"/pyslang-*manylinux2014_x86_64*.whl
rm -rf "$work"
