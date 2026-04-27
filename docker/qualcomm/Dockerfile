# syntax=docker/dockerfile:1.6

# https://askubuntu.com/questions/972516/debian-frontend-environment-variable
ARG DEBIAN_FRONTEND=noninteractive

# Globally set pip break-system-packages option to avoid having to specify it every time
ARG PIP_BREAK_SYSTEM_PACKAGES=1

# qai_appbuilder version. The Python ABI between qai_appbuilder and the QAIRT
# runtime libraries it loads is locked per release; users must install the
# matching QAIRT SDK version on the host (see installation.md).
ARG QAI_APPBUILDER_VERSION=2.40.0

FROM deps AS qualcomm-frigate
ARG DEBIAN_FRONTEND
ARG PIP_BREAK_SYSTEM_PACKAGES
ARG QAI_APPBUILDER_VERSION

# QAIRT runtime libs (libQnnHtp*.so etc.), the cDSP firmware, and the fastrpc
# user-space all live OUTSIDE this image — they are mounted in from the host
# at /opt/qairt and /usr/lib/dsp /usr/lib/rfsa. Users download the QAIRT
# Community Edition SDK directly from Qualcomm (free, no portal login). See
# docs/docs/frigate/installation.md for the full host setup recipe.
#
# pyyaml is imported eagerly by qai_appbuilder.onnxwrapper at module load.
RUN pip3 install --no-cache-dir \
        "qai-appbuilder==${QAI_APPBUILDER_VERSION}" \
        pyyaml

WORKDIR /opt/frigate/
COPY --from=rootfs / /

# fastrpc separator gotcha: ADSP_LIBRARY_PATH is split by ';' (semicolon),
# not the usual ':'. libxdsprpc.so's apps_std_fopen_with_env uses ';' literally;
# every cDSP skel lookup fails silently if you write ':' here.
ENV ADSP_LIBRARY_PATH="/opt/qairt/hexagon-v68/unsigned;/usr/lib/dsp/cdsp;/usr/lib/rfsa/adsp" \
    LD_LIBRARY_PATH="/opt/qairt/lib"
