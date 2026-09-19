package dev.jarvis.link

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView

/** On-phone text chat with Jarvis (POST /chat, history-aware). */
class ChatFragment : Fragment() {
    private data class Msg(val fromJarvis: Boolean, val text: String)
    private val messages = mutableListOf<Msg>()
    private lateinit var adapter: Adapter

    inner class Adapter : RecyclerView.Adapter<Adapter.Holder>() {
        inner class Holder(val view: TextView) : RecyclerView.ViewHolder(view)

        override fun getItemViewType(position: Int) = if (messages[position].fromJarvis) 1 else 0

        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder {
            val tv = TextView(parent.context).apply {
                setPadding(24, 16, 24, 16)
                textSize = 16f
            }
            return Holder(tv)
        }

        override fun onBindViewHolder(h: Holder, position: Int) {
            val m = messages[position]
            h.view.text = (if (m.fromJarvis) "Jarvis: " else "You: ") + m.text
            h.view.setBackgroundColor(if (m.fromJarvis) 0xFF0A1A33.toInt() else 0xFF12233D.toInt())
        }

        override fun getItemCount() = messages.size
    }

    override fun onResume() {
        super.onResume()
        // Always-on while chatting: screen never sleeps here.
        activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    override fun onPause() {
        activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        super.onPause()
    }

    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_chat, host, false)
        val list = v.findViewById<RecyclerView>(R.id.chat_list)
        val input = v.findViewById<EditText>(R.id.edit_chat)
        val send = v.findViewById<Button>(R.id.btn_chat_send)
        adapter = Adapter()
        list.layoutManager = LinearLayoutManager(requireContext()).apply { stackFromEnd = true }
        list.adapter = adapter
        if (messages.isEmpty()) {
            messages.add(Msg(true, "At your service, Sir."))
            adapter.notifyItemInserted(0)
        }
        send.setOnClickListener {

            val text = input.text.toString().trim()
            if (text.isEmpty()) return@setOnClickListener
            input.setText("")
            messages.add(Msg(false, text))
            adapter.notifyItemInserted(messages.size - 1)
            list.scrollToPosition(messages.size - 1)
            send.isEnabled = false
            Thread {
                try {
                    val hist = org.json.JSONArray()
                    for (m in messages.dropLast(1).takeLast(20)) {
                        hist.put(
                            org.json.JSONArray()
                                .put(if (m.fromJarvis) "jarvis" else "user")
                                .put(m.text)
                        )
                    }
                    val reply = LinkApi(Prefs(requireContext())).chat(text, hist).reply
                    activity?.runOnUiThread {
                        messages.add(Msg(true, reply))
                        adapter.notifyItemInserted(messages.size - 1)
                        list.scrollToPosition(messages.size - 1)
                        send.isEnabled = true
                    }
                } catch (e: Exception) {
                    activity?.runOnUiThread {
                        messages.add(Msg(true, "Error: ${e.message}"))
                        adapter.notifyItemInserted(messages.size - 1)
                        send.isEnabled = true
                    }
                }
            }.also { it.isDaemon = true; it.start() }
        }
        return v
    }
}
