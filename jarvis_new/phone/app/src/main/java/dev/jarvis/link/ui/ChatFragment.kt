package dev.jarvis.link.ui

import android.os.Bundle
import android.view.View
import android.widget.EditText
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.logic.Role

class ChatFragment : Fragment(R.layout.fragment_chat) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.chat
        val input = v.findViewById<EditText>(R.id.chat_input)
        v.button(R.id.chat_btn_send).emphasis(Emphasis.PRIMARY)
        v.onClick(R.id.chat_btn_send) {
            m.send(input.text.toString())
            input.text.clear()
        }
        v.onClick(R.id.chat_btn_clear) { m.clear() }
        v.onClick(R.id.chat_btn_speak_laptop) {
            m.state.value.messages.lastOrNull { it.role == Role.JARVIS }?.let { m.speakOnLaptop(it.text) }
        }
        render(m.state) { s ->
            v.text(R.id.chat_log).text = formatMessages(s.messages)
            v.text(R.id.chat_notice).text = s.notice
            v.button(R.id.chat_btn_send).isEnabled = !s.busy
        }
    }
}
