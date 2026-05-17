package com.denzhogzhuy.xiaorobot

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.BufferedInputStream
import java.net.InetSocketAddress
import java.net.Socket

/** PCM s16le mono 16 kHz с TCP :81 на плате. */
class MicPlayer(
    private val scope: CoroutineScope,
    private val onStatus: (String) -> Unit,
) {
    private var job: Job? = null
    private var track: AudioTrack? = null
    @Volatile private var sock: Socket? = null

    fun start(host: String) {
        stop()
        job = scope.launch(Dispatchers.IO) {
            var backoff = 800L
            while (isActive) {
                try {
                    onStatus("мик: подключение…")
                    val socket = Socket().apply {
                        connect(InetSocketAddress(host, 81), 1500)
                        soTimeout = 2000
                    }
                    sock = socket
                    val minBuf = AudioTrack.getMinBufferSize(
                        16000,
                        AudioFormat.CHANNEL_OUT_MONO,
                        AudioFormat.ENCODING_PCM_16BIT,
                    )
                    val at = AudioTrack.Builder()
                        .setAudioAttributes(
                            AudioAttributes.Builder()
                                .setUsage(AudioAttributes.USAGE_MEDIA)
                                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                                .build(),
                        )
                        .setAudioFormat(
                            AudioFormat.Builder()
                                .setSampleRate(16000)
                                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                                .build(),
                        )
                        .setBufferSizeInBytes(minBuf.coerceAtLeast(4096) * 4)
                        .setTransferMode(AudioTrack.MODE_STREAM)
                        .build()
                    track = at
                    at.play()
                    onStatus("мик: воспроизведение")
                    backoff = 800L
                    val inp = BufferedInputStream(socket.getInputStream())
                    val buf = ByteArray(4096)
                    while (isActive) {
                        val got = inp.read(buf)
                        if (got <= 0) break
                        at.write(buf, 0, got)
                    }
                } catch (e: Exception) {
                    if (isActive) onStatus("мик: переподключение (${e.message})")
                } finally {
                    try { track?.stop() } catch (_: Exception) {}
                    track?.release()
                    track = null
                    try { sock?.close() } catch (_: Exception) {}
                    sock = null
                }
                if (!isActive) break
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(4000L)
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        try { sock?.close() } catch (_: Exception) {}
        sock = null
        try { track?.stop() } catch (_: Exception) {}
        track?.release()
        track = null
    }
}
