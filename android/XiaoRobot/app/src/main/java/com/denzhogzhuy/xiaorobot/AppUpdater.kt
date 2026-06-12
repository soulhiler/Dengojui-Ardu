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
 * Обновление приложения с ПК в локальной сети.
 * Дашборд (tools/xiao_serial_telemetry.py) отдаёт:
 *   GET /app/version.json  → {"versionCode": N, "apk": "/app/apk"}
 *   GET /app/apk           → свежий APK из dist/ на ПК
 * Сравниваем versionCode с установленным; если новее — качаем в кэш и
 * передаём системному установщику через FileProvider.
 */
class AppUpdater(
    private val activity: AppCompatActivity,
    private val onStatus: (String) -> Unit,
) {
    /** quiet: авто-проверка после подключения — шумим только если реально есть обновление. */
    fun checkAndInstall(host: String, scope: CoroutineScope, quiet: Boolean = false) {
        scope.launch(Dispatchers.IO) {
            try {
                if (!quiet) onStatus("обновление: проверяю $host…")
                val meta = JSONObject(httpGetText("http://$host/app/version.json"))
                val remote = meta.optLong("versionCode", 0L)
                val info = activity.packageManager.getPackageInfo(activity.packageName, 0)
                val cur = PackageInfoCompat.getLongVersionCode(info)
                if (remote <= cur) {
                    if (!quiet) onStatus("обновление: установлена последняя версия (v$cur)")
                    return@launch
                }
                onStatus("обновление: качаю v$remote…")
                val apk = downloadApk("http://$host" + meta.optString("apk", "/app/apk"))
                onStatus("обновление: запускаю установку v$remote")
                val uri = FileProvider.getUriForFile(
                    activity, activity.packageName + ".fileprovider", apk,
                )
                val intent = Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "application/vnd.android.package-archive")
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                activity.startActivity(intent)
            } catch (e: Exception) {
                if (!quiet) onStatus("обновление: ошибка — ${e.message}")
            }
        }
    }

    private fun httpGetText(url: String): String {
        val c = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 4000
            readTimeout = 4000
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
            connectTimeout = 5000
            readTimeout = 120000
        }
        try {
            c.inputStream.use { inp -> out.outputStream().use { inp.copyTo(it) } }
        } finally {
            c.disconnect()
        }
        return out
    }
}
