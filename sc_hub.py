#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SC HUB - console menu for working with Supercell .sc files (SC1 and SC2).
SC HUB - консольное меню для работы с .sc

Run / Запуск: start.bat (Windows), ./start.sh (Linux/macOS) or `python sc_hub.py [--lang en|ru]`.
The code lives in the schub/ package / Код лежит в пакете schub/ (см. README, раздел Architecture).
"""
from schub.flows import main

if __name__ == "__main__":
    main()
