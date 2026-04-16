# 🚀 Zapret Hub

**Zapret Hub** - Windows-приложение для **удобного управления** `zapret` и `tg-ws-proxy` из **одного интерфейса**. 

**Для обычных пользователей** - без bat-файлов, поиска папок и правки конфигов в блокноте!

<img width="1082" height="669" alt="Главный интерфейс Zapret-Hub" src="https://github.com/user-attachments/assets/7acbcb67-d79f-4cde-b66b-ed6dcbaf901d" />

**Автор**: goshkow • [GitHub](https://github.com/goshkow/Zapret-Hub)

## 💡 Что это такое

Проект объединяет в **одном окне**:

✅ запуск/остановку `Zapret` и `TG WS Proxy`  
✅ автоматическую загрузку и работу в трее
✅ выбор `general`-конфигураций для `zapret`  
✅ импорт модификаций  
✅ диагностику и тестирование  
✅ просмотр логов и файлов  
✅ portable-сборку + установщик  

> Приложение не запускает дополнительные программы, все обходы встроены и не требуют настройки вне приложения.

<img width="1630" height="776" alt="Настройки" src="https://github.com/user-attachments/assets/89c2f9ce-f35f-472e-9f73-98df4c5ff27e" />

## ✨ Возможности

| Фича | Описание |
|------|----------|
| 🎮 **Единая кнопка** | Подключение/отключение одним кликом |
| ⚙️ **Гибкая работа** | Отдельный старт Zapret + TG WS Proxy |
| 🌙 **Трей и фон** | Сворачивается только при активной работе |
| 🚀 **Автозапуск** | С Windows |
| 📦 **Моды** | Импорт из папки/ZIP/GitHub/файлов |
| 🛡️ **Безопасность** | Отдельный runtime, бэкапы базовых файлов |
| 🔍 **Диагностика** | Тестирование general'ей + логи |
| 🎨 **UI** | Светлая/тёмная тема, RU/EN |
| 📱 **Форматы** | Portable + установщик (x64/ARM64) |
| 🔷 **Работа в трее** | Не нужно держать окно открытым при работе |

## 🛠 Модификации

🔹 Хранит моды **отдельно**  
🔹 **Не трогает** базовые файлы  
🔹 Собирает runtime в `merged_runtime/`  
🔹 Импорт general/списков из GitHub/ZIP  

<img width="1743" height="661" alt="Модификации" src="https://github.com/user-attachments/assets/eecec5b1-37e8-4c6c-8121-3d74a455aaf6" />


## 💻 Требования

- 🪟 Windows 10/11
- 🐍 Python 3.11+
- ⚡ PowerShell 5+ / 7+

## 📦 Portable и Installer

В проекте используются три основных формата поставки:

- `portable\win_x64` — portable для Windows x64;
- `portable\win_arm64` — portable для Windows ARM64;
- `install_zaprethub.exe` — установщик.

## 🔗 Используемые проекты

| Инструмент | Автор |
|------------|--------|
| [zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube) | **Flowseal** |
| [tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy) | **Flowseal** |
| [zapret](https://github.com/bol-van/zapret-win-bundle) экосистема | **bol-van** |

**Zapret Hub** = интерфейс + менеджер **поверх** этих инструментов.

## 📁 Структура проекта

Основные каталоги:

- `📂 src/zapret_hub` — прикладная логика, UI и сервисы;
- `📂 installer` — код установщика;
- `📂 packaging` — `.spec`-файлы PyInstaller;
- `📂 runtime` — встроенные runtime-файлы bundled-инструментов;
- `📂 sample_data` — стартовые данные проекта;
- `📂 ui_assets` — иконки и UI-ресурсы.

Рабочие каталоги, которые появляются во время использования приложения:

- `📂 data`
- `📂 logs`
- `📂 cache`
- `📂 mods`
- `📂 merged_runtime`
- `📂 backups`


## 🧪 Запуск в разработке

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m zapret_hub.main
```

## 🔨 Сборка

### Приложение
```powershell
.venv\Scripts\python.exe -m PyInstaller -y packaging\zapret_hub.spec
```
**Результат**: `dist\zapret_hub\`

### Установщик
```powershell
.venv\Scripts\python.exe -m PyInstaller -y packaging\install_zaprethub.spec
```
**Результат**: `dist\install_zaprethub.exe`
