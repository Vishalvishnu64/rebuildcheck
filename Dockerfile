# The Python 3.12.8 slim-bookworm tag exists in Docker Hub and this digest
# pins its multi-architecture manifest; /app is container-local, not a host path.
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0

WORKDIR /app

# Git and make are required by the documented clean-room command when the
# checker is run against a mounted repository. Sources are copied only after
# the runtime tools are installed, then the same offline test command used by
# the README is executed during the image build.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes git make \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN python -m unittest discover -s tests -v

ENTRYPOINT ["python", "-m", "rebuildcheck"]
CMD ["--help"]
