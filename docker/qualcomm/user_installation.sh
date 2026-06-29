#!/bin/bash
#
# Host-side setup for the Qualcomm Hexagon NPU detector on a Radxa Dragon Q6A
# (or other QCS6490 board). Installs:
#   - fastrpc user-space (libcdsprpc.so, cdsprpcd, fastrpc_test)
#   - Radxa firmware that ships the cDSP image + skel libs the QNN HTP
#     backend dlopens at runtime
#   - a transient cdsprpcd systemd service
#   - (optionally, with --fetch-qairt) the QAIRT Community SDK runtime libs
# and disables hexagonrpcd, which holds the fastrpc devices and conflicts.
#
# Run with sudo. Logs out + back in are required for the fastrpc group to
# take effect for your user.
#
# Usage:
#   sudo ./user_installation.sh [--fetch-qairt] [--accept-qairt-license]
#
#   --fetch-qairt             Also download + unpack the QAIRT Community SDK
#                             to /opt/qcom/qairt/<version> (otherwise you must
#                             download it manually — see installation.md Step 2).
#   --accept-qairt-license    Pre-accept Qualcomm's SDK license non-interactively
#                             (implies you have read and agree to the terms).

set -euo pipefail

# --- QAIRT version -----------------------------------------------------------
# MUST match FRIGATE_QNN_BUILD_QAIRT_VERSION baked into the Frigate -qualcomm
# image (docker/qualcomm/Dockerfile -> ARG QAIRT_SDK_VERSION). The QNN binary
# ABI is locked per release; a mismatch makes Inference() silently return an
# empty list at runtime. Bump this in lockstep with the Dockerfile.
QAIRT_VERSION=2.45.40.260406
QAIRT_BASE=/opt/qcom/qairt
QAIRT_LIBDIR="${QAIRT_BASE}/${QAIRT_VERSION}/lib/aarch64-oe-linux-gcc11.2"
QAIRT_URL="https://softwarecenter.qualcomm.com/api/download/software/sdks/Qualcomm_AI_Runtime_Community/All/${QAIRT_VERSION}/v${QAIRT_VERSION}.zip"
# Qualcomm presents the SDK license at the Software Center download page below;
# the same terms ship as LICENSE.pdf inside the extracted SDK.
QAIRT_LICENSE_URL="https://softwarecenter.qualcomm.com/#/catalog/item/Qualcomm_AI_Runtime_Community"

# --- argument parsing --------------------------------------------------------
FETCH_QAIRT=0
ACCEPT_QAIRT_LICENSE=0
for arg in "$@"; do
    case "$arg" in
        --fetch-qairt)           FETCH_QAIRT=1 ;;
        --accept-qairt-license)  ACCEPT_QAIRT_LICENSE=1 ;;
        -h|--help)
            grep -E '^#( |$)' "$0" | sed -E 's/^# ?//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Usage: sudo $0 [--fetch-qairt] [--accept-qairt-license]" >&2
            exit 1
            ;;
    esac
done

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo $0)"
    exit 1
fi

ARCH=$(dpkg --print-architecture)
if [ "$ARCH" != "arm64" ]; then
    echo "This script targets arm64 (QCS6490). Detected: $ARCH"
    exit 1
fi

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl unzip

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# fastrpc user-space (provides libcdsprpc.so + cdsprpcd). Not in apt.
# NB: the diagnostic package is 'fastrpc-test' (ships fastrpc_test); there is no
# 'fastrpc-tools' release asset — using that name 404s and aborts the install.
FASTRPC_VER=1.0.4-1
echo "==> Installing fastrpc ${FASTRPC_VER}"
for pkg in fastrpc fastrpc-test; do
    curl -fsSL -o "${WORKDIR}/${pkg}.deb" \
        "https://github.com/radxa-pkg/fastrpc/releases/download/${FASTRPC_VER}/${pkg}_${FASTRPC_VER}_arm64.deb"
done
apt-get install -y "${WORKDIR}/fastrpc.deb" "${WORKDIR}/fastrpc-test.deb"

# Radxa QCS6490 firmware (provides /usr/lib/dsp/cdsp/{cdsp.mbn,*_skel.so,...}
# and /usr/lib/rfsa/adsp/, both required by the cDSP at runtime). Many QCS6490
# BSP/vendor images already ship this firmware (owned by no dpkg package), in
# which case the package is unnecessary — so skip it when the dirs are present.
# We also tolerate an install failure (e.g. the radxa-firmware-qcs6490 dependency
# firmware-qcom-hlosfw not being available from your configured apt sources)
# rather than aborting before the prerequisite summary, which reports whether the
# firmware actually ended up present.
RADXA_FW_VER=0.2.29
if [ -d /usr/lib/dsp/cdsp ] && [ -d /usr/lib/rfsa/adsp ]; then
    echo "==> cDSP firmware already present (/usr/lib/dsp/cdsp + /usr/lib/rfsa/adsp) — skipping radxa-firmware-qcs6490"
