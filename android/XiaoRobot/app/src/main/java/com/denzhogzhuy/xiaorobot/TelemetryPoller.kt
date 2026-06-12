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

/** Периодически читает /telemetry: краткая строка для статуса + полный JSON для вкладки. */
class TelemetryPoller(
    private val scope: CoroutineScope,
    private val onInfo: (wifiChannel: Int, rssi: Int, ssid: String) -> Unit,
    private val onJson: (JSONObject) -> Unit = {},
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
                        scope.launch(Dispatchers.Main) {
                            onInfo(ch, rssi, ssid)
                            onJson(j)
                        }
                    }
                    c.disconnect()
                } catch (_: Exception) {
                }
                delay(2000)
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }
}
