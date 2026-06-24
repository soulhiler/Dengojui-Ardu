package com.denzhogzhuy.xiaorobot

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.net.HttpURLConnection
import java.net.URL
/** GET /drive?l=&r= и /control?cam=1&mic=1 */
class DriveClient(private val scope: CoroutineScope) {
    private var driveJob: Job? = null
  @Volatile var cmdL: Int = 0
  @Volatile var cmdR: Int = 0
  /** Токен авторизации эндпоинтов управления (firmware XIAO_API_TOKEN). Пусто = не слать. */
  @Volatile var token: String = ""

    fun enableBoard(host: String) {
        scope.launch(Dispatchers.IO) {
            get("http://$host/control?cam=1&mic=1")
        }
    }

    fun startSending(host: String, intervalMs: Long = 120L) {
        driveJob?.cancel()
        driveJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                val l = cmdL
                val r = cmdR
                if (l == 0 && r == 0) {
                    get("http://$host/drive?stop=1")
                } else {
                    get("http://$host/drive?l=$l&r=$r")
                }
                delay(intervalMs)
            }
        }
    }

    fun stopSending(host: String) {
        driveJob?.cancel()
        driveJob = null
        scope.launch(Dispatchers.IO) {
            get("http://$host/drive?stop=1")
        }
    }

    /** Тогглы платы, как devBar в веб-дашборде: /control?wifi|ble|cam|mic|drive=0|1. */
    fun control(host: String, key: String, on: Boolean) {
        scope.launch(Dispatchers.IO) {
            get("http://$host/control?$key=${if (on) 1 else 0}")
        }
    }

    /** Гудок, как кнопка «Beep A» в вебе. gain — скважность «голоса» 10..100 %. */
    fun beep(host: String, gain: Int) {
        scope.launch(Dispatchers.IO) {
            get("http://$host/beep?hz=880&ms=250&ch=A&gain=$gain")
        }
    }

    /** Мелодия (id=1 — мелодия 1, id=9 — «привет», id=0 — стоп звука). */
    fun melody(host: String, id: Int, gain: Int) {
        scope.launch(Dispatchers.IO) {
            if (id == 0) {
                get("http://$host/melody?id=0")
            } else {
                get("http://$host/melody?id=$id&ch=A&gain=$gain")
            }
        }
    }

    private fun get(url: String) {
        var c: HttpURLConnection? = null
        try {
            c = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 3000
                readTimeout = 3000
                requestMethod = "GET"
                if (token.isNotEmpty()) setRequestProperty("X-Auth-Token", token)
            }
            c.inputStream.use { it.readBytes() }
        } catch (_: Exception) {
        } finally {
            c?.disconnect()
        }
    }
}
