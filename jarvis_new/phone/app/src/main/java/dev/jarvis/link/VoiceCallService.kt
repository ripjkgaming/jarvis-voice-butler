package dev.jarvis.link

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * Foreground microphone holder for the realtime voice call. Android 14+
 * kills mic access for backgrounded apps, so the call runs under this
 * `microphone`-type service from join until hangup (same posture as
 * LinkService/HotwordService). No audio logic here — the LiveKit Room in
 * [VoiceCallManager] owns the mic; this only keeps the process entitled.
 */
class VoiceCallService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                startFg()
                return START_STICKY
            }
        }
    }

    private fun startFg() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            nm.getNotificationChannel(CHANNEL) == null
        ) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Voice call", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val notif = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL)
                .setContentTitle("Jarvis voice call")
                .setContentText("Live — mic hot")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setOngoing(true)
                .build()
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
                .setContentTitle("Jarvis voice call")
                .setContentText("Live — mic hot")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setOngoing(true)
                .build()
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
        } else {
            @Suppress("DEPRECATION")
            startForeground(NOTIF_ID, notif)
        }
    }

    companion object {
        const val CHANNEL = "voice_call"
        const val NOTIF_ID = 44
        const val ACTION_STOP = "dev.jarvis.link.VOICE_STOP"

        fun start(ctx: Context) {
            val i = Intent(ctx, VoiceCallService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(i)
            } else {
                ctx.startService(i)
            }
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, VoiceCallService::class.java).setAction(ACTION_STOP))
        }
    }
}
