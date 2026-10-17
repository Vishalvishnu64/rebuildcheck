FROM python:3.12-slim

WORKDIR app
COPY . .

RUN python -m unittest discover -s tests -v

ENTRYPOINT ["python", "-m", "rebuildcheck"]
