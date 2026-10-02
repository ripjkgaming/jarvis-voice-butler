package dev.jarvis.link.device

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.LocationManager
import android.os.BatteryManager
import dev.jarvis.link.net.BatterySample

/** Battery level and charging state from the sticky battery broadcast. */
object AndroidBattery {
    fun sample(ctx: Context): BatterySample? = try {
        val i = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        sampleOf(i)
    } catch (_: Exception) {
        null
    }

    fun sampleOf(i: Intent?): BatterySample? {
        if (i == null) return null
        val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        if (level < 0 || scale <= 0) return null
        val st = i.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val charging = st == BatteryManager.BATTERY_STATUS_CHARGING || st == BatteryManager.BATTERY_STATUS_FULL
        return BatterySample((level * 100f / scale).toInt().coerceIn(0, 100), charging)
    }
}

/** Last-known location without Play Services; null when permission or fix is missing. */
object AndroidLocation {
    fun hasPermission(ctx: Context): Boolean =
        ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ctx.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    fun lastKnown(ctx: Context): Pair<Double, Double>? {
        if (!hasPermission(ctx)) return null
        val lm = ctx.getSystemService(LocationManager::class.java) ?: return null
        var best: android.location.Location? = null
        for (p in listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.PASSIVE_PROVIDER)) {
            val fix = try { lm.getLastKnownLocation(p) } catch (_: Exception) { null } ?: continue
            if (best == null || fix.time > best.time) best = fix
        }
        return best?.let { it.latitude to it.longitude }
    }
}
