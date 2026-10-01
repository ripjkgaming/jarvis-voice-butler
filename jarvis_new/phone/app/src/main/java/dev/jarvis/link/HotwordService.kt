package dev.jarvis.link

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
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
import androidx.core.app.NotificationCompat

/**
 * On-device "Jarvis" hotword loop: a persistent foreground service that
 * restarts the platform SpeechRecognizer, watches partial + final results
 * for the wake word, and routes hits via [WakeRouter]. No extra deps.
 *
 * Stands down while PTT/sessions hold the mic ([HotwordGate]) and while
 * music plays ([Prefs.hotwordPauseOnMusic]). Recognizer busy/server errors
 * back off exponentially ([Hotword.backoffMs]); no-match/timeout restarts
 * immediately. Every path re-arms or stops — never a dead loop.
 */
class HotwordService : Service() {
    companion object {
        const val CH = "jarvis_hotword"
        const val NOTIF = 3
        private const val COOLDOWN_AFTER_WAKE_MS = 5000L
        private const val GATE_RECHECK_MS = 2000L

        fun start(ctx: Context) {
            val i = Intent(ctx, HotwordService::class.java)
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                ctx.startForegroundService(i)
            } else {
                ctx.startService(i)
            }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, HotwordService::class.java))
        }
    }

    private val ui = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var failures = 0
    private var dead = false
    private var coolUntil = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        TtsManager.init(this)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CH, "Jarvis hotword", NotificationManager.IMPORTANCE_LOW)
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!Prefs(this).hotwordEnabled) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIF, notif("Listening for “Jarvis”…"))
        arm()
        return START_STICKY
    }

    override fun onDestroy() {
        dead = true
        ui.removeCallbacksAndMessages(null)
        try { recognizer?.destroy() } catch (_: Exception) { }
        recognizer = null
        super.onDestroy()
    }

    private fun notif(text: String) = NotificationCompat.Builder(this, CH)
        .setSmallIcon(android.R.drawable.ic_btn_speak_now)
        .setContentTitle("Jarvis hotword")
        .setContentText(text)
        .setContentIntent(
            PendingIntent.getActivity(
                this, 0, Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        )
        .setOngoing(true)
        .build()

    private fun musicActive(): Boolean = try {
        (getSystemService(AUDIO_SERVICE) as AudioManager).isMusicActive
    } catch (_: Exception) {
        false
    }

    private fun arm() {
        if (dead) return
        val prefs = Prefs(this)
        if (!prefs.hotwordEnabled) {
            stopSelf()
            return
        }
        if (System.currentTimeMillis() < coolUntil ||
            !HotwordGate.allowed(true, prefs.hotwordPauseOnMusic, musicActive())
        ) {
            ui.postDelayed({ arm() }, GATE_RECHECK_MS)
            return
        }
        val rec = try {
            SpeechRecognizer.createSpeechRecognizer(this)
        } catch (_: Exception) {
            stopSelf()
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
                if (heard(r)) onWake()
            }

            override fun onResults(r: Bundle?) {
                if (heard(r)) onWake() else relisten(rec)
            }

            override fun onError(error: Int) {
                teardown(rec)
                if (dead) return
                if (Hotword.needsBackoff(error)) failures++ else failures = 0
                ui.postDelayed({ arm() }, Hotword.backoffMs(failures).coerceAtLeast(500L))
            }
        })
        try {
            val i = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(
                    RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                )
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            }
            rec.startListening(i)
        } catch (_: Exception) {
            teardown(rec)
            ui.postDelayed({ arm() }, 2000L)
        }
    }

    private fun heard(results: Bundle?): Boolean {
        val list = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            ?: return false
        return list.any { Hotword.isWakeWord(it) }
    }

    private fun onWake() {
        failures = 0
        coolUntil = System.currentTimeMillis() + COOLDOWN_AFTER_WAKE_MS
        teardown(recognizer)
        recognizer = null
        try {
            WakeRouter.route(this)
        } catch (_: Exception) { }
        ui.postDelayed({ arm() }, COOLDOWN_AFTER_WAKE_MS)
    }

    private fun relisten(rec: SpeechRecognizer?) {
        teardown(rec)
        if (rec == recognizer) recognizer = null
        ui.post { arm() }
    }

    private fun teardown(rec: SpeechRecognizer?) {
        try { rec?.cancel() } catch (_: Exception) { }
        try { rec?.destroy() } catch (_: Exception) { }
    }
}
