FROM python:3.10-slim
WORKDIR /work
COPY . /work
RUN pip install --no-cache-dir '.[amqp]' pytest
RUN useradd --create-home proof
USER proof
ENV PYTHONDONTWRITEBYTECODE=1
