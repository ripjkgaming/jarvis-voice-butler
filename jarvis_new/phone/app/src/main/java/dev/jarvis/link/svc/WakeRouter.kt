package dev.jarvis.link.svc

import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.core.app.NotificationCompat
import dev.jarvis.link.R
import dev.jarvis.link.logic.WakeRouting
import dev.jarvis.link.ui.MainActivity
import dev.jarvis.link.ui.OneShotVoiceActivity

/**
 * Executes a wake: locked screens get the one-shot command overlay, unlocked
 * screens open the app and start talking. Background activity starts are
 * blocked on Android 10+, so the target is also posted as a full-screen
 * notification (and tappable) so the wake is never silently lost.
 */
object WakeRouter {
    const val EXTRA_AUTO_TALK = "dev.jarvis.link.AUTO_TALK"

    fun route(ctx: Context) {
        buzz(ctx)
        val locked = try {
            ctx.getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true
        } catch (_: Exception) {
            false
        }
        val target = when (WakeRouting.decide(locked)) {
            WakeRouting.Target.ONE_SHOT ->
                Intent(ctx, OneShotVoiceActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            WakeRouting.Target.APP_AUTO_TALK ->
                Intent(ctx, MainActivity::class.java)
                    .setAction(LinkService.ACTION_WAKE)
                    .putExtra(EXTRA_AUTO_TALK, true)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        try {
            val pi = android.app.PendingIntent.getActivity(
                ctx, 77, target,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE,
            )
            val n = NotificationCompat.Builder(ctx, Notifs.CH_WAKE)
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Yes, Sir?")
                .setContentText("Tap to talk")
                .setContentIntent(pi)
                .setFullScreenIntent(pi, true)
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .build()
            ctx.getSystemService(NotificationManager::class.java).notify(Notifs.ID_WAKE, n)
        } catch (_: Exception) { }
        try {
            ctx.startActivity(target)
        } catch (_: Exception) { }
    }

    private fun buzz(ctx: Context) {
        try {
            val effect = VibrationEffect.createOneShot(120, VibrationEffect.DEFAULT_AMPLITUDE)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                ctx.getSystemService(VibratorManager::class.java)?.defaultVibrator?.vibrate(effect)
            } else {
                @Suppress("DEPRECATION")
                ctx.getSystemService(Vibrator::class.java)?.vibrate(effect)
            }
        } catch (_: Exception) { }
    }
}
