FROM python:3.10-slim

# The manual recipe uses ps to verify the final worker count.
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -r /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.rst LICENSE.txt ./
COPY sarracenia ./sarracenia
RUN python3 -m pip install --no-cache-dir .

COPY tests/manual/sanity/delayed_stop.py /fixture/delayed_stop.py
ENV HOME=/case PYTHONPATH=/fixture
ENV XDG_CONFIG_HOME=/case/config XDG_CACHE_HOME=/case/cache
RUN mkdir -p /case/config/sr3/flow /case/cache /case/input
COPY tests/manual/sanity/recovery.conf /case/config/sr3/flow/recovery.conf
WORKDIR /case
CMD ["bash"]
