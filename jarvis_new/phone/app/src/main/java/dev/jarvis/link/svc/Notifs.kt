package dev.jarvis.link.svc

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import dev.jarvis.link.R
import dev.jarvis.link.ui.MainActivity

/** Notification channels and ids in one place, so ids never collide. */
object Notifs {
    const val CH_LINK = "jarvis_link_status"
    const val CH_WAKE = "jarvis_link_wake"
    const val CH_CALL = "voice_call"
    const val CH_HOTWORD = "jarvis_hotword"
    const val CH_APPROVALS = "jarvis_approvals"
    const val CH_SOS = "jarvis_sos"

    const val ID_LINK = 1
    const val ID_WAKE = 2
    const val ID_HOTWORD = 3
    const val ID_CALL = 4
    const val ID_REMOTE = 5
    const val ID_BOOT = 6
    const val ID_SOS = 7
    const val ID_APPROVAL_BASE = 100

    fun createChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        fun ch(id: String, name: String, imp: Int) = nm.createNotificationChannel(NotificationChannel(id, name, imp))
        ch(CH_LINK, "Link status", NotificationManager.IMPORTANCE_LOW)
        ch(CH_WAKE, "Wake and remote", NotificationManager.IMPORTANCE_HIGH)
        ch(CH_CALL, "Voice call", NotificationManager.IMPORTANCE_LOW)
        ch(CH_HOTWORD, "Hotword", NotificationManager.IMPORTANCE_LOW)
        ch(CH_APPROVALS, "Approvals", NotificationManager.IMPORTANCE_HIGH)
        ch(CH_SOS, "SOS", NotificationManager.IMPORTANCE_HIGH)
    }

    fun openApp(ctx: Context, action: String? = null): PendingIntent {
        val i = Intent(ctx, MainActivity::class.java).apply {
            if (action != null) setAction(action)
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        }
        return PendingIntent.getActivity(ctx, action.hashCode(), i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    fun ongoing(ctx: Context, channel: String, title: String, text: String): Notification =
        NotificationCompat.Builder(ctx, channel)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(openApp(ctx))
            .setOngoing(true)
            .build()

    /**
     * Enter the foreground as a microphone service. False when the system
     * refused (missing permission, background start): the caller must stop.
     */
    fun startMicForeground(svc: android.app.Service, id: Int, n: Notification): Boolean = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            svc.startForeground(id, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            svc.startForeground(id, n)
        }
        true
    } catch (_: Exception) {
        false
    }
}
