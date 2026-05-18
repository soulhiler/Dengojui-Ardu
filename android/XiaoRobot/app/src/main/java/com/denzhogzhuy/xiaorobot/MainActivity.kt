package com.denzhogzhuy.xiaorobot

import android.graphics.Bitmap
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.denzhogzhuy.xiaorobot.databinding.ActivityMainBinding
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val mjpeg = MjpegStream(lifecycleScope, ::showFrame, ::setStatusPart)
    private val mic = MicPlayer(lifecycleScope, ::setStatusPart)
    private val drive = DriveClient(lifecycleScope, ::setStatusPart)
    private val telemetry = TelemetryPoller(
        lifecycleScope,
        onInfo = { ch, rssi, ssid ->
            wifiInfo = "Wi‑Fi ch$ch · ${rssi} dBm · $ssid"
            updateStatusLine()
        },
        onSensors = { s ->
            sensorInfo = s
            updateStatusLine()
        },
    )

    private var connected = false
    private var wifiInfo = ""
    private var sensorInfo = ""
    private var micOn = false
    private var host: String = ""

    private val prefs by lazy {
        getSharedPreferences("xiao_robot", MODE_PRIVATE)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Дефолт = реальный IP платы; устаревший сохранённый .17 авто-исправляем
        // (раньше дефолт был .17 и мог «прилипнуть» в prefs).
        val savedHost = prefs.getString("host", "192.168.9.18") ?: "192.168.9.18"
        binding.editIp.setText(if (savedHost == "192.168.9.17") "192.168.9.18" else savedHost)
        binding.editToken.setText(prefs.getString("token", ""))

        binding.btnConnect.setOnClickListener {
            if (connected) disconnectAll() else connectAll()
        }
        binding.btnStop.setOnClickListener { sendStop() }
        binding.btnMic.setOnClickListener { toggleMic() }

        binding.joystick.onMove = { nx, ny ->
            val l = tankMix(nx, ny, left = true)
            val r = tankMix(nx, ny, left = false)
            drive.cmdL = l
            drive.cmdR = r
            motorInfo = "L=$l R=$r"
            updateStatusLine()
        }
        binding.joystick.onRelease = {
            drive.cmdL = 0
            drive.cmdR = 0
            motorInfo = ""
            if (host.isNotEmpty()) drive.stopSending(host)
            updateStatusLine()
        }
    }

    private var motorInfo = ""

    private fun connectAll() {
        host = binding.editIp.text?.toString()?.trim()?.removePrefix("http://")?.removeSuffix("/")
            ?: ""
        if (host.isEmpty()) {
            Toast.makeText(this, "Введите IP платы", Toast.LENGTH_SHORT).show()
            return
        }
        val token = binding.editToken.text?.toString()?.trim() ?: ""
        drive.token = token
        prefs.edit().putString("host", host).putString("token", token).apply()
        connected = true
        binding.btnConnect.text = getString(R.string.disconnect_session)
        drive.enableBoard(host)
        mjpeg.start(host)
        drive.startSending(host)
        telemetry.start(host)
        setStatusPart("подключено к $host")
    }

    private fun disconnectAll() {
        host = binding.editIp.text?.toString()?.trim()?.removePrefix("http://")?.removeSuffix("/") ?: host
        connected = false
        micOn = false
        binding.btnMic.text = getString(R.string.mic_on)
        binding.btnConnect.text = getString(R.string.connect)
        mjpeg.stop()
        mic.stop()
        telemetry.stop()
        wifiInfo = ""
        sensorInfo = ""
        motorInfo = ""
        if (host.isNotEmpty()) drive.stopSending(host)
        setStatusPart("отключено")
    }

    private fun toggleMic() {
        if (!connected) {
            Toast.makeText(this, "Сначала подключитесь", Toast.LENGTH_SHORT).show()
            return
        }
        micOn = !micOn
        if (micOn) {
            mic.start(host)
            binding.btnMic.text = getString(R.string.mic_off)
        } else {
            mic.stop()
            binding.btnMic.text = getString(R.string.mic_on)
        }
    }

    private fun sendStop() {
        drive.cmdL = 0
        drive.cmdR = 0
        if (host.isNotEmpty()) drive.stopSending(host)
    }

    private fun showFrame(bmp: Bitmap) {
        binding.videoView.setImageBitmap(bmp)
    }

    private var statusLine = ""
    private fun setStatusPart(part: String) {
        statusLine = part
        updateStatusLine()
    }

    private fun updateStatusLine() {
        val parts = listOfNotNull(
            statusLine.takeIf { it.isNotEmpty() },
            wifiInfo.takeIf { it.isNotEmpty() },
            sensorInfo.takeIf { it.isNotEmpty() },
            motorInfo.takeIf { it.isNotEmpty() },
        )
        runOnUiThread {
            binding.statusText.text = parts.joinToString(" · ")
        }
    }

    /** Дифференциальный привод: вперёд/назад + поворот. */
    private fun tankMix(nx: Float, ny: Float, left: Boolean): Int {
        val max = 220
        val forward = (ny * max).roundToInt()
        val turn = (nx * max).roundToInt()
        val v = if (left) forward + turn else forward - turn
        return v.coerceIn(-255, 255)
    }

    override fun onDestroy() {
        disconnectAll()
        super.onDestroy()
    }
}
