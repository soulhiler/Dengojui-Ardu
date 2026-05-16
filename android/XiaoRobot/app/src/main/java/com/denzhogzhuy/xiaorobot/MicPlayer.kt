package com.denzhogzhuy.xiaorobot

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.BufferedInputStream
import java.net.Socket

/** PCM s16le mono 16 kHz с TCP :81 на плате. */
class MicPlayer(
    private val scope: CoroutineScope,
    private val onStatus: (String) -> Unit,
) {
    private var job: Job? = null
    private var track: AudioTrack? = null

    fun start(host: String) {
        stop()
        job = scope.launch(Dispatchers.IO) {
            onStatus("мик: подключение…")
            try {
                val socket = Socket(host, 81)
                socket.soTimeout = 500
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
                val inp = BufferedInputStream(socket.getInputStream())
                val buf = ByteArray(4096)
                while (isActive) {
                    val got = inp.read(buf)
                    if (got <= 0) break
                    at.write(buf, 0, got)
                }
                socket.close()
            } catch (e: Exception) {
                if (isActive) onStatus("мик: ${e.message}")
            } finally {
                track?.stop()
                track?.release()
                track = null
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        track?.stop()
        track?.release()
        track = null
    }
}
