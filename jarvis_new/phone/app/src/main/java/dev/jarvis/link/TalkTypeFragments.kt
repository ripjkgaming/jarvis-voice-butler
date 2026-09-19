package dev.jarvis.link

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.TextView
import androidx.fragment.app.Fragment

/** Push-to-talk: hold the button, release to send. Reply plays + shows. */
class TalkFragment : Fragment() {
    private val recorder = Audio16k.Recorder()
    private var orb: OrbView? = null

    override fun onResume() {
        super.onResume()
        // Always-on while talking: screen never sleeps here.
        activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    override fun onPause() {
        activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        super.onPause()
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_talk, host, false)
        val btn = v.findViewById<Button>(R.id.btn_ptt)
        val out = v.findViewById<TextView>(R.id.txt_talk_out)
        orb = v.findViewById(R.id.orb)
        btn.setOnTouchListener { _, ev ->
            when (ev.action) {
                MotionEvent.ACTION_DOWN -> {
                    out.text = "Listening…"
                    orb?.energy = 0.8f
                    if (!recorder.start()) out.text = "Mic unavailable"
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    btn.isEnabled = false
                    out.text = "Thinking…"
                    orb?.energy = 0.5f
                    val audio = recorder.stopToBase64()
                    Thread {
                        try {
                            val r = LinkApi(Prefs(requireContext())).talk(audio)
                            val show = buildString {
                                append("You: ").append(r.transcript).append("\n\n")
                                append("Jarvis: ").append(
                                    r.reply.ifEmpty { r.warning ?: "(no reply)" }
                                )
                            }
                            activity?.runOnUiThread {
                                out.text = show
                                btn.isEnabled = true
                                orb?.energy = if (r.audioB64.isNotEmpty()) 1.0f else 0.35f
                                if (r.audioB64.isNotEmpty()) {
                                    try {
                                        Audio16k.playPcm16(r.audioB64, r.audioRate)
                                    } catch (_: Exception) { }
                                }
                            }
                        } catch (e: Exception) {
                            activity?.runOnUiThread {
                                out.text = "Error: ${e.message}"
                                btn.isEnabled = true
                            }
                        }
                    }.also { it.isDaemon = true; it.start() }
                    true
                }
                else -> false
            }
        }
        return v
    }
}

/** Remote keyboard: type text or press keys on the PC. */
class TypeFragment : Fragment() {
    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_type, host, false)
        val input = v.findViewById<android.widget.EditText>(R.id.edit_type)
        val status = v.findViewById<TextView>(R.id.txt_type_status)
        v.findViewById<Button>(R.id.btn_send).setOnClickListener {
            val text = input.text.toString()
            if (text.isEmpty()) {
                status.text = "Type something first"
                return@setOnClickListener
            }
            status.text = "Typing…"
            Thread {
                try {
                    LinkApi(Prefs(requireContext())).typeText(text)
                    activity?.runOnUiThread { status.text = "Sent" }
                } catch (e: Exception) {
                    activity?.runOnUiThread { status.text = "Error: ${e.message}" }
                }
            }.also { it.isDaemon = true; it.start() }
        }
        val keys = mapOf(
            R.id.key_enter to "Enter", R.id.key_esc to "Escape", R.id.key_tab to "Tab",
            R.id.key_up to "Up", R.id.key_down to "Down",
            R.id.key_left to "Left", R.id.key_right to "Right",
        )
        for ((id, name) in keys) {
            v.findViewById<Button>(id).setOnClickListener {
                status.text = "Key $name…"
                Thread {
                    try {
                        LinkApi(Prefs(requireContext())).pressKey(name)
                        activity?.runOnUiThread { status.text = "Sent $name" }
                    } catch (e: Exception) {
                        activity?.runOnUiThread { status.text = "Error: ${e.message}" }
                    }
                }.also { it.isDaemon = true; it.start() }
            }
        }
        return v
    }
}
