package dev.jarvis.link.svc

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Vibrator
import android.os.VibrationEffect
import androidx.core.app.NotificationCompat
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.R
import dev.jarvis.link.logic.Approval
import dev.jarvis.link.logic.ApprovalNotifier
import dev.jarvis.link.logic.SosEffects

/** Shade notifications with one-tap Approve/Deny for [dev.jarvis.link.logic.ApprovalStore]. */
class AndroidApprovalNotifier(private val ctx: Context) : ApprovalNotifier {
    private val nm get() = ctx.getSystemService(NotificationManager::class.java)

    private fun pending(a: Approval, ok: Boolean): PendingIntent = PendingIntent.getBroadcast(
        ctx, a.id.toInt() * 2 + if (ok) 1 else 0,
        Intent(ctx, ApprovalReceiver::class.java).setAction(ApprovalReceiver.ACTION)
            .putExtra(ApprovalReceiver.EXTRA_ID, a.id).putExtra(ApprovalReceiver.EXTRA_OK, ok),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    override fun ask(a: Approval) {
        try {
            nm.notify(
                Notifs.ID_APPROVAL_BASE + (a.id % 800).toInt(),
                NotificationCompat.Builder(ctx, Notifs.CH_APPROVALS)
                    .setSmallIcon(R.drawable.ic_launcher_foreground)
                    .setContentTitle(a.title).setContentText(a.detail)
                    .setContentIntent(Notifs.openApp(ctx))
                    .addAction(0, "Deny", pending(a, false))
                    .addAction(0, "Approve", pending(a, true))
                    .setAutoCancel(true).build(),
            )
        } catch (_: Exception) { }
    }

    override fun cancel(id: Long) {
        try { nm.cancel(Notifs.ID_APPROVAL_BASE + (id % 800).toInt()) } catch (_: Exception) { }
    }

    override fun confirm(id: Long, approved: Boolean) {
        try {
            nm.notify(
                Notifs.ID_APPROVAL_BASE + 800 + (id % 99).toInt(),
                NotificationCompat.Builder(ctx, Notifs.CH_APPROVALS)
                    .setSmallIcon(R.drawable.ic_launcher_foreground)
                    .setContentTitle(if (approved) "Approved" else "Denied")
                    .setContentText("Request #$id ${if (approved) "approved" else "denied"}.")
                    .setAutoCancel(true).setTimeoutAfter(10_000).build(),
            )
        } catch (_: Exception) { }
    }
}

class ApprovalReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != ACTION) return
        val id = intent.getLongExtra(EXTRA_ID, -1)
        val ok = intent.getBooleanExtra(EXTRA_OK, false)
        val decided = JarvisApp.graph(ctx).approvals.decide(id, ok)
        if (!decided) {
            // Process restarted since the request was raised: say so instead of doing nothing.
            try {
                val nm = ctx.getSystemService(NotificationManager::class.java)
                nm.cancel(Notifs.ID_APPROVAL_BASE + (id % 800).toInt())
                nm.notify(
                    Notifs.ID_APPROVAL_BASE + 800 + (id % 99).toInt(),
                    NotificationCompat.Builder(ctx, Notifs.CH_APPROVALS)
                        .setSmallIcon(R.drawable.ic_launcher_foreground)
                        .setContentTitle("Request expired")
                        .setContentText("Request #$id is no longer pending.")
                        .setAutoCancel(true).setTimeoutAfter(10_000).build(),
                )
            } catch (_: Exception) { }
        }
    }

    companion object {
        const val ACTION = "dev.jarvis.link.APPROVAL_DECIDE"
        const val EXTRA_ID = "id"
        const val EXTRA_OK = "ok"
    }
}

/** SOS alarm: repeating vibration + persistent notification. (The screen flash is drawn by the SOS tab.) */
class AndroidSosEffects(private val ctx: Context) : SosEffects {
    override fun start() {
        try {
            ctx.getSystemService(Vibrator::class.java)
                ?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 400, 200, 400), 0))
        } catch (_: Exception) { }
        try {
            ctx.getSystemService(NotificationManager::class.java).notify(
                Notifs.ID_SOS,
                NotificationCompat.Builder(ctx, Notifs.CH_SOS)
                    .setSmallIcon(R.drawable.ic_launcher_foreground)
                    .setContentTitle("SOS ACTIVE")
                    .setContentText("Tap to open JarvisLink and stand down.")
                    .setContentIntent(Notifs.openApp(ctx))
                    .setOngoing(true).build(),
            )
        } catch (_: Exception) { }
    }

    override fun stop() {
        try { ctx.getSystemService(Vibrator::class.java)?.cancel() } catch (_: Exception) { }
        try { ctx.getSystemService(NotificationManager::class.java).cancel(Notifs.ID_SOS) } catch (_: Exception) { }
    }
}
