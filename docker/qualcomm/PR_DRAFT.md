# [Draft] Add Qualcomm Hexagon NPU detector (`qnn` / `-qcs6490`)

> **DRAFT — pre-submission notes for the contributor.** This file is local-only,
> not committed. It is the proposed PR description plus a few decisions that
> still need a maintainer's input before this is ready to open.

## Summary

Adds a community-supported hardware detector for the **Qualcomm Hexagon NPU** on
the QCS6490 SoC (Radxa Dragon Q6A and similar boards). Mirrors the existing
community-board pattern (Rockchip / Synaptics): new detector plugin, new
`docker/qcs6490/` build subdirectory with its own bake target, and a host-side
installer script for the FastRPC + firmware bits that have to live on the host.

Closes #18602 (the only prior Qualcomm/QCS6490 request — closed without code,
explicitly redirected to "implement as a community-supported board").

Resulting Docker image: `ghcr.io/blakeblackshear/frigate:stable-qcs6490`.

## Performance

Measured on Radxa Dragon Q6A (QCS6490, Hexagon v68, ~12 TOPS), YOLOv8n at 640×640,
QNN context binary from Qualcomm AI Hub:

- ~10 ms per inference under light load
- ~24 ms per inference with 5 RTSP cameras pumping frames

For comparison, the same model on the Cortex-A78 cores via ONNX Runtime CPU is
~340 ms — roughly **30× speedup** on the NPU.

## Files added

```
frigate/detectors/plugins/qnn.py       # detector plugin (~150 LOC)
docker/qcs6490/Dockerfile              # two-stage; builds qai_appbuilder wheel inside Frigate's image
docker/qcs6490/qcs6490.hcl             # bake target
docker/qcs6490/qcs6490.mk              # local-/build-/push- targets, BOARDS += qcs6490
docker/qcs6490/user_installation.sh    # host-side: fastrpc, firmware, cdsprpcd
.github/workflows/ci.yml               # qcs6490_build job (mirrors synaptics_build)
CODEOWNERS                             # /docker/qcs6490/ and qnn.py owned by @cbwcjw
docs/docs/configuration/object_detectors.md   # new ## Qualcomm Hexagon NPU section under Community Supported
docs/docs/frigate/installation.md      # new ### Qualcomm platform section
docs/docs/frigate/hardware.md          # new ### Qualcomm Hexagon NPU row
```

No changes to existing detector code paths. No new `ModelTypeEnum` values —
`yolo-generic` is used (consistent with the post-MemryX-PR convention).

## Design choices

- **`yolo-generic` model type, not a new `yolov8` enum.** Following the
  guidance from PR #17723 review: all YOLO variants should funnel through
  `yologeneric`.
- **No bundled model.** YOLOv8 weights are AGPL-3.0 (Ultralytics), so the
  user downloads a QNN context binary from
  [Qualcomm AI Hub](https://aihub.qualcomm.com/) once and mounts it in.
  The detector docs include the warning + the steps. Same approach as
  YOLO-NAS and DeGirum precedent.
- **Lazy SDK import.** `qai_appbuilder` is only imported when the detector
  actually loads (matching PRs #19737 and #19802 for MemryX/DeGirum).
- **Synchronous `detect_raw`.** No `RequestStore`/`ResponseStore` async
  scaffolding — at ~10 ms, single-thread synchronous is fine and simpler.
  Can be added later if multi-stream throughput becomes an issue.
- **Two-stage Dockerfile.** Builds the `qai_appbuilder` wheel inside the
  Frigate image so its libstdc++/glibc ABI matches Frigate's runtime stage.
  Building it on the host produces a wheel that crashes Frigate's Python
  with `GLIBCXX_*` errors — already verified during development.

## Things that might draw review comments — let me address them up front

1. **`ADSP_LIBRARY_PATH` uses `;` (semicolon) as the separator, not `:`.**
   Counterintuitive but it's what `libxdsprpc.so` expects. The Dockerfile and
   docs both say so explicitly with a comment, since this is the single
   biggest gotcha that would burn anyone debugging this.
2. **Bind-mounts of `/usr/lib/dsp` and `/usr/lib/rfsa` from the host.** The
   cDSP firmware refuses to load skel libraries from any path other than
   these two (returns "error 1002 / Failed to load skel" if you try to use
   the SDK-shipped copies under `/opt/qairt/`). Documented and called out
   in the install instructions.
3. **`detect_raw` always copies the input tensor.** Frigate hands a view
   into a shared-memory mmap; passing that directly into qai_appbuilder's
   C++ boundary segfaults. The plugin always materializes a private
   contiguous float32 buffer. One-line comment explains why.
4. **`docker/qcs6490/` is the smallest community-board subtree to date** —
   Dockerfile, hcl, mk, and a single shell script. No COCO labels (we use
   the existing `/labelmap/coco-80.txt`), no in-tree model, no per-board
   ffmpeg.

## Open questions for the reviewer

1. **Where should the QAIRT runtime tarball be hosted?** The Dockerfile
   currently fetches from a placeholder URL on my own GH releases:
   `https://github.com/cbwcjw/qairt-runtime/releases/...`. Hailo and
   Rockchip use `frigate-nvr/*` releases for their per-board runtime
   binaries. If you'd prefer the same pattern, I can mirror the tarball
   into a `frigate-nvr/qairt-runtime` release once you create it (or
   provide a script to do so). The tarball is ~50 MB and contains only
   the QAIRT runtime libs + Hexagon v68 backend skel — everything else is
   built from upstream sources at build time.
2. **CODEOWNERS handle.** Used `@cbwcjw`. Replace with the GitHub handle
   you'd prefer for community-detector ownership pings.

## Test plan

- [x] Standalone Python harness on Radxa Dragon Q6A: YOLOv8n NPU at ~11 ms median (50 iters), CPU baseline ~343 ms — same detections (bus + 3 people, conf 0.84–0.88) on `bus.jpg`.
- [x] Frigate inside container: detector subprocess loads, warmup OK, first real frame OK, no segfault.
- [x] Live multi-camera: 5 RTSP streams running for 4+ hours, `inference_speed` 10–25 ms.
- [ ] CI build of the `qcs6490` target succeeds (need this PR open as draft against `dev` to verify).
- [ ] Manual smoke test from a clean board following only the documentation in this PR (no existing local state).

## Branch / target

- Branch: `qcs6490-detector` on a fork
- Target: `dev`
- Status: **DRAFT** — opening for early feedback per #11365's pattern
- No `@-mentions` of maintainers in the initial PR body. Will let GitHub's
  CODEOWNERS auto-request the active detector reviewers via the file paths.
