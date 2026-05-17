# Очистка git-истории от утёкшего Wi-Fi пароля

> ⚠️ Эти шаги **переписывают историю git и делают force-push**. Это
> разрушительная операция для общего репозитория: все клоны придётся
> пересоздать. Выполняет **владелец репозитория** после согласования.
> Агент это автоматически не делает.

## Что случилось

Реальный Wi-Fi пароль попал в архив переписки Cursor.

- Файл: `docs/cursor-chat-archives/chat-67f50fc7-xiao-robot-and-android.jsonl`
- Введён коммитом: `b283eab` ("Add drive stack, Android remote, tooling, and Cursor chat archive.")
- Рабочее дерево **уже очищено** (строка заменена на `***REDACTED***`,
  zip пересобран). Но строка **остаётся в истории** коммита `b283eab`.
- Удалённый репозиторий: `origin` = `github.com/soulhiler/Dengojui-Ardu`.

## Шаг 0 (ОБЯЗАТЕЛЬНО, независимо от остального)

Считать пароль скомпрометированным: **сменить пароль Wi-Fi на роутере** и
обновить локальный `xiao_cam_stream/secrets.h`. Даже после чистки истории
старое значение могло быть склонировано/проиндексировано — ротация
обязательна и не откладывается.

## Шаг 1. Предупредить всех

Все, у кого есть клон, должны прекратить пушить и быть готовы
переклонировать после рерайта (старые хеши станут невалидны).

## Шаг 2. Переписать историю (вариант A — git filter-repo, рекомендуется)

`git filter-repo` безопаснее всего запускать на свежем зеркале:

```bash
# рядом с рабочей копией, не внутри неё
git clone --mirror https://github.com/soulhiler/Dengojui-Ardu.git dengojui-mirror
cd dengojui-mirror

# файл с заменами
printf '0815350698==>***REDACTED***\n' > ../replace.txt

git filter-repo --replace-text ../replace.txt

# проверка: должно быть пусто
git log --all -S '0815350698' --oneline
```

### Вариант B — BFG Repo-Cleaner

```bash
git clone --mirror https://github.com/soulhiler/Dengojui-Ardu.git dengojui-mirror
printf '0815350698\n' > replacements.txt   # BFG заменит на ***REMOVED***
java -jar bfg.jar --replace-text replacements.txt dengojui-mirror
cd dengojui-mirror && git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

## Шаг 3. Force-push переписанной истории

> Согласовать. Это перезапишет все ветки/теги в `origin`.

```bash
# из зеркала
git push --force --all
git push --force --tags
```

## Шаг 4. Всем — переклонировать

Старые локальные клоны несовместимы. Удалить и склонировать заново
(или жёстко сбросить на новые ветки). Незапушенную работу сначала сохранить
патчем (`git format-patch`) и применить заново.

## Шаг 5. Учесть кэш GitHub

GitHub может какое-то время отдавать старые объекты по прямой ссылке на
коммит и в форках/PR. Это ещё одна причина, почему **Шаг 0 (смена пароля)
обязателен** — чистка истории не отменяет факт компрометации.

## Профилактика

Включена защита от рецидива: `git config core.hooksPath .githooks`
(хук `.githooks/pre-commit`). См. раздел «Безопасность» в `README.md`.
