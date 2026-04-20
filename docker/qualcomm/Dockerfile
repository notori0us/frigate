# syntax=docker/dockerfile:1.6

# https://askubuntu.com/questions/972516/debian-frontend-environment-variable
ARG DEBIAN_FRONTEND=noninteractive

# Globally set pip break-system-packages option to avoid having to specify it every time
ARG PIP_BREAK_SYSTEM_PACKAGES=1

# ============================================================================
# !!! TEMPORARY — remove once upstream ships a cp311 Linux aarch64 wheel !!!
#
# qai_appbuilder publishes Linux aarch64 wheels for cp310, cp312, cp313 on
# PyPI but not cp311, which is what Frigate's Debian 12 base runs. Tracked
# upstream at https://github.com/quic/ai-engine-direct-helper/issues/91.
#
# Until that lands, we build the cp311 wheel from source inside this image.
# Building here (rather than pulling a pre-built wheel from a side repo) also
# guarantees the wheel's libstdc++/glibc ABI matches the runtime stage — a
# wheel compiled on a newer GCC fails at dlopen with
# `version GLIBCXX_3.4.32 not found`.
#
# Once the official cp311 wheel ships, replace the wheel-builder stage and the
# COPY + pip install below with:
#
#   RUN pip3 install --no-cache-dir "qai-appbuilder==<version>"
# ============================================================================

# Pinned to a known-working commit on quic/ai-engine-direct-helper.
ARG QAI_APPBUILDER_COMMIT=942bc0d
# Qualcomm AI Runtime Community Edition — free, no portal login.
ARG QAIRT_SDK_VERSION=2.38.0.250901
ARG QAIRT_SDK_URL=https://softwarecenter.qualcomm.com/api/download/software/sdks/Qualcomm_AI_Runtime_Community/All/${QAIRT_SDK_VERSION}/v${QAIRT_SDK_VERSION}.zip

FROM debian:12 AS wheel-builder
ARG DEBIAN_FRONTEND
ARG PIP_BREAK_SYSTEM_PACKAGES
ARG QAI_APPBUILDER_COMMIT
ARG QAIRT_SDK_VERSION
ARG QAIRT_SDK_URL

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ca-certificates git unzip wget \
        python3 python3-dev python3-pip python3-setuptools python3-wheel \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir pybind11

# Fetch source. Only the pybind11 submodule is needed for the build — the
# other submodules under samples/ are unused and some have broken refs on
# older commits.
RUN git clone https://github.com/quic/ai-engine-direct-helper.git /src
WORKDIR /src
RUN git checkout ${QAI_APPBUILDER_COMMIT} && git submodule update --init pybind/pybind11

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
FROM deps AS qualcomm-frigate
ARG DEBIAN_FRONTEND
ARG PIP_BREAK_SYSTEM_PACKAGES

# QAIRT runtime libs (libQnnHtp*.so etc.), the cDSP firmware, and the fastrpc
# user-space all live OUTSIDE this image — they are mounted in from the host
# at /opt/qairt and /usr/lib/dsp /usr/lib/rfsa. Users download the QAIRT
# Community Edition SDK directly from Qualcomm (free, no portal login). See
# docs/docs/frigate/installation.md for the full host setup recipe.
COPY --from=wheel-builder /src/dist/qai_appbuilder-*.whl /tmp/
RUN pip3 install --no-cache-dir /tmp/qai_appbuilder-*.whl && rm /tmp/qai_appbuilder-*.whl

WORKDIR /opt/frigate/
COPY --from=rootfs / /

# fastrpc separator gotcha: ADSP_LIBRARY_PATH is split by ';' (semicolon),
# not the usual ':'. libxdsprpc.so's apps_std_fopen_with_env uses ';' literally;
# every cDSP skel lookup fails silently if you write ':' here.
ENV ADSP_LIBRARY_PATH="/opt/qairt/hexagon-v68/unsigned;/usr/lib/dsp/cdsp;/usr/lib/rfsa/adsp" \
    LD_LIBRARY_PATH="/opt/qairt/lib"
