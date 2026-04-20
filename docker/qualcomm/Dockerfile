# syntax=docker/dockerfile:1.6

# https://askubuntu.com/questions/972516/debian-frontend-environment-variable
ARG DEBIAN_FRONTEND=noninteractive

# Globally set pip break-system-packages option to avoid having to specify it every time
ARG PIP_BREAK_SYSTEM_PACKAGES=1

# qai_appbuilder version. Pinned because the Python ABI between qai_appbuilder
# and the QAIRT runtime libraries it loads is locked per release; users must
# install a matching QAIRT SDK version on the host.
#
# As of writing, qai_appbuilder publishes Linux wheels for cp310, cp312, and
# cp313 — see https://github.com/quic/ai-engine-direct-helper/releases. The
# cp311 wheel (matching Frigate's Debian 12 / Python 3.11 base image) is
# requested upstream in https://github.com/quic/ai-engine-direct-helper/issues/91.
# Once that ships, this image builds cleanly with no extra steps.
ARG QAI_APPBUILDER_VERSION=2.38.0

FROM deps AS qualcomm-frigate
ARG DEBIAN_FRONTEND
ARG PIP_BREAK_SYSTEM_PACKAGES
ARG QAI_APPBUILDER_VERSION

# QAIRT runtime libs (libQnnHtp*.so etc.), the cDSP firmware, and the fastrpc
# user-space all live OUTSIDE this image — they are mounted in from the host
# at /opt/qairt and /usr/lib/dsp /usr/lib/rfsa. Users download the QAIRT
# Community Edition SDK directly from Qualcomm (free, no portal login):
#
#   https://softwarecenter.qualcomm.com/api/download/software/sdks/Qualcomm_AI_Runtime_Community/All/<version>/v<version>.zip
#
# See docs/docs/frigate/installation.md for the full host setup recipe.
RUN pip3 install --no-cache-dir "qai-appbuilder==${QAI_APPBUILDER_VERSION}"

WORKDIR /opt/frigate/
COPY --from=rootfs / /

# fastrpc separator gotcha: ADSP_LIBRARY_PATH is split by ';' (semicolon),
# not the usual ':'. libxdsprpc.so's apps_std_fopen_with_env uses ';' literally;
# every cDSP skel lookup fails silently if you write ':' here.
ENV ADSP_LIBRARY_PATH="/opt/qairt/hexagon-v68/unsigned;/usr/lib/dsp/cdsp;/usr/lib/rfsa/adsp" \
    LD_LIBRARY_PATH="/opt/qairt/lib"
