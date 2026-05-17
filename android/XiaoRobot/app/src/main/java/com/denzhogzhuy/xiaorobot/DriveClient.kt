package com.denzhogzhuy.xiaorobot

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
/** GET /drive?l=&r= и /control?cam=1&mic=1 */
class DriveClient(
    private val scope: CoroutineScope,
    private val onStatus: ((String) -> Unit)? = null,
) {
    private var driveJob: Job? = null
  @Volatile var cmdL: Int = 0
  @Volatile var cmdR: Int = 0
  /** Токен авторизации платы; пусто = не слать (auth выключен на плате). */
  @Volatile var token: String = ""
  @Volatile private var lastOk: Boolean? = null

    private fun auth(): String =
        if (token.isEmpty()) "" else "&t=" + URLEncoder.encode(token, "UTF-8")

    fun enableBoard(host: String) {
        scope.launch(Dispatchers.IO) {
            get("http://$host/control?cam=1&mic=1${auth()}")
        }
    }

    fun startSending(host: String, intervalMs: Long = 120L) {
        driveJob?.cancel()
        driveJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                val l = cmdL
                val r = cmdR
                if (l == 0 && r == 0) {
                    get("http://$host/drive?stop=1${auth()}")
                } else {
                    get("http://$host/drive?l=$l&r=$r${auth()}")
                }
                delay(intervalMs)
            }
        }
    }

    fun stopSending(host: String) {
        driveJob?.cancel()
        driveJob = null
        scope.launch(Dispatchers.IO) {
            get("http://$host/drive?stop=1${auth()}")
        }
    }

    private fun get(url: String) {
        var c: HttpURLConnection? = null
        try {
            c = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 3000
                readTimeout = 3000
                requestMethod = "GET"
            }
            val code = c.responseCode
            if (code in 200..299) {
                c.inputStream.use { it.readBytes() }
                report(true, null)
            } else {
                c.errorStream?.use { it.readBytes() }
                report(false, "HTTP $code")
            }
        } catch (e: Exception) {
            report(false, e.message)
        } finally {
            c?.disconnect()
        }
    }

    /** Сообщаем только при смене состояния — без спама на каждую посылку (120 мс). */
    private fun report(ok: Boolean, detail: String?) {
        if (lastOk == ok) return
        lastOk = ok
        val cb = onStatus ?: return
        cb(if (ok) "привод: ок" else "привод: нет связи" + (detail?.let { " ($it)" } ?: ""))
    }
}
