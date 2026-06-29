# syntax=docker/dockerfile:1.6

# https://askubuntu.com/questions/972516/debian-frontend-environment-variable
ARG DEBIAN_FRONTEND=noninteractive

# Globally set pip break-system-packages option to avoid having to specify it every time
ARG PIP_BREAK_SYSTEM_PACKAGES=1

# ============================================================================
# !!! TEMPORARY: remove once upstream ships a cp311 Linux aarch64 wheel !!!
#
# qai_appbuilder publishes Linux aarch64 wheels for cp310, cp312, cp313 on
# PyPI but not cp311, which is what Frigate's Debian 12 base runs. Tracked
# upstream at https://github.com/quic/ai-engine-direct-helper/issues/91.
#
# Until that lands, we build the cp311 wheel from source inside this image.
# Building here (rather than pulling a pre-built wheel from a side repo) also
# guarantees the wheel's libstdc++/glibc ABI matches the runtime stage. A
# wheel compiled on a newer GCC fails at dlopen with
# `version GLIBCXX_3.4.32 not found`.
#
# Once the official cp311 wheel ships, replace the wheel-builder stage and the
# COPY + pip install below with:
#
#   RUN pip3 install --no-cache-dir "qai-appbuilder==<version>"
# ============================================================================

# Pinned to upstream release tag v2.37.1 (commit 942bc0d), known-working on QCS6490.
ARG QAI_APPBUILDER_REF=v2.37.1
# Qualcomm AI Runtime Community Edition (free, no portal login).
ARG QAIRT_SDK_VERSION=2.45.40.260406
ARG QAIRT_SDK_URL=https://softwarecenter.qualcomm.com/api/download/software/sdks/Qualcomm_AI_Runtime_Community/All/${QAIRT_SDK_VERSION}/v${QAIRT_SDK_VERSION}.zip

# libcdsprpc.so (FastRPC user-space; qualcomm/fastrpc, BSD-3-Clause). Bundled
# so users no longer bind-mount the host libcdsprpc.so* paths. Pulled from the
# radxa 'libcdsprpc1' .deb (the actual binary), NOT 'fastrpc' (daemons only).
# Full rationale at the libcdsprpc build stage below.
ARG LIBCDSPRPC_DEB_URL=https://github.com/radxa-pkg/fastrpc/releases/download/1.0.4-1/libcdsprpc1_1.0.4-1_arm64.deb

FROM debian:12 AS wheel-builder
ARG DEBIAN_FRONTEND
ARG PIP_BREAK_SYSTEM_PACKAGES
ARG QAI_APPBUILDER_REF
ARG QAIRT_SDK_VERSION
ARG QAIRT_SDK_URL

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ca-certificates git unzip wget \
        python3 python3-dev python3-pip python3-setuptools python3-wheel \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir pybind11

# Fetch source. Only the pybind11 submodule is needed for the build; the
# other submodules under samples/ are unused and some have broken refs on
# older commits.
RUN git clone https://github.com/quic/ai-engine-direct-helper.git /src
WORKDIR /src
RUN git checkout ${QAI_APPBUILDER_REF} && git submodule update --init pybind/pybind11

# Fetch QAIRT SDK and extract only the headers + libGenie.so we need
# (9 MB vs 1.4 GB for the full SDK).
ADD ${QAIRT_SDK_URL} /tmp/qairt.zip
RUN unzip -q /tmp/qairt.zip \
        "qairt/${QAIRT_SDK_VERSION}/include/QNN/*" \
        "qairt/${QAIRT_SDK_VERSION}/include/Genie/*" \
        "qairt/${QAIRT_SDK_VERSION}/lib/aarch64-oe-linux-gcc11.2/libGenie.so" \
        -d /opt \
    && mv "/opt/qairt/${QAIRT_SDK_VERSION}" /opt/qairt-sdk \
    && rm /tmp/qairt.zip && rmdir /opt/qairt

ENV QNN_SDK_ROOT=/opt/qairt-sdk

# setup.py runs cmake twice: once for libappbuilder.so, once for the pybind11
# module. Both pick up QNN_SDK_ROOT from env. Output: /src/dist/*.whl.
RUN python3 setup.py bdist_wheel

