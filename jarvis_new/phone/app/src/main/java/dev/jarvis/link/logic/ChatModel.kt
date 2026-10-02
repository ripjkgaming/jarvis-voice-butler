package dev.jarvis.link.logic

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope

data class ChatState(
    val messages: List<Message> = emptyList(),
    val busy: Boolean = false,
    /** Status/error line shown under the log. */
    val notice: String = "",
)

/** Chat tab: history-aware conversation with Jarvis (`POST /chat`). */
class ChatModel(
    private val messenger: Messenger,
    private val conversation: Conversation,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
) : Model<ChatState>(ChatState(), scope, io) {

    init {
        launch { conversation.messages.collect { m -> update { it.copy(messages = m) } } }
    }

    fun send(text: String) {
        if (current.busy) return
        update { it.copy(busy = true, notice = "Jarvis is thinking...") }
        launch {
            when (val r = io { messenger.send(text, instant = false) }) {
                is Outcome.Ok -> update {
                    it.copy(
                        busy = false,
                        notice = when {
                            r.value.summoned -> "Voice call summoned; the answer comes spoken."
                            r.value.text.isEmpty() -> r.value.warning ?: ""
                            else -> r.value.warning ?: ""
                        },
                    )
                }
                is Outcome.Fail -> update { it.copy(busy = false, notice = r.message) }
            }
        }
    }

    /** Long-press a Jarvis bubble: have the laptop speak it. */
    fun speakOnLaptop(text: String) {
        launch {
            val msg = when (val r = io { messenger.speakOnLaptop(text) }) {
                is Outcome.Ok -> "Sent to the laptop to speak."
                is Outcome.Fail -> r.message
            }
            update { it.copy(notice = msg) }
        }
    }

    fun clear() {
        conversation.clear()
        update { it.copy(notice = "History cleared.") }
    }
}
