package com.denzhogzhuy.xiaorobot

import android.graphics.Bitmap
import android.graphics.Typeface
import android.os.Bundle
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.denzhogzhuy.xiaorobot.databinding.ActivityMainBinding
import org.json.JSONObject
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val mjpeg = MjpegStream(lifecycleScope, ::showFrame, ::setStatusPart)
    private val mic = MicPlayer(lifecycleScope, ::setStatusPart)
    private val drive = DriveClient(lifecycleScope)
    private val updater = AppUpdater(this, ::setStatusPart)
    private val telemetry = TelemetryPoller(
        lifecycleScope,
        onInfo = { ch, rssi, ssid ->
            wifiInfo = "Wi‑Fi ch$ch · ${rssi} dBm · $ssid"
            updateStatusLine()
        },
        onJson = ::onTelemetryJson,
    )

    private val discovery by lazy { BoardDiscovery(this, ::setStatusPart) }

    private var connected = false
    private var wifiInfo = ""
    private var tofInfo = ""
    private var driveInfo = ""
    private var micOn = false
    private var host: String = ""

    /** Мощность джойстика 40..255 и скважность «пения» 10..100 % — как слайдеры в веб-дашборде. */
    private var joyPower = 180
    private var audioGain = 10

    private val prefs by lazy {
        getSharedPreferences("xiao_robot", MODE_PRIVATE)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // targetSdk 35: edge-to-edge принудительный — без отступов контент
        // уезжает под статусбар и системную навигацию (поля/кнопки накладываются).
        val basePad = (12 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(basePad + bars.left, basePad + bars.top, basePad + bars.right, basePad + bars.bottom)
            insets
        }

        // По умолчанию — авто-поиск по mDNS (xiao-cam.local), IP знать не нужно.
        // Устаревший «прилипший» .17 сбрасываем на авто.
        val savedHost = prefs.getString("host", "xiao-cam.local") ?: "xiao-cam.local"
        binding.editIp.setText(if (savedHost == "192.168.9.17") "xiao-cam.local" else savedHost)

        binding.btnConnect.setOnClickListener {
            if (connected) disconnectAll() else connectAll()
        }
        binding.btnStop.setOnClickListener { sendStop() }
        binding.btnMic.setOnClickListener { toggleMic() }
        binding.btnUpdate.setOnClickListener { askUpdateHost() }

        bindJoystick(binding.joystick)
        bindJoystick(binding.joystickOverlay)

        setupTabs()
        setupControls()
    }

    private var motorInfo = ""

    /** Один обработчик на оба джойстика (главный экран и оверлей на телеметрии). */
    private fun bindJoystick(j: JoystickView) {
        j.onMove = { nx, ny ->
            val l = tankMix(nx, ny, left = true)
            val r = tankMix(nx, ny, left = false)
            drive.cmdL = l
            drive.cmdR = r
            motorInfo = "L=$l R=$r"
            updateStatusLine()
        }
        j.onRelease = {
            // НЕ останавливаем цикл отправки (drive.stopSending убивал driveJob,
            // и после первого касания джойстик переставал работать). Обнуляем
            // команды — живой цикл сам шлёт /drive?stop=1, пока джойстик отпущен.
            drive.cmdL = 0
            drive.cmdR = 0
            motorInfo = ""
            updateStatusLine()
        }
    }

    // --- Вкладки ---

    private fun setupTabs() {
        binding.bottomNav.setOnItemSelectedListener { item ->
            binding.pageRobot.visibility = if (item.itemId == R.id.tabRobot) android.view.View.VISIBLE else android.view.View.GONE
            binding.pageTelemetry.visibility = if (item.itemId == R.id.tabTelemetry) android.view.View.VISIBLE else android.view.View.GONE
            binding.pageControls.visibility = if (item.itemId == R.id.tabControls) android.view.View.VISIBLE else android.view.View.GONE
            // Джойстик-оверлей на телеметрии: рулить и смотреть drive_cmd_l/r одновременно.
            binding.joystickOverlay.visibility =
                if (item.itemId == R.id.tabTelemetry) android.view.View.VISIBLE else android.view.View.GONE
            if (item.itemId == R.id.tabTelemetry) {
                lastJson?.let { renderTelemetry(it) }
            }
            true
        }
        binding.bottomNav.selectedItemId = R.id.tabRobot
    }

    // --- Вкладка «Управление» ---

    /** true, пока выставляем тогглы из телеметрии — чтобы не слать /control обратно. */
    private var updatingSwitches = false

    private fun setupControls() {
        joyPower = prefs.getInt("joy_max_spd", 180).coerceIn(40, 255)
        audioGain = prefs.getInt("audio_gain", 10).coerceIn(10, 100)
        binding.seekPower.progress = joyPower
        binding.seekGain.progress = audioGain
        refreshSliderLabels()

        binding.seekPower.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, value: Int, fromUser: Boolean) {
                joyPower = value.coerceIn(40, 255)
                refreshSliderLabels()
            }
            override fun onStartTrackingTouch(sb: SeekBar?) {}
            override fun onStopTrackingTouch(sb: SeekBar?) {
                prefs.edit().putInt("joy_max_spd", joyPower).apply()
            }
        })
        binding.seekGain.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, value: Int, fromUser: Boolean) {
                audioGain = value.coerceIn(10, 100)
                refreshSliderLabels()
            }
            override fun onStartTrackingTouch(sb: SeekBar?) {}
            override fun onStopTrackingTouch(sb: SeekBar?) {
                prefs.edit().putInt("audio_gain", audioGain).apply()
            }
        })

        val switches = mapOf(
            binding.swWifi to "wifi",
            binding.swBle to "ble",
            binding.swCam to "cam",
            binding.swMic to "mic",
            binding.swDrive to "drive",
        )
        switches.forEach { (sw, key) ->
            sw.setOnCheckedChangeListener { _, isChecked ->
                if (updatingSwitches) return@setOnCheckedChangeListener
                if (!requireConnected()) return@setOnCheckedChangeListener
                drive.control(host, key, isChecked)
            }
        }

        binding.btnBeep.setOnClickListener { if (requireConnected()) drive.beep(host, audioGain) }
        binding.btnMelody1.setOnClickListener { if (requireConnected()) drive.melody(host, 1, audioGain) }
        binding.btnSayHi.setOnClickListener { if (requireConnected()) drive.melody(host, 9, audioGain) }
        binding.btnSoundStop.setOnClickListener { if (requireConnected()) drive.melody(host, 0, audioGain) }
    }

    private fun refreshSliderLabels() {
        binding.lblPower.text = "Мощность джойстика: $joyPower/255"
        binding.lblGain.text = "Пение (скважность): $audioGain%"
    }

    private fun requireConnected(): Boolean {
        if (!connected || host.isEmpty()) {
            Toast.makeText(this, "Сначала подключитесь", Toast.LENGTH_SHORT).show()
            return false
        }
        return true
    }

    // --- Вкладка «Телеметрия» (зеркало веб-дашборда) ---

    private var lastJson: JSONObject? = null

    /** Сводка — 10 основных показателей, как CORE_TELEM_MAIN в tools/xiao_serial_telemetry.py. */
    private val coreFields = listOf(
        "uptime_ms" to "Время с перезагрузки",
        "fw_version" to "Версия прошивки",
        "wifi_status" to "Wi‑Fi: статус",
        "wifi_ssid" to "Имя сети (SSID)",
        "wifi_ip" to "IP в сети",
        "wifi_rssi" to "Сигнал Wi‑Fi (RSSI)",
        "heap_free" to "Свободная куча (SRAM)",
        "psram_free_esp" to "Свободная PSRAM",
        "cam_frames_stream" to "Кадров MJPEG всего",
        "mic_dbfs" to "Уровень микрофона (dBFS)",
    )

    private val groupOrder = listOf(
        "Система и MCU", "Память", "Flash и OTA", "Wi-Fi", "Управление",
        "Bluetooth LE", "Точка доступа (AP)", "Камера", "Микрофон", "Датчики",
        "Привод", "Прочее",
    )

    /** Группа ключа — как groupOf() в веб-дашборде (без ПК-шных групп Прокси/USB). */
    private fun groupOf(k: String): String = when {
        k.startsWith("ctrl_") -> "Управление"
        k == "chip_temp_c" -> "Датчики"
        k.startsWith("ble_") -> "Bluetooth LE"
        k.startsWith("mic_") -> "Микрофон"
        k.startsWith("cam_") -> "Камера"
        k.startsWith("ap_") -> "Точка доступа (AP)"
        k.startsWith("tof_") -> "Датчики"
        k.startsWith("drive_") || k == "enc_l" || k == "enc_r" -> "Привод"
        k.startsWith("wifi_") -> "Wi-Fi"
        k.startsWith("part_") || k.startsWith("sketch_") || k.startsWith("flash_") -> "Flash и OTA"
        k.startsWith("heap_") || k.startsWith("psram_") || k == "stack_watermark" || k == "rtos_task_count" -> "Память"
        k.startsWith("uptime") || k.startsWith("micros") || k.startsWith("reset") || k.startsWith("led_") ||
            k.startsWith("fw_") || k.startsWith("chip_") || k.startsWith("cpu_") || k == "sdk" ||
            k.startsWith("core_") || k.startsWith("arduino") || k.startsWith("efuse") -> "Система и MCU"
        else -> "Прочее"
    }

    private fun onTelemetryJson(j: JSONObject) {
        lastJson = j
        // Базовая телеметрия на главном экране: ToF в статус-строку.
        tofInfo = if (j.optInt("tof_ok", 0) == 1) {
            val mm = j.optInt("tof_mm", 0)
            if (j.optInt("tof_valid", 0) == 1 && mm > 0) "ToF $mm мм" else "ToF —"
        } else ""
        // Почему моторы могут молчать — сразу в статус, не копаясь в телеметрии.
        driveInfo = when {
            j.optInt("drive_hw", 0) == 0 -> ""
            j.optInt("drive_enabled", 1) == 0 -> "⚠ привод ВЫКЛ (вкладка Управление)"
            j.optInt("drive_safety", 0) != 0 -> "⚠ рефлекс-стоп (бампер/УЗ)"
            j.optInt("drive_watchdog", 0) == 1 -> "⚠ watchdog: команды не доходят"
            else -> ""
        }
        updateStatusLine()
        syncSwitches(j)
        if (binding.pageTelemetry.visibility == android.view.View.VISIBLE) {
            renderTelemetry(j)
        }
    }

    private fun syncSwitches(j: JSONObject) {
        // Трогаем тумблер только если ключ реально пришёл — иначе optInt-дефолт
        // «сбрасывал» состояние (привод всегда выглядел выключенным).
        updatingSwitches = true
        if (j.has("ctrl_wifi")) binding.swWifi.isChecked = j.optInt("ctrl_wifi", 1) == 1
        if (j.has("ctrl_ble")) binding.swBle.isChecked = j.optInt("ctrl_ble", 0) == 1
        if (j.has("ctrl_cam")) binding.swCam.isChecked = j.optInt("ctrl_cam", 0) == 1
        if (j.has("ctrl_mic")) binding.swMic.isChecked = j.optInt("ctrl_mic", 0) == 1
        // Привод: в /telemetry состояние называется drive_enabled (ctrl_drive — только в ответе /control).
        if (j.has("drive_enabled")) binding.swDrive.isChecked = j.optInt("drive_enabled", 1) == 1
        updatingSwitches = false
    }

    private fun addTelemetryText(text: String, header: Boolean) {
        val tv = TextView(this)
        tv.text = text
        if (header) {
            tv.setTextColor(0xFF58A6FF.toInt())
            tv.textSize = 15f
            tv.setTypeface(null, Typeface.BOLD)
            tv.setPadding(0, dp(14), 0, dp(4))
        } else {
            tv.setTextColor(0xFFC9D1D9.toInt())
            tv.textSize = 12f
            tv.typeface = Typeface.MONOSPACE
            tv.setPadding(0, 0, 0, dp(2))
        }
        binding.telemetryList.addView(tv)
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun renderTelemetry(j: JSONObject) {
        binding.telemetryList.removeAllViews()

        addTelemetryText("Сводка — 10 основных показателей", header = true)
        coreFields.forEach { (key, label) ->
            if (j.has(key)) {
                addTelemetryText("$label: ${j.opt(key)}", header = false)
            }
        }

        val buckets = LinkedHashMap<String, MutableList<String>>()
        groupOrder.forEach { buckets[it] = mutableListOf() }
        val keys = j.keys().asSequence().toList().sorted()
        keys.forEach { k ->
            val g = groupOf(k)
            buckets.getOrPut(g) { mutableListOf() }.add("$k = ${j.opt(k)}")
        }
        buckets.forEach { (g, rows) ->
            if (rows.isEmpty()) return@forEach
            addTelemetryText(g, header = true)
            rows.forEach { addTelemetryText(it, header = false) }
        }
    }

    // --- Подключение ---

    private fun connectAll() {
        val field = binding.editIp.text?.toString()?.trim()
            ?.removePrefix("http://")?.removeSuffix("/") ?: ""
        prefs.edit().putString("host", if (field.isEmpty()) "xiao-cam.local" else field).apply()
        connected = true
        binding.btnConnect.text = getString(R.string.disconnect_session)

        val auto = field.isEmpty() ||
            field.equals("xiao-cam.local", ignoreCase = true) ||
            field.equals("auto", ignoreCase = true)
        if (auto) {
            setStatusPart("поиск платы по mDNS…")
            discovery.find(
                timeoutMs = 6000L,
                onFound = { ip ->
                    setStatusPart("плата найдена: $ip")
                    startSessions(ip)
                },
                onFail = {
                    val last = prefs.getString("last_ip", "") ?: ""
                    if (last.isNotEmpty()) {
                        setStatusPart("mDNS не нашёл — пробую $last")
                        startSessions(last)
                    } else {
                        connected = false
                        binding.btnConnect.text = getString(R.string.connect)
                        setStatusPart("плата не найдена. Включи плату или впиши IP")
                    }
                },
            )
        } else {
            startSessions(field)
        }
    }

    /** Запуск всех каналов на известном хосте/IP. */
    private fun startSessions(h: String) {
        host = h
        prefs.edit().putString("last_ip", h).apply()
        drive.enableBoard(h)
        mjpeg.start(h)
        drive.startSending(h)
        telemetry.start(h)
        setStatusPart("подключено к $h")
    }

    private fun disconnectAll() {
        discovery.stop()
        connected = false
        micOn = false
        binding.btnMic.text = getString(R.string.mic_on)
        binding.btnConnect.text = getString(R.string.connect)
        mjpeg.stop()
        mic.stop()
        telemetry.stop()
        wifiInfo = ""
        tofInfo = ""
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

    /** Обновление приложения с ПК (дашборд раздаёт APK по LAN). */
    private fun askUpdateHost() {
        // ПК обязан быть в той же подсети, что плата/телефон. Прилипший адрес из
        // другой подсети (например 192.168.9.18 после переезда сети на 192.168.1.x)
        // бесполезен — подставляем текущую подсеть платы, пользователь дописывает хвост.
        val lastIp = prefs.getString("last_ip", "") ?: ""
        val subnet = lastIp.substringBeforeLast('.', "").let { if (it.isEmpty()) "" else "$it." }
        var preset = prefs.getString("upd_host", "") ?: ""
        if (preset.isEmpty() || (subnet.isNotEmpty() && !preset.startsWith(subnet))) {
            preset = subnet
        }
        val input = android.widget.EditText(this).apply {
            setText(preset)
            hint = if (subnet.isNotEmpty()) "напр. ${subnet}18 (порт :8897 сам)" else "IP ПК[:8897]"
            setSelection(text.length)
        }
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle(getString(R.string.update_host_title))
            .setMessage("ПК и телефон — в одной Wi‑Fi. На ПК запусти дашборд:\npy -3 tools\\xiao_serial_telemetry.py\nIP ПК — команда ipconfig. APK кладётся в dist\\.")
            .setView(input)
            .setPositiveButton(getString(R.string.app_update)) { _, _ ->
                var h = input.text.toString().trim().removePrefix("http://").removeSuffix("/")
                if (h.isNotEmpty()) {
                    if (!h.contains(':')) h += ":8897"
                    prefs.edit().putString("upd_host", h).apply()
                    updater.checkAndInstall(h, lifecycleScope)
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun sendStop() {
        // Кнопка «Стоп» обнуляет команды; цикл отправки остаётся жить
        // (шлёт stop=1 каждые 120 мс) — джойстик продолжает работать.
        drive.cmdL = 0
        drive.cmdR = 0
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
            tofInfo.takeIf { it.isNotEmpty() },
            driveInfo.takeIf { it.isNotEmpty() },
            motorInfo.takeIf { it.isNotEmpty() },
        )
        runOnUiThread {
            binding.statusText.text = parts.joinToString(" · ")
        }
    }

    /** Дифференциальный привод: вперёд/назад + поворот; масштаб — слайдер «Мощность». */
    private fun tankMix(nx: Float, ny: Float, left: Boolean): Int {
        val max = joyPower
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
