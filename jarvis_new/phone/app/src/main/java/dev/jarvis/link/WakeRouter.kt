package dev.jarvis.link

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * Context-aware wake routing: locked screens stay closed (one-shot voice
 * command), unlocked screens pop the app open and start talking.
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
        if (locked) {
            ctx.startActivity(
                Intent(ctx, OneShotVoiceActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } else {
            ctx.startActivity(
                Intent(ctx, MainActivity::class.java)
                    .setAction(LinkService.ACTION_WAKE)
                    .putExtra(EXTRA_AUTO_TALK, true)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            )
        }
    }

    private fun buzz(ctx: Context) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                ctx.getSystemService(VibratorManager::class.java)
                    ?.defaultVibrator
                    ?.vibrate(VibrationEffect.createOneShot(120, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                ctx.getSystemService(Vibrator::class.java)
                    ?.vibrate(VibrationEffect.createOneShot(120, VibrationEffect.DEFAULT_AMPLITUDE))
            }
        } catch (_: Exception) { }
    }
}
