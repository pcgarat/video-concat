# syntax=docker/dockerfile:1.7

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    QT_QPA_PLATFORM=wayland \
    QT_X11_NO_MITSHM=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
        libgl1 \
        libxkbcommon0 \
        libxkbcommon-x11-0 \
        libdbus-1-3 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-xinerama0 \
        libxcb-xfixes0 \
        libegl1 \
        libwayland-client0 \
        libwayland-cursor0 \
        libwayland-egl1 \
        fonts-ibm-plex \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python3 -m pip install --no-cache-dir --break-system-packages .

# Ubuntu 24.04 ya trae usuario `ubuntu` con uid 1000
RUN mkdir -p /media /tmp/runtime-user \
    && chown -R ubuntu:ubuntu /app /media /tmp/runtime-user

USER ubuntu
ENV XDG_RUNTIME_DIR=/tmp/runtime-user \
    HOME=/home/ubuntu

WORKDIR /media
ENTRYPOINT ["video-concat"]
