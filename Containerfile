# syntax=docker/dockerfile:1
#
# Task 18 — reproducible supported-Linux environment (ADR-005/006/007).
#
# Targets:
#   runtime — production application image. Physically excludes eval/,
#             tests/, fixtures/, examples/, and development documentation;
#             the REPLAY fallback fixtures and routes cannot exist in it.
#   ci      — complete repository for the full test suite and deterministic
#             REPLAY evaluation inside the supported environment.
#
# Reproducibility contract (Task 18 decisions D2/D4):
#   - The base image is pinned by immutable digest (python:3.13.14-slim-trixie).
#   - All apt packages install from the frozen Debian snapshot below with
#     exact pinned versions; no mutable live repository is consulted.
#   - uv is installed by exact version with a recorded wheel SHA-256.
#   - Python packages install from the committed uv.lock only (--frozen).
#   - CalculiX ccx 2.23 is built from the official hash-verified source
#     archives (dhondt.de + netlib SPOOLES) in a dedicated builder stage,
#     because the calculix-ccx package was removed from Debian trixie and
#     cross-release package mixing is not permitted. Only the stripped ccx
#     executable reaches the runtime/ci images; compilers and build tools do
#     not. CalculiX is GPL; SPOOLES is public domain per its documentation.

ARG BASE_IMAGE=python:3.13.14-slim-trixie@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

########################################################################
# base: frozen apt snapshot + exact native packages (gmsh closure + ccx
# runtime libraries)
########################################################################
FROM ${BASE_IMAGE} AS base

ARG DEBIAN_SNAPSHOT=20260720T000000Z

# First group: the measured shared-library closure of the locked gmsh 4.15.2
# wheel (ldd: libGL, libGLU, libX11, libXcursor, libXext, libXfixes, libXft,
# libXinerama, libXrender, libfontconfig, libgomp).
# Second group: the measured runtime closure of the source-built ccx 2.23
# (ldd: libarpack, liblapack, libblas, libgfortran).
# Versions are the exact candidates of the frozen snapshot above.
RUN set -eux; \
    printf '%s\n' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/" \
      'Suites: trixie trixie-updates' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      '' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/" \
      'Suites: trixie-security' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      > /etc/apt/sources.list.d/debian.sources; \
    printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "3";\n' \
      > /etc/apt/apt.conf.d/80snapshot; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      libgl1=1.7.0-1+b2 \
      libglu1-mesa=9.0.2-1.1+b3 \
      libx11-6=2:1.8.12-1 \
      libxcursor1=1:1.2.3-1 \
      libxext6=2:1.3.4-1+b3 \
      libxfixes3=1:6.0.0-2+b4 \
      libxft2=2.3.6-1+b4 \
      libxinerama1=2:1.1.4-3+b4 \
      libxrender1=1:0.9.12-1 \
      libfontconfig1=2.15.0-2.3 \
      libgomp1=14.2.0-19 \
      libarpack2t64=3.9.1-6 \
      liblapack3=3.12.1-6 \
      libblas3=3.12.1-6 \
      libgfortran5=14.2.0-19 \
      ; \
    dpkg-query -W -f '${Package}=${Version}\n' | sort > /opt/native-packages.txt; \
    rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_PREFERENCE=only-system \
    PATH=/opt/venv/bin:$PATH

########################################################################
# ccx-builder: CalculiX 2.23 from hash-verified official sources
########################################################################
FROM base AS ccx-builder

# Build tools come only into this stage, never into runtime/ci.
RUN set -eux; \
    printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "3";\n' \
      > /etc/apt/apt.conf.d/80snapshot; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      gcc=4:14.2.0-1 \
      gfortran=4:14.2.0-1 \
      make=4.4.1-2 \
      libarpack2-dev=3.9.1-6 \
      bzip2=1.0.8-6 \
      wget=1.25.0-2 \
      ca-certificates=20250419 \
      ; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
