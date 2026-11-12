FROM python:3.12-slim

ARG WHISPER_GPU=1
ARG WHISPER_MODEL=small

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CSAFI_IN_DOCKER=1 \
    DATA_DIR=/app/data \
    HF_HOME=/app/models

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 curl rclone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-gpu.txt ./
RUN pip install -r requirements.txt \
 && if [ "$WHISPER_GPU" = "1" ]; then pip install -r requirements-gpu.txt; fi

# The pip CUDA libraries live inside site-packages; point the loader at them.
RUN SP=$(python -c "import site; print(site.getsitepackages()[0])") \
 && echo "$SP/nvidia/cublas/lib" > /etc/ld.so.conf.d/nvidia-pip.conf \
 && echo "$SP/nvidia/cudnn/lib" >> /etc/ld.so.conf.d/nvidia-pip.conf \
 && ldconfig

RUN useradd --create-home --uid 1000 csafi \
 && mkdir -p /app/data /app/models \
 && chown -R csafi:csafi /app
USER csafi

# Bake the Whisper model into the image so the first reel does not wait on a download.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')" \
 && python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

COPY --chown=csafi:csafi csafi_bot ./csafi_bot

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8765/health || exit 1
CMD ["python", "-m", "csafi_bot", "serve", "--host", "0.0.0.0", "--port", "8765"]
