FROM gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5

LABEL org.tickflow.phase2.base-image-digest="sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"

COPY --chown=65532:65532 relay.py /relay/relay.py
COPY --chown=65532:65532 request.placeholder.json /input/request.json
COPY --chown=65532:65532 projection.placeholder.json /input/projection.json
COPY --chown=65532:65532 response.placeholder.json /output/response.json
COPY --chown=65532:65532 receipt.placeholder.json /output/receipt.json

USER 65532:65532
WORKDIR /relay
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
ENTRYPOINT ["/usr/bin/python3", "/relay/relay.py"]
CMD ["run"]
