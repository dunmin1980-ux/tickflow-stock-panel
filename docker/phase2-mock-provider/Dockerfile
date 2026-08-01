FROM gcr.io/distroless/python3-debian12@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5

LABEL org.tickflow.phase2.base-image-digest="sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5"

COPY --chown=65532:65532 phase2-ai-worker/worker.py /app/candidate_factory.py
COPY --chown=65532:65532 phase2-mock-provider/mock_provider.py /app/mock_provider.py
COPY --chown=65532:65532 phase2-mock-provider/secret.placeholder /run/phase2/provider-auth
COPY --chown=65532:65532 phase2-mock-provider/scenario.placeholder.json /input/scenario.json
COPY --chown=65532:65532 phase2-mock-provider/receipt.placeholder.json /output/receipt.json

USER 65532:65532
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0
ENTRYPOINT ["/usr/bin/python3", "/app/mock_provider.py"]
