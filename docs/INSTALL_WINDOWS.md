# Установка NVCode на Windows

## Быстрый старт (Python)

1. Установите Python 3.11+ с python.org и включите добавление Python в PATH.
2. Склонируйте репозиторий NVCode или скачайте его архив.
3. Откройте cmd или PowerShell в каталоге репозитория:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-ui.txt
python dispatcher.py --init
python dispatcher.py --doctor
```

## Запуск

```bat
python dispatcher.py
python dispatcher_ui.py
```

Или проверьте скрипт `scripts\start_all.bat`.

## Ollama (опционально)

```bat
ollama pull qwen2.5-coder:7b
```

Подберите модель под доступную память и скорость компьютера. Наличие модели не гарантирует, что worker настроен правильно.

## Сборка .exe (опционально)

```bat
pip install pyinstaller
python scripts\build_exe.py
```

Ожидаемые имена сборки: `dist\NVCodeUI.exe` и `dist\NVCode.exe`. Проверьте фактический результат сборки; worker-инструменты могут требовать отдельной установки.

## Документация

Начните с [общего индекса](INDEX.md) и [Getting Started](GETTING_STARTED.md).