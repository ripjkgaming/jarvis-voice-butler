package dev.jarvis.link.svc

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import dev.jarvis.link.device.Audio16k

/**
 * Foreground microphone holder for the realtime voice call (Android 14+
 * kills mic access for backgrounded apps). No audio here: the LiveKit room
 * owns the mic. Not sticky: a call never survives process death, so the
 * service must not be resurrected without one.
 */
class VoiceCallService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val ok = Notifs.startMicForeground(
            this, Notifs.ID_CALL,
            Notifs.ongoing(this, Notifs.CH_CALL, "Jarvis voice call", "Call in progress, mic live"),
        )
        if (!ok) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_NOT_STICKY
    }

    companion object {
        /** Start from a visible activity only; silently ignored when not allowed. */
        fun start(ctx: Context) {
            if (!Audio16k.hasMicPermission(ctx)) return
            try {
                ctx.startForegroundService(Intent(ctx, VoiceCallService::class.java))
            } catch (_: Exception) { }
        }

        fun stop(ctx: Context) {
            try { ctx.stopService(Intent(ctx, VoiceCallService::class.java)) } catch (_: Exception) { }
        }
    }
}
