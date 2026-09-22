.PHONY: help install install-dev venv run run-local build up down shell test lint \
	clean gpu-check probe-ffmpeg xhost

APP        := video-concat
IMAGE      := video-concat:local
VENV       := .venv
PYTHON     := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
COMPOSE    := docker compose
MEDIA_DIR  ?= $(HOME)/Videos

export VIDEO_MEDIA_DIR := $(MEDIA_DIR)

help:
	@echo "Video Concat — objetivos disponibles"
	@echo ""
	@echo "  Nativo"
	@echo "    make venv          Crea entorno virtual"
	@echo "    make install       Instala la app en el venv"
	@echo "    make install-dev   Instala app + pytest/ruff"
	@echo "    make run-local     Lanza la UI en el host"
	@echo "    make test          Ejecuta pytest"
	@echo "    make lint          Ejecuta ruff"
	@echo ""
	@echo "  Docker"
	@echo "    make build         Construye la imagen"
	@echo "    make run           Arranca la UI en contenedor (GPU + Wayland)"
	@echo "    make up            Alias de run"
	@echo "    make down          Detiene el servicio compose"
	@echo "    make shell         Shell interactivo en la imagen"
	@echo "    make gpu-check     Verifica NVENC dentro del contenedor"
	@echo "    make xhost         Autoriza X11/Wayland al contenedor (si hace falta)"
	@echo ""
	@echo "  Misc"
	@echo "    make probe-ffmpeg  Muestra encoders NVENC del host"
	@echo "    make clean         Limpia artefactos"
	@echo ""
	@echo "  MEDIA_DIR=$(MEDIA_DIR)  (volumen montado en /media)"

venv:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

install: venv
	$(PIP) install -e .

install-dev: venv
	$(PIP) install -e ".[dev]"

run-local: install
	$(VENV)/bin/video-concat

build:
	$(COMPOSE) build

xhost:
	@command -v xhost >/dev/null && xhost +local: || true

run up: build xhost
	@mkdir -p "$(MEDIA_DIR)"
	$(COMPOSE) run --rm video-concat

down:
	$(COMPOSE) down --remove-orphans

shell: build
	$(COMPOSE) run --rm --entrypoint bash video-concat

gpu-check: build
	$(COMPOSE) run --rm --entrypoint bash video-concat -lc '\
		ffmpeg -hide_banner -encoders 2>/dev/null | grep -i nvenc || true; \
		nvidia-smi -L 2>/dev/null || echo "nvidia-smi no disponible en contenedor"; \
		python3 -c "from video_concat.core.gpu import detect_gpu; print(detect_gpu())"'

probe-ffmpeg:
	@ffmpeg -hide_banner -encoders 2>/dev/null | grep -i nvenc || echo "sin nvenc en host"
	@nvidia-smi -L 2>/dev/null || true

test: install-dev
	$(VENV)/bin/pytest -q

lint: install-dev
	$(VENV)/bin/ruff check src tests

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache dist build *.egg-info \
		src/*.egg-info **/__pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	$(COMPOSE) down --rmi local --remove-orphans 2>/dev/null || true
