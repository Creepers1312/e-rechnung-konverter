FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_THREAD_LIMIT=1

# Tesseract with German + English (OCR), Ghostscript (PDF handling, PDF/A-3 conversion),
# Java for veraPDF (PDF/A check). The man directory is missing in slim images and the
# JRE's post-install script needs it.
RUN mkdir -p /usr/share/man/man1 \
 && apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng ghostscript \
      default-jre-headless \
 && rm -rf /var/lib/apt/lists/*

# veraPDF, pinned version + SHA-256 (see install_verapdf.py)
COPY install_verapdf.py /tmp/
RUN python /tmp/install_verapdf.py /opt/verapdf \
 && ln -s /opt/verapdf/verapdf /usr/local/bin/verapdf \
 && rm /tmp/install_verapdf.py

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .

RUN useradd --system --uid 10002 --no-create-home ocr
USER ocr

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
