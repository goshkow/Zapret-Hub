<div align="center">

# 🚀 Zapret Hub

**Zapret Hub** - Windows-приложение для **удобного управления** `zapret` и `tg-ws-proxy` из **одного интерфейса**. 

**Для обычных пользователей** - без bat-файлов, поиска папок и правки конфигов в блокноте!

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/banner-light.png">
  <img alt="Zapret Hub banner" src="assets/banner-dark.png">
</picture>

**Автор**: goshkow • [GitHub](https://github.com/goshkow/Zapret-Hub)

</div>

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

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/mods-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/mods-light.png">
  <img alt="Меню модификаций" src="assets/mods-dark.png" width="75%">
</picture>

## 💻 Требования

- 🪟 Windows 10/11
  
Для разработки:
- 🐍 Python 3.11+
- ⚡ PowerShell 5+ / 7+

> [!WARNING]
>
> ### АНТИВИРУСЫ
> WinDivert - это инструмент для перехвата и фильтрации трафика, необходимый для работы zapret. Он не является вирусом.
>
> Некоторые антивирусы склонны относить файлы WinDivert к классам повышенного риска или хакерским инструментам. Происходит удаление файла и помещение его в карантин. При этом детект обязательно имеет название `WinDivert` или `Not-a-virus:RiskTool.Multi.WinDivert`
>
> В случае проблем с антивирусом добавьте папку с Zapret Hub в исключения, либо отключите детектирование PUA (потенциально нежелательных приложений). Например, в касперском есть галочка "Обнаруживать легальные приложения, которые злоумышленники часто используют для нанесения вреда". При аккуратной и правильной настройке исключений - рекомендуется настроить исключение, но если вы не до конца понимаете что делаете - рекомендуется отключить детект PUA.

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

> [!CAUTION]
>
> ### Авторство
> **Zapret Hub** = интерфейс + менеджер **поверх** этих инструментов.
> 
> В самой программе дважды указаны инструменты и их авторство.

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
