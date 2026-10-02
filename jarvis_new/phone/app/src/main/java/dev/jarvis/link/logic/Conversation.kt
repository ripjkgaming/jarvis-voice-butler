package dev.jarvis.link.logic

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONException

enum class Role { USER, JARVIS }

data class Message(val role: Role, val text: String, val ts: Long)

/**
 * The single chat log shared by Home, Chat, the assistant and the one-shot
 * wake. Persisted (bounded) so it survives process death; what we send to
 * the bridge as `history` is derived from it via [ChatHistory].
 */
class Conversation(
    private val prefs: Prefs,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val _messages = MutableStateFlow(load())
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    @Synchronized
    fun add(role: Role, text: String) {
        val t = text.trim()
        if (t.isEmpty()) return
        val next = (_messages.value + Message(role, t, clock())).takeLast(MAX)
        _messages.value = next
        prefs.conversation = encode(next)
    }

    @Synchronized
    fun clear() {
        _messages.value = emptyList()
        prefs.conversation = "[]"
    }

    /** Last 10 exchanges as [role, text] pairs for `POST /chat`. */
    fun history(): List<List<String>> =
        ChatHistory.historyOf(_messages.value.map { it.role.name to it.text })

    private fun load(): List<Message> = try {
        val arr = JSONArray(prefs.conversation)
        (0 until arr.length()).mapNotNull { i ->
            val a = arr.optJSONArray(i) ?: return@mapNotNull null
            val role = if (a.optString(0).equals("jarvis", ignoreCase = true)) Role.JARVIS else Role.USER
            val text = a.optString(1)
            if (text.isEmpty()) null else Message(role, text, a.optLong(2))
        }.takeLast(MAX)
    } catch (_: JSONException) {
        emptyList()
    }

    companion object {
        const val MAX = 100

        fun encode(list: List<Message>): String {
            val arr = JSONArray()
            list.forEach { arr.put(JSONArray().put(it.role.name.lowercase()).put(it.text).put(it.ts)) }
            return arr.toString()
        }
    }
}