# =============================================================================
# libcdsprpc.so: FastRPC user-space, the CPU<->cDSP bridge half that runs
# inside the container. Bundling it here means users no longer have to
# bind-mount the three host /usr/lib/libcdsprpc.so* paths.
#
# The radxa 'libcdsprpc1' .deb ships the real binary (libcdsprpc.so.1.0.0) plus
# the libcdsprpc.so.1 SONAME symlink, but NOT the unversioned libcdsprpc.so
# dev symlink (that one lives in the separate 'fastrpc' .deb). We recreate the
# unversioned symlink in the final stage so all three legacy mount paths exist
# in-image. The binary links libyaml-0.so.2 and libbsd.so.0, both installed
# in the final stage.
FROM debian:12 AS libcdsprpc
ARG LIBCDSPRPC_DEB_URL
# BuildKit's ADD fetches the URL over TLS without needing ca-certificates in
# the image; dpkg-deb is part of the debian:12 base.
ADD ${LIBCDSPRPC_DEB_URL} /tmp/libcdsprpc1.deb
RUN dpkg-deb -x /tmp/libcdsprpc1.deb /tmp/cdsprpc \
    && test -f /tmp/cdsprpc/usr/lib/libcdsprpc.so.1.0.0 \
    && rm /tmp/libcdsprpc1.deb

# =============================================================================
FROM deps AS qualcomm-frigate
ARG DEBIAN_FRONTEND
ARG PIP_BREAK_SYSTEM_PACKAGES
ARG QAIRT_SDK_VERSION

# Runtime deps NOT in Frigate's base image:
#   - libatomic1: qai_appbuilder dlopens libatomic.so.1 during QNN system init.
#     If missing, the detector silently fails to initialize the QNN system
#     function pointers and `Inference()` returns an empty list at runtime.
#   - libyaml-0-2 + libbsd0: the bundled libcdsprpc.so links libyaml-0.so.2
#     (parses the fastrpc shell config) and libbsd.so.0. Without them the
#     library fails to dlopen and the cDSP bridge never comes up.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 libyaml-0-2 libbsd0 \
    && rm -rf /var/lib/apt/lists/*

# QAIRT runtime libs (libQnnHtp*.so etc.) and the cDSP firmware live OUTSIDE
# this image and are mounted in from the host at /opt/qairt, /usr/lib/dsp, and
# /usr/lib/rfsa. The fastrpc user-space (libcdsprpc.so*) IS bundled in this
# image (see the COPY below). Users download the QAIRT Community Edition SDK
# from Qualcomm (free, no portal login). See
# docs/docs/frigate/installation.md for the full setup.
COPY --from=wheel-builder /src/dist/qai_appbuilder-*.whl /tmp/
# Install the wheel, then strip the proprietary Genie runtime (libGenie.so + the
# geniebuilder/geniecontext bindings, all unused by detection) so the published
# image carries no Qualcomm proprietary binaries. Locate the package without
# importing it (import pulls Genie, which we're removing).
RUN pip3 install --no-cache-dir /tmp/qai_appbuilder-*.whl && rm /tmp/qai_appbuilder-*.whl \
    && QAID="$(python3 -c 'import importlib.util, os; print(os.path.dirname(importlib.util.find_spec("qai_appbuilder").origin))')" \
    && rm -f "$QAID"/libGenie.so "$QAID"/geniebuilder*.so "$QAID"/geniecontext.py \
    && sed -i '/[Gg]enie/d' "$QAID/__init__.py" \
    && python3 -c "import qai_appbuilder; from qai_appbuilder import QNNContext"

WORKDIR /opt/frigate/
COPY --from=rootfs / /

# Bundle libcdsprpc.so* (FastRPC user-space; upstream qualcomm/fastrpc,
# BSD-3-Clause) extracted from the radxa 'libcdsprpc1' .deb in the libcdsprpc
# stage. Copy the real binary, then recreate the .so.1 SONAME and the
# unversioned .so dev symlink so all three legacy host-mount paths resolve
# in-image. Done after the rootfs overlay so nothing clobbers it; ldconfig
# refreshes the linker cache.
COPY --from=libcdsprpc /tmp/cdsprpc/usr/lib/libcdsprpc.so.1.0.0 /usr/lib/libcdsprpc.so.1.0.0
RUN ln -sf libcdsprpc.so.1.0.0 /usr/lib/libcdsprpc.so.1 \
    && ln -sf libcdsprpc.so.1.0.0 /usr/lib/libcdsprpc.so \
    && ldconfig

# Ship libcdsprpc's BSD-3-Clause license/attribution with the bundled binary
# (the radxa deb carries only its GPL-3 packaging copyright, not the BSD-3 text).
COPY docker/qualcomm/licenses/libcdsprpc.LICENSE /usr/share/doc/libcdsprpc/copyright

# fastrpc separator gotcha: ADSP_LIBRARY_PATH is split by ';' (semicolon),
# not the usual ':'. libxdsprpc.so's apps_std_fopen_with_env uses ';' literally;
# every cDSP skel lookup fails silently if you write ':' here.
ENV ADSP_LIBRARY_PATH="/opt/qairt/hexagon-v68/unsigned;/usr/lib/dsp/cdsp;/usr/lib/rfsa/adsp" \
    LD_LIBRARY_PATH="/opt/qairt/lib" \
    FRIGATE_QNN_BUILD_QAIRT_VERSION="${QAIRT_SDK_VERSION}"
