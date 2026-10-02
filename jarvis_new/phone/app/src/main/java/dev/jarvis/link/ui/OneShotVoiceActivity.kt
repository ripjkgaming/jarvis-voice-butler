package dev.jarvis.link.ui

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.R
import dev.jarvis.link.device.Audio16k
import dev.jarvis.link.logic.HotwordGate
import dev.jarvis.link.logic.Outcome
import dev.jarvis.link.logic.TtsReply
import kotlin.concurrent.thread

/**
 * Locked-screen one-shot voice command: says "recording", listens once,
 * sends the text to the PC (instant route, chat fallback), speaks the first
 * reply line and closes. Nothing persists beyond the shared conversation.
 */
class OneShotVoiceActivity : AppCompatActivity() {
    private val ui = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    @Volatile private var finished = false
    private lateinit var status: TextView
    private lateinit var transcript: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        setContentView(R.layout.activity_oneshot)
        status = findViewById(R.id.oneshot_status)
        transcript = findViewById(R.id.oneshot_text)
        val graph = JarvisApp.graph(this)
        HotwordGate.pttHeld = true
        val c = graph.config()
        when {
            c.offline -> return endWith("Offline mode.", "Offline. Reconnect in Settings.")
            c.problem() != null -> return endWith("Not linked.", c.problem()!!.message)
            !Audio16k.hasMicPermission(this) -> return endWith("No microphone access.", "Grant the microphone permission in the app.")
            !SpeechRecognizer.isRecognitionAvailable(this) -> return endWith("No speech engine.", "This phone has no speech recognition service.")
        }
        thread(isDaemon = true) {
            graph.tts.speakBlocking("Recording.")
            if (finished) return@thread
            runOnUiThread { status.text = "Listening..."; listenOnce() }
        }
        ui.postDelayed({ if (!finished) endWith("Timed out.", "Nothing heard.") }, LISTEN_MS + 8000)
    }

    private fun listenOnce() {
        val rec = try { SpeechRecognizer.createSpeechRecognizer(this) } catch (_: Exception) {
            endWith("Voice engine unavailable.", ""); return
        }
        recognizer = rec
        rec.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(p: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(r: Float) = Unit
            override fun onBufferReceived(b: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onEvent(t: Int, p: Bundle?) = Unit
            override fun onPartialResults(r: Bundle?) = Unit
            override fun onResults(r: Bundle?) {
                val text = r?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim().orEmpty()
                destroyRec()
                if (text.isEmpty()) endWith("Didn't catch that.", "") else send(text)
            }
            override fun onError(e: Int) { destroyRec(); endWith("Didn't catch that.", "") }
        })
        try {
            rec.startListening(
                Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
                },
            )
        } catch (_: Exception) {
            destroyRec(); endWith("Voice engine unavailable.", "")
        }
    }

    private fun send(text: String) {
        status.text = "Thinking..."
        transcript.text = text
        val graph = JarvisApp.graph(this)
        thread(isDaemon = true) {
            val r = graph.messenger.send(text, instant = true)
            runOnUiThread {
                when (r) {
                    is Outcome.Ok -> {
                        val line = TtsReply.firstLine(r.value.text.ifEmpty { r.value.warning ?: "Done." })
                        endWith("Jarvis", "$text\n\n$line", speak = false)
                    }
                    is Outcome.Fail -> endWith("Jarvis", r.message)
                }
            }
        }
    }

    private fun endWith(title: String, body: String, speak: Boolean = false) {
        if (finished) return
        status.text = title
        transcript.text = body
        if (speak) JarvisApp.graph(this).tts.speak(title)
        ui.postDelayed({ finishOk() }, 5000)
    }

    private fun finishOk() {
        if (finished) return
        finished = true
        finish()
    }

    private fun destroyRec() {
        try { recognizer?.cancel() } catch (_: Exception) { }
        try { recognizer?.destroy() } catch (_: Exception) { }
        recognizer = null
    }

    override fun onDestroy() {
        finished = true
        ui.removeCallbacksAndMessages(null)
        destroyRec()
        HotwordGate.pttHeld = false
        super.onDestroy()
    }

    private companion object {
        const val LISTEN_MS = 10_000L
    }
}