else
    echo "==> Installing radxa-firmware-qcs6490 ${RADXA_FW_VER}"
    if curl -fsSL -o "${WORKDIR}/radxa-firmware-qcs6490.deb" \
            "https://github.com/radxa-pkg/radxa-firmware/releases/download/${RADXA_FW_VER}/radxa-firmware-qcs6490_${RADXA_FW_VER}_all.deb" \
       && apt-get install -y "${WORKDIR}/radxa-firmware-qcs6490.deb"; then
        :
    else
        echo "    ⚠️  radxa-firmware-qcs6490 install failed — its dependency firmware-qcom-hlosfw"
        echo "        may not be available from your configured apt sources. If your board's BSP"
        echo "        image already provides the cDSP firmware this is harmless; the summary below"
        echo "        confirms whether /usr/lib/dsp + /usr/lib/rfsa are present. If they are NOT,"
        echo "        install your board vendor's cDSP firmware package and re-run."
    fi
fi

# hexagonrpcd from the apt 'hexagonrpcd' package conflicts with cdsprpcd by
# holding /dev/fastrpc-* exclusively. We need cdsprpcd for QNN HTP.
echo "==> Disabling conflicting hexagonrpcd services"
for unit in hexagonrpcd hexagonrpcd-suspend hexagonrpcd-resume; do
    systemctl disable --now "${unit}" 2>/dev/null || true
done

echo "==> Enabling cdsprpcd"
cat >/etc/systemd/system/cdsprpcd.service <<'UNIT'
[Unit]
Description=Qualcomm cDSP FastRPC daemon
After=local-fs.target

[Service]
Type=simple
ExecStart=/usr/bin/cdsprpcd
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cdsprpcd

# cDSP recovery service: when a process dies mid-inference (Frigate watchdog
# kills its detector subprocess, container restart, etc.) the cDSP can be
# left in a stuck state where new sessions either time out or return empty
# results. The fix is to bounce the remoteproc, which is cheap (<5s) and
# safe to do at boot. Running this once on host boot clears any state from
# a previous boot's crash.
echo "==> Installing cDSP boot-time reset hook"
cat >/etc/systemd/system/cdsp-reset.service <<'UNIT'
[Unit]
Description=Reset Qualcomm cDSP remoteproc at boot for clean inference state
After=local-fs.target
Before=cdsprpcd.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c '\
  echo stop > /sys/class/remoteproc/remoteproc1/state; \
  sleep 2; \
  echo start > /sys/class/remoteproc/remoteproc1/state'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable cdsp-reset.service

# Allow non-root containers + users to open /dev/fastrpc-*.
echo "==> Adding invoking user to fastrpc group"
TARGET_USER="${SUDO_USER:-$USER}"
if [ -n "${TARGET_USER}" ] && id "${TARGET_USER}" >/dev/null 2>&1; then
    usermod -aG fastrpc "${TARGET_USER}"
fi

# --- QAIRT Community SDK (optional, --fetch-qairt) ---------------------------
# USER-RUN ONLY. This must never be invoked from inside a container: the human
# operator is the one accepting Qualcomm's license terms, which keeps the legal
# agency with the person, not the image. Idempotent and gated behind explicit
# consent.
fetch_qairt() {
    echo "==> QAIRT Community SDK ${QAIRT_VERSION}"

    # Idempotency: if the runtime lib dir already exists, there is nothing to do.
    if [ -d "${QAIRT_LIBDIR}" ]; then
        echo "    Already present at ${QAIRT_LIBDIR} — skipping download."
        return 0
    fi

    # Explicit consent gate. Either --accept-qairt-license was passed, or the
    # user must type 'accept' at the prompt. Anything else refuses the download.
    if [ "${ACCEPT_QAIRT_LICENSE}" -ne 1 ]; then
        echo
        echo "    The QAIRT Community SDK is proprietary Qualcomm software."
        echo "    By downloading it you agree to Qualcomm's license terms:"
        echo "      ${QAIRT_LICENSE_URL}"
        echo "    (the same terms ship as LICENSE.pdf inside the SDK)."
        echo
        if [ ! -t 0 ]; then
            echo "    No TTY and --accept-qairt-license not passed; refusing to download." >&2
            echo "    Re-run with --accept-qairt-license to consent non-interactively." >&2
            return 1
        fi
        printf "    Type 'accept' to agree and download, anything else to skip: "
        read -r reply
        if [ "${reply}" != "accept" ]; then
            echo "    License not accepted — skipping QAIRT download."
            return 1
        fi
    else
        echo "    License pre-accepted via --accept-qairt-license."
    fi

    echo "    Downloading (~1.4 GB) from ${QAIRT_URL}"
    # softwarecenter.qualcomm.com 403s requests without a browser UA.
    curl -fSL -A 'Mozilla/5.0' -o "${WORKDIR}/qairt.zip" "${QAIRT_URL}"

    echo "    Unpacking to ${QAIRT_BASE%/qairt}/"
    mkdir -p "${QAIRT_BASE%/qairt}"
    # The zip already contains qairt/<version>/..., so extract into /opt/qcom/.
    unzip -q -o "${WORKDIR}/qairt.zip" -d "${QAIRT_BASE%/qairt}/"
    rm -f "${WORKDIR}/qairt.zip"

    if [ ! -d "${QAIRT_LIBDIR}" ]; then
        echo "    ERROR: expected lib dir not found after unpack: ${QAIRT_LIBDIR}" >&2
        return 1
    fi
    echo "    QAIRT ${QAIRT_VERSION} ready at ${QAIRT_LIBDIR}"
}

