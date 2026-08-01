FROM gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5

LABEL org.tickflow.phase2.base-image-digest="sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"

COPY --chown=65532:65532 proxy.py /proxy/proxy.py
COPY --chown=65532:65532 secret.placeholder /run/phase2/provider-auth
COPY --chown=65532:65532 receipt.placeholder.json /output/receipt.json

USER 65532:65532
WORKDIR /proxy
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
ENTRYPOINT ["/usr/bin/python3", "/proxy/proxy.py"]
