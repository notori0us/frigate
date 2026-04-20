# syntax=docker/dockerfile:1.6

# https://askubuntu.com/questions/972516/debian-frontend-environment-variable
ARG DEBIAN_FRONTEND=noninteractive

# Globally set pip break-system-packages option to avoid having to specify it every time
ARG PIP_BREAK_SYSTEM_PACKAGES=1

# ============================================================================
# !!! TEMPORARY — DO NOT MERGE UPSTREAM !!!
#
# qai_appbuilder publishes Linux aarch64 wheels for cp310, cp312, and cp313 on
# PyPI, but not cp311 — the version Frigate's Debian 12 base image runs.
# Tracked upstream at https://github.com/quic/ai-engine-direct-helper/issues/91.
#
# As a stopgap so this fork can build + run, we install a locally-rebuilt cp311
# wheel from a personal release. It's the same BSD-3 source as upstream, just
# compiled on a Radxa Dragon Q6A against QAIRT 2.37.1. Wheel + license details:
#   https://github.com/notori0us/qai-appbuilder-wheel
#
# Once issue #91 ships an official cp311 wheel on PyPI, replace the WHEEL_URL
# block below with:
#   RUN pip3 install --no-cache-dir "qai-appbuilder==<version>"
# and delete this comment block.
# ============================================================================
ARG QAI_APPBUILDER_WHEEL_URL=https://github.com/notori0us/qai-appbuilder-wheel/releases/download/v2.37.1.68-frigate-fork-test/qai_appbuilder-2.37.1.68-cp311-cp311-linux_aarch64.whl

FROM deps AS qualcomm-frigate
ARG DEBIAN_FRONTEND
ARG PIP_BREAK_SYSTEM_PACKAGES
ARG QAI_APPBUILDER_WHEEL_URL

# QAIRT runtime libs (libQnnHtp*.so etc.), the cDSP firmware, and the fastrpc
# user-space all live OUTSIDE this image — they are mounted in from the host
# at /opt/qairt and /usr/lib/dsp /usr/lib/rfsa. Users download the QAIRT
# Community Edition SDK directly from Qualcomm (free, no portal login):
#
#   https://softwarecenter.qualcomm.com/api/download/software/sdks/Qualcomm_AI_Runtime_Community/All/<version>/v<version>.zip
#
# See docs/docs/frigate/installation.md for the full host setup recipe.
ADD --chmod=644 ${QAI_APPBUILDER_WHEEL_URL} /tmp/qai_appbuilder.whl
RUN pip3 install --no-cache-dir /tmp/qai_appbuilder.whl && rm /tmp/qai_appbuilder.whl

WORKDIR /opt/frigate/
COPY --from=rootfs / /

# fastrpc separator gotcha: ADSP_LIBRARY_PATH is split by ';' (semicolon),
# not the usual ':'. libxdsprpc.so's apps_std_fopen_with_env uses ';' literally;
# every cDSP skel lookup fails silently if you write ':' here.
ENV ADSP_LIBRARY_PATH="/opt/qairt/hexagon-v68/unsigned;/usr/lib/dsp/cdsp;/usr/lib/rfsa/adsp" \
    LD_LIBRARY_PATH="/opt/qairt/lib"
