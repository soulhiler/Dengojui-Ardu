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

/**
 * Читает MJPEG с http://host/stream (multipart/x-mixed-replace).
 */
class MjpegStream(
    private val scope: CoroutineScope,
    private val onFrame: (Bitmap) -> Unit,
    private val onStatus: (String) -> Unit,
) {
    private var job: Job? = null

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
                        // Конечный таймаут: если плата отдала заголовки, но кадры
                        // встали — не виснем, а переподключаемся (внешний цикл).
                        readTimeout = 6000
                        requestMethod = "GET"
                    }
                    if (conn.responseCode != 200) {
                        onStatus("видео: HTTP ${conn.responseCode}")
                        kotlinx.coroutines.delay(1500)
                        continue
                    }
                    onStatus("видео: поток OK")
                    readLoop(this, BufferedInputStream(conn.inputStream))
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
    }

    private suspend fun readLoop(
        scope: kotlinx.coroutines.CoroutineScope,
        input: BufferedInputStream,
    ) {
        val buffer = ByteArray(256 * 1024)
        var acc = ByteArray(0)
        while (scope.isActive) {
            val n = input.read(buffer)
            if (n <= 0) break
            acc = acc + buffer.copyOfRange(0, n)
            var soi = indexOf(acc, 0xFF, 0xD8)
            while (soi >= 0) {
                val eoi = indexOf(acc, 0xFF, 0xD9, soi + 2)
                if (eoi < 0) break
                val frame = acc.copyOfRange(soi, eoi + 2)
                val bmp = BitmapFactory.decodeByteArray(frame, 0, frame.size)
                if (bmp != null) {
                    scope.launch(Dispatchers.Main) { onFrame(bmp) }
                }
                acc = acc.copyOfRange(eoi + 2, acc.size)
                soi = indexOf(acc, 0xFF, 0xD8)
            }
            if (acc.size > 512 * 1024) {
                // Не теряем кадр, который ещё дочитывается: оставляем хвост от
                // последнего SOI; если маркера нет — сбрасываем накопившийся мусор.
                val lastSoi = lastIndexOfSoi(acc)
                acc = if (lastSoi >= 0) acc.copyOfRange(lastSoi, acc.size) else ByteArray(0)
            }
        }
    }

    private fun indexOf(data: ByteArray, b0: Int, b1: Int, from: Int = 0): Int {
        var i = from
        while (i < data.size - 1) {
            if ((data[i].toInt() and 0xFF) == b0 && (data[i + 1].toInt() and 0xFF) == b1) {
                return i
            }
            i++
        }
        return -1
    }

    /** Индекс последнего SOI (0xFF 0xD8) или -1. */
    private fun lastIndexOfSoi(data: ByteArray): Int {
        var i = data.size - 2
        while (i >= 0) {
            if ((data[i].toInt() and 0xFF) == 0xFF && (data[i + 1].toInt() and 0xFF) == 0xD8) {
                return i
            }
            i--
        }
        return -1
    }
}
