package com.denzhogzhuy.xiaorobot

import android.content.Intent
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.core.content.pm.PackageInfoCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Обновление приложения по сети. Два источника:
 *  1) GitHub-релиз `apk-latest` (canonical сборка из CI) — `checkGithubAndInstall`,
 *     работает откуда угодно с интернетом, без ПК. ОСНОВНОЙ путь (кнопка «Обновить»).
 *  2) ПК-дашборд в LAN (`tools/xiao_serial_telemetry.py`, `/app/version.json` + `/app/apk`)
 *     — `checkAndInstall`, запасной для сети без интернета.
 * Версию сравниваем с установленным versionCode; новее — качаем в кэш и отдаём
 * системному установщику через FileProvider.
 */
class AppUpdater(
    private val activity: AppCompatActivity,
    private val onStatus: (String) -> Unit,
) {
    private val githubRelease =
        "https://api.github.com/repos/soulhiler/Dengojui-Ardu/releases/tags/apk-latest"

    /** Обновление из GitHub-релиза apk-latest (нужен интернет; на SoftAP без инета не сработает). */
    fun checkGithubAndInstall(scope: CoroutineScope, quiet: Boolean = false) {
        scope.launch(Dispatchers.IO) {
            try {
                if (!quiet) onStatus("обновление: проверяю GitHub…")
                val rel = JSONObject(httpGetText(githubRelease))
                val assets = rel.optJSONArray("assets")
                var url = ""
                var name = ""
                if (assets != null) {
                    for (i in 0 until assets.length()) {
                        val a = assets.getJSONObject(i)
                        if (a.optString("name").endsWith(".apk")) {
                            url = a.optString("browser_download_url")
                            name = a.optString("name")
                            break
                        }
                    }
                }
                if (url.isEmpty()) {
                    if (!quiet) onStatus("обновление: APK не найден в релизе")
                    return@launch
                }
                // имя вида xiao-robot-v10-debug.apk → 10
                val remote = Regex("v(\\d+)").find(name)?.groupValues?.get(1)?.toLongOrNull() ?: 0L
                val cur = currentVersion()
                if (remote <= cur) {
                    if (!quiet) onStatus("обновление: уже последняя (v$cur)")
                    return@launch
                }
                onStatus("обновление: качаю v$remote…")
                val apk = downloadApk(url)
                onStatus("обновление: установка v$remote")
                installApk(apk)
            } catch (e: Exception) {
                if (!quiet) onStatus("обновление: нет интернета или ошибка — ${e.message}")
            }
        }
    }

    /** Запасной путь: обновление с ПК-дашборда в LAN. */
    fun checkAndInstall(host: String, scope: CoroutineScope, quiet: Boolean = false) {
        scope.launch(Dispatchers.IO) {
            try {
                if (!quiet) onStatus("обновление: проверяю $host…")
                val meta = JSONObject(httpGetText("http://$host/app/version.json"))
                val remote = meta.optLong("versionCode", 0L)
                val cur = currentVersion()
                if (remote <= cur) {
                    if (!quiet) onStatus("обновление: установлена последняя версия (v$cur)")
                    return@launch
                }
                onStatus("обновление: качаю v$remote…")
                val apk = downloadApk("http://$host" + meta.optString("apk", "/app/apk"))
                onStatus("обновление: запускаю установку v$remote")
                installApk(apk)
            } catch (e: Exception) {
                if (!quiet) onStatus("обновление: ошибка — ${e.message}")
            }
        }
    }

    private fun currentVersion(): Long {
        val info = activity.packageManager.getPackageInfo(activity.packageName, 0)
        return PackageInfoCompat.getLongVersionCode(info)
    }

    private fun installApk(apk: File) {
        val uri = FileProvider.getUriForFile(activity, activity.packageName + ".fileprovider", apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        activity.startActivity(intent)
    }

    private fun httpGetText(url: String): String {
        val c = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 6000
            readTimeout = 6000
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "XiaoRobot")  // GitHub API требует User-Agent
            setRequestProperty("Accept", "application/vnd.github+json")
        }
        try {
            return c.inputStream.bufferedReader().readText()
        } finally {
            c.disconnect()
        }
    }

    private fun downloadApk(url: String): File {
        val dir = File(activity.cacheDir, "updates").apply { mkdirs() }
        val out = File(dir, "xiao-robot.apk")
        val c = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 8000
            readTimeout = 120000
            instanceFollowRedirects = true                 // github.com → objects.githubusercontent.com
            setRequestProperty("User-Agent", "XiaoRobot")
        }
        try {
            c.inputStream.use { inp -> out.outputStream().use { inp.copyTo(it) } }
        } finally {
            c.disconnect()
        }
        return out
    }
}
