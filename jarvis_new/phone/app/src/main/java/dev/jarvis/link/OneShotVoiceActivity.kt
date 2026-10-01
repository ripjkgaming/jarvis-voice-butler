package dev.jarvis.link

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat

/**
 * Locked-screen one-shot voice command: the screen stays closed, Jarvis
 * says "recording", listens once (10s timeout), sends the text to the PC,
 * speaks the first reply line, and finishes. Nothing persists.
 */
class OneShotVoiceActivity : AppCompatActivity() {
    companion object {
        private const val LISTEN_TIMEOUT_MS = 10_000L
        private const val FINISH_AFTER_REPLY_MS = 5000L
    }

    private val ui = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var finished = false
    private lateinit var status: TextView
    private lateinit var transcript: TextView

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        WindowCompat.getInsetsController(window, window.decorView)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        setContentView(R.layout.activity_oneshot)
        status = findViewById(R.id.oneshot_status)
        transcript = findViewById(R.id.oneshot_text)
        TtsManager.init(this)
        HotwordGate.pttHeld = true
        Thread {
            if (Prefs(this).offlineMode) {
                sayAndFinish("Offline mode.", "Offline — reconnect in Settings.")
                return@Thread
            }
            TtsManager.speakBlocking("Recording.")
            if (finished) return@Thread
            runOnUiThread { status.text = "Listening…" }
            listenOnce()
        }.also { it.isDaemon = true; it.start() }
        ui.postDelayed({ if (!finished) fail("Timed out.") }, LISTEN_TIMEOUT_MS + 8000)
    }

    private fun listenOnce() {
        val rec = try {
            SpeechRecognizer.createSpeechRecognizer(this)
        } catch (_: Exception) {
            runOnUiThread { fail("Voice engine unavailable.") }
            return
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
                val text = r?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()?.trim().orEmpty()
                destroyRec()
                if (text.isEmpty()) fail("Didn't catch that.") else send(text)
            }

            override fun onError(e: Int) {
                destroyRec()
                fail("Didn't catch that.")
            }
        })
        try {
            rec.startListening(
                Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(
                        RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                        RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                    )
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
                }
            )
        } catch (_: Exception) {
            destroyRec()
            runOnUiThread { fail("Voice engine unavailable.") }
        }
    }

    private fun send(text: String) {
        runOnUiThread {
            status.text = "Thinking…"
            transcript.text = text
        }
        Thread {
            try {
                val res = LinkApi(Prefs(this)).chat(text)
                RemoteLauncher.handleAction(this, res.action)
                val reply = res.reply
                val line = TtsReply.firstLine(reply.ifEmpty { "(no reply)" })
                runOnUiThread {
                    status.text = "Jarvis"
                    transcript.text = "$text\n\n$line"
                }
                TtsManager.speak(line)
                Prefs(this).let { it.eventCursor = it.eventCursor + 1 }
            } catch (e: Exception) {
                runOnUiThread { fail("Error: ${e.message}") }
                return@Thread
            }
            ui.postDelayed({ finishOk() }, FINISH_AFTER_REPLY_MS)
        }.also { it.isDaemon = true; it.start() }
    }

    private fun sayAndFinish(say: String, show: String) {
        runOnUiThread {
            status.text = "Jarvis"
            transcript.text = show
        }
        TtsManager.speak(say)
        ui.postDelayed({ finishOk() }, 3000)
    }

    private fun fail(msg: String) {
        if (finished) return
        status.text = "Jarvis"
        transcript.text = msg
        TtsManager.speak("Sorry, Sir.")
        ui.postDelayed({ finishOk() }, 3000)
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
}
