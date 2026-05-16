# Архив переписок Cursor (агент-транскрипты)

Формат: **JSON Lines** (`.jsonl`) — по одной JSON-записи на строку (`role`, `message`, …). Экспорт внутреннего стека Cursor для воркспейса **It trane exp**.

| Файл | UUID папки Cursor | Примечание |
|------|-------------------|------------|
| `chat-67f50fc7-xiao-robot-and-android.jsonl` | [67f50fc7-a2ee-4229-81bf-95c45d6bf959](67f50fc7-a2ee-4229-81bf-95c45d6bf959) | Основная переписка: XIAO Sense, прошивка, TB6612, Android, прокси и т.д. |
| `chat-722e16c5-earlier-session.jsonl` | [722e16c5-fea9-4d3b-ab0a-99434d6cf814](722e16c5-fea9-4d3b-ab0a-99434d6cf814) | Более ранняя сессия по тому же воркспейсу |

Архив со всеми `chat-*.jsonl`: **`cursor-chats-workspace-it-trane-exp.zip`**.

Распаковка не обязательна: `.jsonl` можно открыть в редакторе или обработать `jq`/скриптом.

### Обновить ZIP после добавления новых `.jsonl`

Из корня репозитория `denzhogzhuy-ardu`:

```powershell
Compress-Archive -Path "docs\cursor-chat-archives\chat-*.jsonl" -DestinationPath "docs\cursor-chat-archives\cursor-chats-workspace-it-trane-exp.zip" -Force
```
