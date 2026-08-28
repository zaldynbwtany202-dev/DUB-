# DUB- — one-command entry points
#   make setup   install ffmpeg + python deps
#   make test    run unit tests
#   make dub VIDEO=clip.mp4 SRC=en TGT=ar ENGINE=xtts

VIDEO ?= input.mp4
SRC   ?= en
TGT   ?= ar
ENGINE ?= xtts
OUT   ?= dubbed_$(TGT).mp4

.PHONY: setup test dub docker-build docker-dub

setup:
	bash scripts/setup.sh

test:
	python -m pytest tests/ -q

dub:
	dub run $(VIDEO) --src $(SRC) --tgt $(TGT) --engine $(ENGINE) --out $(OUT)

docker-build:
	docker build -t dub-forge .

docker-dub: docker-build
	docker run --rm -v $(PWD):/work dub-forge \
	  run /work/$(VIDEO) --src $(SRC) --tgt $(TGT) --engine $(ENGINE) --out /work/$(OUT)
