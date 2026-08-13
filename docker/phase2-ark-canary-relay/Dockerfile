FROM gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5

ARG BASE_IMAGE_DIGEST
ARG RELAY_SOURCE_SHA256
ARG RUNTIME_CONTRACT_SHA256

LABEL org.tickflow.phase2.provider-id="volcengine_ark" \
      org.tickflow.phase2.base-image-digest="${BASE_IMAGE_DIGEST}" \
      org.tickflow.phase2.relay-source-sha256="${RELAY_SOURCE_SHA256}" \
      org.tickflow.phase2.runtime-contract-sha256="${RUNTIME_CONTRACT_SHA256}"

COPY --chown=65532:65532 relay.py /relay/relay.py
COPY --chown=65532:65532 runtime-contract.json /relay/runtime-contract.json
COPY --chown=65532:65532 request.placeholder.json /input/request.json
COPY --chown=65532:65532 projection.placeholder.json /input/projection.json
COPY --chown=65532:65532 output.placeholder /output/.placeholder

USER 65532:65532
WORKDIR /relay
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0
ENTRYPOINT ["/usr/bin/python3", "/relay/relay.py"]
