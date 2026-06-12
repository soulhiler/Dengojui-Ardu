package com.denzhogzhuy.xiaorobot

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Поиск платы по mDNS/NSD: сервис _http._tcp с именем, содержащим "xiao"
 * (прошивка: MDNS.begin("xiao-cam") + MDNS.addService("http","tcp",80)).
 * Возвращает текущий IPv4 платы — DHCP-адрес больше не нужно знать/вводить.
 *
 * NsdManager.resolveService устарел в API 34, но это совместимый путь для
 * minSdk 26..target 35; предупреждение компиляции не критично.
 */
class BoardDiscovery(
    context: Context,
    private val onStatus: (String) -> Unit,
) {
    private val appCtx = context.applicationContext
    private val nsd = appCtx.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val main = Handler(Looper.getMainLooper())
    private var listener: NsdManager.DiscoveryListener? = null
    private var lock: WifiManager.MulticastLock? = null
    private val done = AtomicBoolean(false)
    private val resolving = AtomicBoolean(false)

    /**
     * Ищет сервис до timeoutMs; onFound(ip, port) или onFail() — всегда на main-потоке, один раз.
     * nameFilter: "xiao-cam" — плата, "xiao-dash" — дашборд на ПК (раздаёт APK на :8897).
     */
    fun find(
        timeoutMs: Long,
        onFound: (String, Int) -> Unit,
        onFail: () -> Unit,
        nameFilter: String = "xiao-cam",
    ) {
        done.set(false)
        resolving.set(false)
        acquireLock()

        fun finish(ok: Boolean, ip: String?, port: Int) {
            if (done.compareAndSet(false, true)) {
                stop()
                main.post { if (ok && ip != null) onFound(ip, port) else onFail() }
            }
        }
        main.postDelayed({ finish(false, null, 0) }, timeoutMs)

        val dl = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                finish(false, null, 0)
            }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onServiceLost(serviceInfo: NsdServiceInfo) {}
            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (done.get()) return
                val name = serviceInfo.serviceName ?: ""
                if (!name.lowercase().contains(nameFilter)) return
                // Один resolve за раз (ограничение NsdManager до API 34).
                if (!resolving.compareAndSet(false, true)) return
                nsd.resolveService(serviceInfo, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(si: NsdServiceInfo, errorCode: Int) {
                        resolving.set(false)
                    }
                    override fun onServiceResolved(si: NsdServiceInfo) {
                        val ip = si.host?.hostAddress
                        if (ip != null) finish(true, ip, si.port) else resolving.set(false)
                    }
                })
            }
        }
        listener = dl
        try {
            nsd.discoverServices("_http._tcp.", NsdManager.PROTOCOL_DNS_SD, dl)
        } catch (e: Exception) {
            onStatus("mDNS: ошибка (${e.message})")
            finish(false, null, 0)
        }
    }

    /** Останавливает поиск и снимает multicast-lock. Идемпотентно. */
    fun stop() {
        listener?.let {
            try {
                nsd.stopServiceDiscovery(it)
            } catch (_: Exception) {
            }
        }
        listener = null
        releaseLock()
    }

    private fun acquireLock() {
        try {
            val wifi = appCtx.getSystemService(Context.WIFI_SERVICE) as WifiManager
            lock = wifi.createMulticastLock("xiao-nsd").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Exception) {
        }
    }

    private fun releaseLock() {
        try {
            lock?.let { if (it.isHeld) it.release() }
        } catch (_: Exception) {
        }
        lock = null
    }
}
