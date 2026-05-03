#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local script shim. Use `python -m musinsa` or `from musinsa.pipeline import main`."""
from __future__ import annotations

from musinsa.cli import entrypoint


if __name__ == "__main__":
    entrypoint()
