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

/** Периодически читает /telemetry и отдаёт компактный многострочный блок (оверлей). */
class TelemetryPoller(
    private val scope: CoroutineScope,
    private val onTelemetry: (String) -> Unit,
) {
    private var job: Job? = null

    fun start(host: String) {
        stop()
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                var text = ""
                try {
                    val c = (URL("http://$host/telemetry").openConnection() as HttpURLConnection).apply {
                        connectTimeout = 4000
                        readTimeout = 4000
                        requestMethod = "GET"
                    }
                    if (c.responseCode == 200) {
                        text = format(JSONObject(c.inputStream.bufferedReader().readText()))
                    }
                    c.disconnect()
                } catch (_: Exception) {
                }
                if (text.isNotEmpty()) {
                    scope.launch(Dispatchers.Main) { onTelemetry(text) }
                }
                delay(2000)
            }
        }
    }

    /** Берём только присутствующие поля — формат устойчив к версии прошивки. */
    private fun format(j: JSONObject): String {
        val lines = mutableListOf<String>()

        val fw = j.optString("fw_version", "")
        val temp = j.optDouble("chip_temp_c", Double.NaN)
        StringBuilder().apply {
            if (fw.isNotEmpty()) append("fw $fw b${j.optInt("fw_build", 0)}")
            if (!temp.isNaN()) {
                if (isNotEmpty()) append(" · ")
                append("%.0f°C".format(temp))
            }
            if (isNotEmpty()) lines += toString()
        }

        val ssid = j.optString("wifi_ssid", "")
        if (ssid.isNotEmpty()) {
            lines += "WiFi $ssid ${j.optInt("wifi_rssi", 0)}dBm ch${j.optInt("wifi_channel", 0)}"
        }
        val ip = j.optString("wifi_ip", "")
        if (ip.isNotEmpty() && ip != "0.0.0.0") lines += ip

        StringBuilder().apply {
            if (j.has("heap_free")) append("heap ${j.optInt("heap_free", 0) / 1024}k")
            if (j.has("mic_dbfs")) {
                if (isNotEmpty()) append(" · ")
                append("мик %.0fdBFS".format(j.optDouble("mic_dbfs", 0.0)))
            }
            if (isNotEmpty()) lines += toString()
        }

        if (j.has("cam_fail") || j.has("cam_frames_stream")) {
            lines += "cam fail${j.optInt("cam_fail", 0)} кадры${j.optInt("cam_frames_stream", 0)}"
        }

        if (j.optInt("drive_hw", 0) != 0) {
            lines += "привод L${j.optInt("drive_cmd_l", 0)} R${j.optInt("drive_cmd_r", 0)}" +
                " enc${j.optInt("enc_l", 0)}/${j.optInt("enc_r", 0)}" +
                " v${j.optInt("spd_l", 0)}/${j.optInt("spd_r", 0)}"
            val saf = j.optInt("drive_safety", 0)
            val safTxt = if (saf == 0) {
                "OK"
            } else {
                buildString {
                    append("STOP(")
                    if (saf and 1 != 0) append("бампер")
                    if (saf and 2 != 0) {
                        if (last() != '(') append("+")
                        append("УЗ")
                    }
                    append(")")
                }
            }
            val us = j.optInt("us_cm", 0)
            lines += "УЗ ${if (us > 0) "$us см" else "—"} · safety $safTxt" +
                if (j.optInt("bumper", 0) != 0) " · бампер" else ""
        }

        return lines.joinToString("\n")
    }

    fun stop() {
        job?.cancel()
        job = null
    }
}
