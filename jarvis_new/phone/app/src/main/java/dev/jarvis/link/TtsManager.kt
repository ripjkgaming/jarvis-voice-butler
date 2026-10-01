package dev.jarvis.link

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * On-device spoken replies: Jarvis speaks the first reply line.
 * Singleton over the platform TextToSpeech engine (no extra deps).
 * Honors [Prefs.ttsEnabled]; [Prefs.ttsMale] prefers a male English voice
 * and otherwise drops pitch for a focus-friendly tone.
 */
object TtsManager {
    private var tts: TextToSpeech? = null
    private var appCtx: Context? = null
    private const val UTTER = "jarvis-tts"

    @Synchronized
    fun init(ctx: Context) {
        if (tts != null) return
        appCtx = ctx.applicationContext
        tts = TextToSpeech(appCtx) { applyVoice() }
    }

    @Synchronized
    private fun applyVoice() {
        val engine = tts ?: return
        val prefs = appCtx?.let { Prefs(it) }
        engine.language = Locale.US
        if (prefs?.ttsMale == true) {
            val male = try {
                engine.voices?.firstOrNull {
                    it.locale.language == "en" && it.name.contains("male", ignoreCase = true)
                }
            } catch (_: Exception) {
                null
            }
            if (male != null) {
                try {
                    engine.voice = male
                } catch (_: Exception) { }
                engine.setPitch(1.0f)
            } else {
                engine.setPitch(0.85f)
            }
        } else {
            engine.setPitch(1.0f)
        }
        engine.setSpeechRate(0.95f)
    }

    /** Speak [text]'s first line if TTS is enabled. Never throws. */
    fun speak(text: String) {
        try {
            val ctx = appCtx ?: return
            if (!Prefs(ctx).ttsEnabled) return
            val line = TtsReply.firstLine(text)
            if (line.isEmpty()) return
            val engine = tts ?: return
            engine.speak(line, TextToSpeech.QUEUE_FLUSH, null, UTTER)
        } catch (_: Exception) { }
    }

    /**
     * Blocking speak used by the lock-screen one-shot: waits for the
     * utterance to finish (or [timeoutMs]) so "recording" is heard before
     * the mic opens. Worker threads only.
     */
    fun speakBlocking(text: String, timeoutMs: Long = 6000) {
        try {
            val ctx = appCtx ?: return
            if (!Prefs(ctx).ttsEnabled) return
            val line = TtsReply.firstLine(text)
            if (line.isEmpty()) return
            val engine = tts ?: return
            val done = CountDownLatch(1)
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(id: String?) = Unit
                override fun onDone(id: String?) {
                    done.countDown()
                }

                override fun onError(id: String?) {
                    done.countDown()
                }
            })
            engine.speak(line, TextToSpeech.QUEUE_FLUSH, null, UTTER)
            done.await(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (_: Exception) { }
    }

    @Synchronized
    fun shutdown() {
        try { tts?.shutdown() } catch (_: Exception) { }
        tts = null
        appCtx = null
    }
}
