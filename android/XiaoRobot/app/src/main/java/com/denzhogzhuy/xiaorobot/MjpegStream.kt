package com.denzhogzhuy.xiaorobot

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.BufferedInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Читает MJPEG с http://host/stream (multipart/x-mixed-replace).
 *
 * Производительность (иначе джойстик лагал и приложение зависало):
 *  - буфер компактится на месте (System.arraycopy), без acc = acc + ... ,
 *    который пересоздавал весь массив на каждое чтение (O(n²), GC-шторм);
 *  - «последний кадр побеждает»: пока UI-поток рисует кадр, новые кадры
 *    декодируются, но НЕ постятся в Main — иначе очередь Main забивалась
 *    и касания джойстика не успевали обрабатываться;
 *  - конечный readTimeout: мёртвый поток приводит к реконнекту, а не виснет.
 */
class MjpegStream(
    private val scope: CoroutineScope,
    private val onFrame: (Bitmap) -> Unit,
    private val onStatus: (String) -> Unit,
) {
    private var job: Job? = null
    private val frameInFlight = AtomicBoolean(false)

    fun start(host: String) {
        stop()
        val url = "http://$host/stream"
        job = scope.launch(Dispatchers.IO) {
            onStatus("видео: подключение…")
            while (isActive) {
                var conn: HttpURLConnection? = null
                try {
                    conn = (URL(url).openConnection() as HttpURLConnection).apply {
                        connectTimeout = 8000
                        readTimeout = 5000          // непрерывный MJPEG: 5 с тишины = разрыв
                        instanceFollowRedirects = true   // /stream :80 -> 302 :82
                        requestMethod = "GET"
                    }
                    if (conn.responseCode != 200) {
                        onStatus("видео: HTTP ${conn.responseCode}")
                        kotlinx.coroutines.delay(1500)
                        continue
                    }
                    onStatus("видео: поток OK")
                    readLoop(this, BufferedInputStream(conn.inputStream, 64 * 1024))
                } catch (e: Exception) {
                    if (isActive) {
                        onStatus("видео: ${e.message}")
                        kotlinx.coroutines.delay(1200)
                    }
                } finally {
                    conn?.disconnect()
                }
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        frameInFlight.set(false)
    }

    private suspend fun readLoop(
        scope: kotlinx.coroutines.CoroutineScope,
        input: BufferedInputStream,
    ) {
        val chunk = ByteArray(32 * 1024)
        var buf = ByteArray(96 * 1024)
        var len = 0

        while (scope.isActive) {
            val n = input.read(chunk)
            if (n <= 0) break

            // дорастить буфер при нехватке (редко — он компактится после каждого кадра)
            if (len + n > buf.size) {
                var ns = buf.size * 2
                while (ns < len + n) ns *= 2
                buf = buf.copyOf(ns)
            }
            System.arraycopy(chunk, 0, buf, len, n)
            len += n

            // найти ПОСЛЕДНИЙ целый кадр в [0, len); промежуточные пропускаем
            var i = 0
            var fStart = -1
            var fEnd = -1
            while (true) {
                val soi = indexOf(buf, 0xFF, 0xD8, i, len)
                if (soi < 0) break
                val eoi = indexOf(buf, 0xFF, 0xD9, soi + 2, len)
                if (eoi < 0) break
                fStart = soi
                fEnd = eoi + 2
                i = eoi + 2
            }

            if (fEnd >= 0) {
                // декодируем кадр только если UI-поток свободен (latest-wins)
                if (frameInFlight.compareAndSet(false, true)) {
                    val bmp = try {
                        BitmapFactory.decodeByteArray(buf, fStart, fEnd - fStart)
                    } catch (_: Throwable) {
                        null
                    }
                    if (bmp != null) {
                        scope.launch(Dispatchers.Main) {
                            onFrame(bmp)
                            frameInFlight.set(false)
                        }
                    } else {
                        frameInFlight.set(false)
                    }
                }
                // компакт: оставить «хвост» после последнего кадра в начале буфера
                val leftover = len - fEnd
                if (leftover > 0) {
                    System.arraycopy(buf, fEnd, buf, 0, leftover)
                }
                len = leftover
            } else if (len > 1024 * 1024) {
                // нет кадра в мегабайте — мусор/рассинхрон, сбрасываем
                len = 0
            }
        }
    }

    private fun indexOf(data: ByteArray, b0: Int, b1: Int, from: Int, to: Int): Int {
        var i = from
        val end = to - 1
        while (i < end) {
            if ((data[i].toInt() and 0xFF) == b0 && (data[i + 1].toInt() and 0xFF) == b1) {
                return i
            }
            i++
        }
        return -1
    }
}
