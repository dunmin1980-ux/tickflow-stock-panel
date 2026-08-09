FROM gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5

ARG BASE_IMAGE_DIGEST
ARG PROXY_SOURCE_SHA256
ARG RESPONSES_CONTRACT_SHA256
ARG PROXY_POLICY_SHA256
ARG RUNTIME_CONTRACT_SHA256
ARG READINESS_CONTRACT_SHA256

LABEL org.tickflow.phase2.base-image-digest="${BASE_IMAGE_DIGEST}" \
      org.tickflow.phase2.proxy-source-sha256="${PROXY_SOURCE_SHA256}" \
      org.tickflow.phase2.responses-contract-sha256="${RESPONSES_CONTRACT_SHA256}" \
      org.tickflow.phase2.proxy-policy-sha256="${PROXY_POLICY_SHA256}" \
      org.tickflow.phase2.runtime-contract-sha256="${RUNTIME_CONTRACT_SHA256}" \
      org.tickflow.phase2.readiness-contract-sha256="${READINESS_CONTRACT_SHA256}"

COPY --chown=65532:65532 proxy.py /proxy/proxy.py
COPY --chown=65532:65532 responses-contract.json /proxy/responses-contract.json
COPY --chown=65532:65532 runtime-contract.json /proxy/runtime-contract.json
COPY --chown=65532:65532 readiness-contract.json /proxy/readiness-contract.json
COPY --chown=65532:65532 secret.placeholder /run/phase2/provider-auth
COPY --chown=65532:65532 receipt.placeholder.json /output/receipt.json

USER 65532:65532
WORKDIR /proxy
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0
ENTRYPOINT ["/usr/bin/python3", "/proxy/proxy.py"]
