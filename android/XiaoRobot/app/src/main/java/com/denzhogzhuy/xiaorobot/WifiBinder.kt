package com.denzhogzhuy.xiaorobot

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/**
 * Привязка трафика приложения к Wi-Fi-интерфейсу.
 *
 * Нужно для подключения к роботу, когда его сеть БЕЗ интернета (SoftAP `XIAO-Robot`):
 * иначе Android при «Wi-Fi без интернета» гонит запросы приложения через мобильные
 * данные, и `192.168.4.1` недостижим (ошибка `failed to connect ... from /10.x.x.x`).
 *
 * `bindProcessToNetwork` роутит ВСЕ сокеты процесса (HTTP-управление, MJPEG :82,
 * микрофон :81, NSD) через выбранную сеть — поэтому достаточно вызвать один раз.
 */
object WifiBinder {

    private fun cm(context: Context): ConnectivityManager =
        context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    /** Привязать процесс к активной Wi-Fi-сети. true — привязка удалась (есть Wi-Fi). */
    fun bindToWifi(context: Context): Boolean {
        val c = cm(context)
        val wifi = c.allNetworks.firstOrNull { n ->
            c.getNetworkCapabilities(n)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        } ?: return false
        return c.bindProcessToNetwork(wifi)
    }

    /** Снять привязку — вернуть обычную маршрутизацию (после отключения от робота). */
    fun unbind(context: Context) {
        cm(context).bindProcessToNetwork(null)
    }
}