if [ "${FETCH_QAIRT}" -eq 1 ]; then
    # Don't abort the whole run if the user declines the license; the summary
    # below will report QAIRT as missing.
    fetch_qairt || true
fi

# --- prerequisite summary ----------------------------------------------------
echo
echo "Prerequisite summary:"
status() {
    # $1 = label, $2 = 0/1 ok flag, $3 = remediation hint when missing
    if [ "$2" -eq 1 ]; then
        echo "  ✅ $1"
    else
        echo "  ❌ $1 — $3"
    fi
}

OK_FASTRPC=0
[ -e /usr/lib/libcdsprpc.so ] && OK_FASTRPC=1
OK_FIRMWARE=0
[ -d /usr/lib/dsp/cdsp ] && [ -d /usr/lib/rfsa/adsp ] && OK_FIRMWARE=1
OK_CDSPRPCD=0
{ systemctl is-active cdsprpcd >/dev/null 2>&1 || systemctl is-enabled cdsprpcd >/dev/null 2>&1; } && OK_CDSPRPCD=1
OK_GROUP=0
if [ -n "${TARGET_USER}" ] && id -nG "${TARGET_USER}" 2>/dev/null | tr ' ' '\n' | grep -qx fastrpc; then
    OK_GROUP=1
fi
OK_QAIRT=0
[ -d "${QAIRT_LIBDIR}" ] && OK_QAIRT=1

status "fastrpc user-space (libcdsprpc.so)" "${OK_FASTRPC}" "install the fastrpc .deb"
status "cDSP firmware (/usr/lib/dsp, /usr/lib/rfsa)" "${OK_FIRMWARE}" "install radxa-firmware-qcs6490"
status "cdsprpcd service active/enabled" "${OK_CDSPRPCD}" "systemctl enable --now cdsprpcd (advisory: some boards run the cDSP without it)"
status "user '${TARGET_USER}' in fastrpc group" "${OK_GROUP}" "log out/in to pick up the group"
status "QAIRT ${QAIRT_VERSION} at ${QAIRT_LIBDIR}" "${OK_QAIRT}" "re-run with --fetch-qairt or download manually (installation.md Step 2)"

echo
echo "Hexagon NPU host setup complete."
echo "Log out and back in for fastrpc group membership to take effect, then:"
echo "  docker run ... ghcr.io/blakeblackshear/frigate:stable-qualcomm"
echo "Pass these to the container (devices, group, QAIRT mounts):"
echo "  --device /dev/fastrpc-cdsp --device /dev/fastrpc-cdsp-secure"
echo "  --device /dev/fastrpc-adsp --device /dev/dma_heap/system"
echo "  --group-add \$(getent group fastrpc | cut -d: -f3)"
echo "  -v /usr/lib/dsp:/usr/lib/dsp:ro -v /usr/lib/rfsa:/usr/lib/rfsa:ro"
echo "  -v ${QAIRT_BASE}/${QAIRT_VERSION}/lib/aarch64-oe-linux-gcc11.2:/opt/qairt/lib:ro"
echo "  -v ${QAIRT_BASE}/${QAIRT_VERSION}/lib/hexagon-v68:/opt/qairt/hexagon-v68:ro"
echo
if [ "${OK_QAIRT}" -ne 1 ]; then
    echo "QAIRT not yet present. Either re-run with --fetch-qairt, or download"
    echo "the Community Edition (free, no portal login) manually:"
    echo "  ${QAIRT_URL}"
    echo "  (unzip into /opt/qcom/)"
    echo
fi
echo "If detection ever stops working (Frigate logs show 'IndexError' from"
echo "qnn.py or 'Failed to create transport for device, error: 4000'), the"
echo "cDSP is in a stuck state. Recover by REBOOTING THE HOST:"
echo "  sudo reboot"
echo "The cdsp-reset.service installed above bounces the remoteproc cleanly at"
echo "boot, when nothing holds the fastrpc devices."
echo
echo "WARNING: do NOT 'echo stop/start > /sys/class/remoteproc/remoteproc1/state'"
echo "on a running system. On Linux 6.18 / QCS6490, resetting the remoteproc while"
echo "any process (a Frigate container, a benchmark) holds /dev/fastrpc-* triggers"
echo "an unrecoverable kernel data abort — only a power-cycle recovers. Reboot instead."
