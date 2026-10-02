package dev.jarvis.link.svc

import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.app.NotificationManager
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.device.Audio16k
import dev.jarvis.link.logic.HotwordController
import dev.jarvis.link.logic.HotwordStep

/**
 * On-device "Jarvis" hotword loop: a foreground service that restarts the
 * platform SpeechRecognizer and executes the decisions of
 * [HotwordController] (backoff, cooldown, mic arbitration). Every path
 * re-arms or stops; there is no silent dead loop.
 */
class HotwordService : Service() {
    private val ui = Handler(Looper.getMainLooper())
    private val controller = HotwordController()
    private var recognizer: SpeechRecognizer? = null
    private var dead = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val prefs = JarvisApp.graph(this).prefs
        if (!Notifs.startMicForeground(
                this, Notifs.ID_HOTWORD,
                Notifs.ongoing(this, Notifs.CH_HOTWORD, "Jarvis hotword", "Listening for \"Jarvis\""),
            )
        ) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (!prefs.hotwordEnabled) return stopWith("Hotword is turned off.")
        if (!Audio16k.hasMicPermission(this)) return stopWith("Microphone permission is missing.")
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            return stopWith("No speech recognition service on this phone.")
        }
        dead = false
        ui.removeCallbacksAndMessages(null)
        arm()
        return START_STICKY
    }

    private fun stopWith(reason: String): Int {
        try {
            getSystemService(NotificationManager::class.java).notify(
                Notifs.ID_HOTWORD, Notifs.ongoing(this, Notifs.CH_HOTWORD, "Jarvis hotword stopped", reason),
            )
        } catch (_: Exception) { }
        stopForeground(STOP_FOREGROUND_DETACH)
        stopSelf()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        dead = true
        ui.removeCallbacksAndMessages(null)
        teardown(recognizer)
        recognizer = null
        super.onDestroy()
    }

    private fun musicActive(): Boolean = try {
        (getSystemService(AUDIO_SERVICE) as AudioManager).isMusicActive
    } catch (_: Exception) {
        false
    }

    private fun apply(step: HotwordStep) {
        if (dead) return
        when (step) {
            is HotwordStep.Listen -> listen()
            is HotwordStep.Wait -> ui.postDelayed({ arm() }, step.ms)
            is HotwordStep.Restart -> ui.postDelayed({ arm() }, step.ms)
            is HotwordStep.Wake -> {
                teardown(recognizer)
                recognizer = null
                try { WakeRouter.route(this) } catch (_: Exception) { }
                ui.postDelayed({ arm() }, step.cooldownMs)
            }
            is HotwordStep.Stop -> {
                stopWith(step.reason)
            }
        }
    }

    private fun arm() {
        if (dead) return
        val prefs = JarvisApp.graph(this).prefs
        apply(controller.arm(prefs.hotwordEnabled, prefs.hotwordPauseOnMusic, musicActive()))
    }

    private fun listen() {
        val rec = try {
            SpeechRecognizer.createSpeechRecognizer(this)
        } catch (_: Exception) {
            apply(controller.startFailed())
            return
        }
        recognizer = rec
        rec.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(p: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(b: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onEvent(type: Int, p: Bundle?) = Unit
            override fun onPartialResults(r: Bundle?) {
                if (recognizer !== rec) return
                controller.heard(texts(r), final = false)?.let { teardown(rec); recognizer = null; apply(it) }
            }

            override fun onResults(r: Bundle?) {
                if (recognizer !== rec) return
                val step = controller.heard(texts(r), final = true) ?: return
                teardown(rec)
                recognizer = null
                apply(step)
            }

            override fun onError(error: Int) {
                if (recognizer !== rec) return
                teardown(rec)
                recognizer = null
                apply(controller.error(error))
            }
        })
        try {
            rec.startListening(
                Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
                    putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, packageName)
                },
            )
        } catch (_: Exception) {
            teardown(rec)
            recognizer = null
            apply(controller.startFailed())
        }
    }

    private fun texts(b: Bundle?): List<String> =
        b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION) ?: emptyList()

    private fun teardown(rec: SpeechRecognizer?) {
        try { rec?.cancel() } catch (_: Exception) { }
        try { rec?.destroy() } catch (_: Exception) { }
    }

    companion object {
        fun start(ctx: Context) {
            try {
                ctx.startForegroundService(Intent(ctx, HotwordService::class.java))
            } catch (_: Exception) { }
        }

        fun stop(ctx: Context) {
            try { ctx.stopService(Intent(ctx, HotwordService::class.java)) } catch (_: Exception) { }
        }
    }
}
