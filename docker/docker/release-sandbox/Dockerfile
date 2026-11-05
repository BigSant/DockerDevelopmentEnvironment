# Release isolation (RELEASE_ISOLATION=container; docker/release_sandbox.py): every operation of setup on an untrusted
# release copy or on the target's code tree runs in a throwaway container of this image, which mounts only those two
# directories and setup's helper scripts (read only), with no network, a read-only root and no capabilities.
# Nothing of setup is baked in: the helpers are mounted at run time, so this image only changes with its tools.
FROM alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
RUN apk add --no-cache python3 rsync tar
LABEL setup.managed=1 setup.role=release-sandbox
