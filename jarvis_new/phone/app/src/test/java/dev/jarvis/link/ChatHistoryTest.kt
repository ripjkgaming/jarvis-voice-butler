package dev.jarvis.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Multi-turn history builder sent with every POST /chat. */
class ChatHistoryTest {
    @Test
    fun empty_isEmpty() {
        assertTrue(ChatHistory.historyOf(emptyList()).isEmpty())
    }

    @Test
    fun roles_normalizeToUserOrJarvis() {
        val out = ChatHistory.historyOf(
            listOf("user" to "hi", "jarvis" to "hello, Sir", "You" to "x", "JARVIS" to "y")
        )
        assertEquals(
            listOf(
                listOf("user", "hi"),
                listOf("jarvis", "hello, Sir"),
                listOf("user", "x"),
                listOf("jarvis", "y"),
            ),
            out,
        )
    }

    @Test
    fun trimsToLastTwenty_oldestFirst() {
        val log = (1..30).map { "user" to "m$it" }
        val out = ChatHistory.historyOf(log)
        assertEquals(20, out.size)
        assertEquals(listOf("user", "m11"), out.first())
        assertEquals(listOf("user", "m30"), out.last())
    }

    @Test
    fun exactlyTwenty_passesThrough() {
        val log = (1..20).map { "jarvis" to "r$it" }
        val out = ChatHistory.historyOf(log)
        assertEquals(20, out.size)
        assertEquals("r1", out.first()[1])
    }

    @Test
    fun toJson_matchesBridgeShape() {
        val arr = ChatHistory.toJson(listOf(listOf("user", "hi"), listOf("jarvis", "yo")))
        assertEquals(2, arr.length())
        assertEquals("user", arr.getJSONArray(0).getString(0))
        assertEquals("hi", arr.getJSONArray(0).getString(1))
        assertEquals("jarvis", arr.getJSONArray(1).getString(0))
    }
}