# Official immutable source archives with recorded SHA-256:
#   - CalculiX ccx 2.23 (GPL) from the author's site, dhondt.de.
#   - SPOOLES 2.2 (public domain) from netlib.org.
RUN set -eux; \
    wget -q https://www.dhondt.de/ccx_2.23.src.tar.bz2; \
    wget -q https://netlib.org/linalg/spooles/spooles.2.2.tgz; \
    echo "9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7  ccx_2.23.src.tar.bz2" | sha256sum -c -; \
    echo "a84559a0e987a1e423055ef4fdf3035d55b65bbe4bf915efaa1a35bef7f8c5dd  spooles.2.2.tgz" | sha256sum -c -

# SPOOLES is K&R-era C: gnu89 keeps GCC 14's implicit-declaration promotion
# a warning. The ccx C sources need GCC 14's newly promoted errors downgraded
# back to warnings, and the Fortran needs -fallow-argument-mismatch; the
# static ARPACK path is replaced by the trixie shared arpack/lapack/blas.
RUN set -eux; \
    mkdir SPOOLES.2.2; tar xzf spooles.2.2.tgz -C SPOOLES.2.2; \
    cd SPOOLES.2.2; \
    make lib CC="gcc -std=gnu89" OPTLEVEL="-O2"
RUN set -eux; \
    tar xjf ccx_2.23.src.tar.bz2; \
    cd CalculiX/ccx_2.23/src; \
    sed -i 's|\.\./\.\./\.\./ARPACK/libarpack_INTEL.a|-larpack -llapack -lblas|' Makefile; \
    sed -i 's|^CFLAGS = -Wall -O2|CFLAGS = -Wall -O2 -Wno-error=return-mismatch -Wno-error=implicit-function-declaration -Wno-error=implicit-int -Wno-error=int-conversion -Wno-error=incompatible-pointer-types|' Makefile; \
    sed -i 's|^FFLAGS = -Wall -O2 -cpp|FFLAGS = -Wall -O2 -cpp -fallow-argument-mismatch|' Makefile; \
    make -j"$(nproc)"; \
    install -m 0755 ccx_2.23 /usr/local/bin/ccx; \
    strip /usr/local/bin/ccx; \
    ccx -v | grep -q "Version 2.23"; \
    sha256sum /usr/local/bin/ccx

########################################################################
# builder: pinned uv + frozen locked runtime environment
########################################################################
FROM base AS builder

# uv installed by exact version with recorded wheel hash (linux x86_64).
RUN set -eux; \
    printf '%s\n' \
      'uv==0.11.32 \' \
      '  --hash=sha256:3da76cd4e2697de30928b8a8524bd39183ac1e08cb7e72833807c022b7cba6c4 \' \
      '  --hash=sha256:77f4356548ee8dc47efae154efd4e930c65570e7d4971c57bdef592f6eefb39c' \
      > /tmp/uv-pin.txt; \
    pip install --no-cache-dir --require-hashes -r /tmp/uv-pin.txt; \
    rm /tmp/uv-pin.txt

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-progress

########################################################################
# ci-builder: same, plus the locked dev dependency group
########################################################################
FROM builder AS ci-builder
RUN uv sync --frozen --no-progress

########################################################################
# runtime: production application image (no eval/tests/fixtures/examples)
########################################################################
FROM base AS runtime

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=ccx-builder /usr/local/bin/ccx /usr/local/bin/ccx
# Application runtime files and required static assets only. The eval/,
# tests/, fixtures/, and examples/ trees are deliberately absent: REPLAY
# fixtures cannot be loaded in this image regardless of configuration.
COPY app/ app/
COPY geom/ geom/
COPY ground/ ground/
COPY ir/ ir/
COPY llm/ llm/
COPY export/ export/
COPY scripts/check_env.py scripts/check_env.py

# Startup-fixed runtime mode (ADR-005): unset selects production.
# Loopback bind is the supported default; exposure is an operator decision.
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", "8000"]

########################################################################
# ci: full repository for the complete suite and REPLAY evaluation
########################################################################
FROM base AS ci

WORKDIR /app
COPY --from=ci-builder /opt/venv /opt/venv
COPY --from=ccx-builder /usr/local/bin/ccx /usr/local/bin/ccx
COPY . .
CMD ["python", "-m", "pytest", "tests", "-q"]
