package dev.jarvis.link.device

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import dev.jarvis.link.logic.Prefs
import dev.jarvis.link.logic.Speaker
import dev.jarvis.link.logic.TtsReply
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * On-device spoken replies: Jarvis speaks the first reply line. Wraps the
 * platform TextToSpeech engine. Honors [Prefs.ttsEnabled]; [Prefs.ttsMale]
 * prefers a male English voice and otherwise drops pitch.
 */
class TtsManager(context: Context, private val prefs: Prefs) : Speaker {
    private val appCtx = context.applicationContext
    private var tts: TextToSpeech? = null
    @Volatile private var ready = false

    @Synchronized
    fun init() {
        if (tts != null) return
        tts = TextToSpeech(appCtx) { status ->
            ready = status == TextToSpeech.SUCCESS
            if (ready) applyVoice()
        }
    }

    @Synchronized
    fun applyVoice() {
        val engine = tts ?: return
        if (!ready) return
        engine.language = Locale.US
        if (prefs.ttsMale) {
            val male = try {
                engine.voices?.firstOrNull {
                    it.locale.language == "en" && it.name.contains("male", ignoreCase = true) &&
                        !it.name.contains("female", ignoreCase = true)
                }
            } catch (_: Exception) {
                null
            }
            if (male != null) {
                try { engine.voice = male } catch (_: Exception) { }
                engine.setPitch(1.0f)
            } else {
                engine.setPitch(0.85f)
            }
        } else {
            engine.setPitch(1.0f)
        }
        engine.setSpeechRate(0.95f)
    }

    /** Speak the first line of [text] when TTS is enabled. Never throws. */
    override fun speak(text: String) {
        try {
            if (!prefs.ttsEnabled) return
            val line = TtsReply.firstLine(text)
            if (line.isEmpty()) return
            init()
            val engine = tts ?: return
            if (!ready) return
            engine.speak(line, TextToSpeech.QUEUE_FLUSH, null, UTTER)
        } catch (_: Exception) { }
    }

    /** Blocks until the line is spoken (or [timeoutMs]). Worker threads only. */
    fun speakBlocking(text: String, timeoutMs: Long = 6000) {
        try {
            if (!prefs.ttsEnabled) return
            val line = TtsReply.firstLine(text)
            if (line.isEmpty()) return
            init()
            val engine = tts ?: return
            // Wait briefly for engine start-up on a cold process.
            var waited = 0
            while (!ready && waited < 2000) { Thread.sleep(100); waited += 100 }
            if (!ready) return
            val done = CountDownLatch(1)
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(id: String?) = Unit
                override fun onDone(id: String?) { done.countDown() }
                @Deprecated("Deprecated in Java")
                override fun onError(id: String?) { done.countDown() }
            })
            engine.speak(line, TextToSpeech.QUEUE_FLUSH, null, UTTER)
            done.await(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (_: Exception) { }
    }

    @Synchronized
    fun shutdown() {
        try { tts?.shutdown() } catch (_: Exception) { }
        tts = null
        ready = false
    }

    private companion object {
        const val UTTER = "jarvis-tts"
    }
}
