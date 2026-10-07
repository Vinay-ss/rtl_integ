#!/bin/bash
# Build a pyslang wheel for glibc 2.17 (manylinux2014) from PyPI's sdist, so
# the Linux bundle runs on CentOS/RHEL 7 and later (PyPI's own Linux wheels
# need glibc 2.28).  Runs inside the manylinux2014 image:
#
#   docker run --rm -v "$PWD:/io" quay.io/pypa/manylinux2014_x86_64 \
#       bash /io/tools/bundle/build_pyslang_wheel.sh /io/wheelhouse
#
# The sdist URL and sha256 come from versions.toml.  slang needs a C++20
# compiler (GCC 11+); the image ships an older devtoolset, so a newer one is
# installed from the CentOS SCL repositories first.
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

if [ "$(gcc -dumpversion | cut -d. -f1)" -lt 11 ]; then
    for v in 13 12 11; do
        if yum install -y -q "devtoolset-$v-gcc-c++" >/dev/null 2>&1; then
            # shellcheck disable=SC1090
            source "/opt/rh/devtoolset-$v/enable"
            break
        fi
    done
fi
major=$(gcc -dumpversion | cut -d. -f1)
if [ "$major" -lt 11 ]; then
    echo "build_pyslang_wheel: slang needs GCC 11 or newer, found $(gcc -dumpversion)" >&2
    exit 1
fi
echo "compiler: $(gcc --version | head -1)"

work=$(mktemp -d)
curl -fsSL "$url" -o "$work/pyslang.tar.gz"
echo "$sum  $work/pyslang.tar.gz" | sha256sum -c -
CMAKE_BUILD_PARALLEL_LEVEL=$(nproc) "$py" -m pip wheel --no-deps -w "$work/raw" "$work/pyslang.tar.gz"
auditwheel repair --plat manylinux2014_x86_64 -w "$out" "$work"/raw/pyslang-*.whl
auditwheel show "$out"/pyslang-*manylinux2014_x86_64*.whl
rm -rf "$work"
