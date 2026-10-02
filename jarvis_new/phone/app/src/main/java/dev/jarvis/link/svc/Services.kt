package dev.jarvis.link.svc

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.speech.SpeechRecognizer
import androidx.core.app.NotificationCompat
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.R
import dev.jarvis.link.device.Audio16k
import dev.jarvis.link.device.TtsManager
import dev.jarvis.link.logic.Prefs
import dev.jarvis.link.logic.ServiceControl

/** Starts/stops the foreground services behind the Settings toggles and autostart. */
class AndroidServiceControl(
    private val ctx: Context,
    private val prefs: Prefs,
    private val tts: TtsManager,
) : ServiceControl {

    override fun applyMicUplink(on: Boolean): String? {
        if (!on) { stopLink(); return null }
        if (!Audio16k.hasMicPermission(ctx)) return "Grant the microphone permission first (Settings > Request permissions)."
        if (!prefs.config().configured) return "Set the PC address and token before enabling the mic uplink."
        return if (startLink()) null else "Android refused to start the mic service; open the app and try again."
    }

    override fun applyHotword(on: Boolean): String? {
        if (!on) { HotwordService.stop(ctx); return null }
        if (!Audio16k.hasMicPermission(ctx)) return "Grant the microphone permission first (Settings > Request permissions)."
        if (!SpeechRecognizer.isRecognitionAvailable(ctx)) return "This phone has no speech recognition service."
        HotwordService.start(ctx)
        return null
    }

    override fun refreshTts() = tts.applyVoice()

    private fun startLink(): Boolean = try {
        ctx.startForegroundService(Intent(ctx, LinkService::class.java))
        true
    } catch (_: Exception) {
        false
    }

    private fun stopLink() {
        try { ctx.stopService(Intent(ctx, LinkService::class.java)) } catch (_: Exception) { }
    }

    /** Start whatever the prefs ask for. Returns false if something could not start. */
    fun ensureRunning(): Boolean {
        var ok = true
        if (!Audio16k.hasMicPermission(ctx)) return !(prefs.micUplink || prefs.hotwordEnabled)
        if (prefs.micUplink && prefs.config().configured) ok = startLink() && ok
        if (prefs.hotwordEnabled && SpeechRecognizer.isRecognitionAvailable(ctx)) HotwordService.start(ctx)
        return ok
    }
}

/** Autostart after boot (gated by the autostart toggle, then per-service toggles). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val graph = JarvisApp.graph(context)
        if (!graph.prefs.autostart) return
        if (!graph.prefs.micUplink && !graph.prefs.hotwordEnabled) return
        // Android 14/15 forbid starting microphone foreground services from
        // BOOT_COMPLETED. Try; if refused, ask the user with one tap.
        val ok = try {
            graph.services.ensureRunning()
        } catch (_: Exception) {
            false
        }
        if (!ok || !Audio16k.hasMicPermission(context)) notifyTapToStart(context)
    }

    private fun notifyTapToStart(ctx: Context) {
        try {
            val n = NotificationCompat.Builder(ctx, Notifs.CH_WAKE)
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("JarvisLink needs a tap")
                .setContentText("Android blocks the microphone service at boot. Tap to start listening.")
                .setContentIntent(Notifs.openApp(ctx, LinkService.ACTION_START_SERVICES))
                .setAutoCancel(true)
                .build()
            ctx.getSystemService(NotificationManager::class.java).notify(Notifs.ID_BOOT, n)
        } catch (_: Exception) { }
    }
}
