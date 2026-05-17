package com.denzhogzhuy.xiaorobot

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Периодически читает /telemetry (Wi‑Fi + сенсоры привода). */
class TelemetryPoller(
    private val scope: CoroutineScope,
    private val onInfo: (wifiChannel: Int, rssi: Int, ssid: String) -> Unit,
    private val onSensors: ((String) -> Unit)? = null,
) {
    private var job: Job? = null

    fun start(host: String) {
        stop()
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                try {
                    val c = (URL("http://$host/telemetry").openConnection() as HttpURLConnection).apply {
                        connectTimeout = 4000
                        readTimeout = 4000
                        requestMethod = "GET"
                    }
                    if (c.responseCode == 200) {
                        val body = c.inputStream.bufferedReader().readText()
                        val j = JSONObject(body)
                        val ch = j.optInt("wifi_channel", 0)
                        val rssi = j.optInt("wifi_rssi", 0)
                        val ssid = j.optString("wifi_ssid", "")
                        val sensors = buildSensors(j)
                        scope.launch(Dispatchers.Main) {
                            onInfo(ch, rssi, ssid)
                            onSensors?.invoke(sensors)
                        }
                    }
                    c.disconnect()
                } catch (_: Exception) {
                }
                delay(2000)
            }
        }
    }

    /** Компактная сводка сенсоров привода (пусто, если привода нет в JSON). */
    private fun buildSensors(j: JSONObject): String {
        if (!j.has("drive_hw")) return ""
        val parts = mutableListOf<String>()
        val saf = j.optInt("drive_safety", 0)
        if (saf != 0) {
            val why = buildString {
                if (saf and 1 != 0) append("бампер")
                if (saf and 2 != 0) {
                    if (isNotEmpty()) append("+")
                    append("УЗ")
                }
            }
            parts += "СТОП ($why)"
        } else if (j.optInt("bumper", 0) != 0) {
            parts += "бампер"
        }
        val us = j.optInt("us_cm", 0)
        if (us > 0) parts += "УЗ ${us}см"
        if (j.has("enc_l")) parts += "enc ${j.optInt("enc_l", 0)}/${j.optInt("enc_r", 0)}"
        if (j.has("spd_l")) parts += "v ${j.optInt("spd_l", 0)}/${j.optInt("spd_r", 0)}"
        return parts.joinToString(" · ")
    }

    fun stop() {
        job?.cancel()
        job = null
    }
}
