package dev.jarvis.link.logic

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationTest {
    private fun fresh(): Prefs = Prefs(MemoryStore())

    @Test
    fun addTrimsAndIgnoresBlank() {
        val c = Conversation(fresh())
        c.add(Role.USER, "  hello  ")
        assertEquals(1, c.messages.value.size)
        assertEquals("hello", c.messages.value[0].text)
        c.add(Role.USER, "   ")
        c.add(Role.JARVIS, "")
        assertEquals(1, c.messages.value.size)
    }

    @Test
    fun trimsToMaxBound() {
        val c = Conversation(fresh())
        for (i in 1..105) c.add(Role.USER, "m$i")
        val msgs = c.messages.value
        assertEquals(Conversation.MAX, msgs.size)
        assertEquals(100, msgs.size)
        assertEquals("m6", msgs.first().text)
        assertEquals("m105", msgs.last().text)
    }

    @Test
    fun persistenceRoundTrip() {
        val prefs = fresh()
        val c1 = Conversation(prefs)
        c1.add(Role.USER, "hi")
        c1.add(Role.JARVIS, "hello back")
        val c2 = Conversation(prefs)
        assertEquals(c1.messages.value, c2.messages.value)
        assertEquals("hi", c2.messages.value[0].text)
        assertEquals(Role.JARVIS, c2.messages.value[1].role)
    }

    @Test
    fun corruptJsonYieldsEmpty() {
        val prefs = fresh()
        prefs.conversation = "not json{{"
        assertTrue(Conversation(prefs).messages.value.isEmpty())
    }

    @Test
    fun historyLastTwentyOrderedAndNormalized() {
        val c = Conversation(fresh())
        for (i in 0..24) c.add(if (i % 2 == 0) Role.USER else Role.JARVIS, "m$i")
        val h = c.history()
        assertEquals(20, h.size)
        assertEquals(listOf("jarvis", "m5"), h.first())
        assertEquals(listOf("user", "m6"), h[1])
        assertEquals(listOf("user", "m24"), h.last())
        assertTrue(h.all { it[0] == "user" || it[0] == "jarvis" })
    }

    @Test
    fun clearEmptiesAndPersists() {
        val prefs = fresh()
        val c = Conversation(prefs)
        c.add(Role.USER, "hi")
        c.clear()
        assertTrue(c.messages.value.isEmpty())
        assertEquals("[]", prefs.conversation)
        assertTrue(Conversation(prefs).messages.value.isEmpty())
    }

    @Test
    fun historyOfDirect() {
        val log = (0..24).map { if (it % 2 == 0) "You" to "u$it" else "Jarvis" to "j$it" }
        val h = ChatHistory.historyOf(log)
        assertEquals(20, h.size)
        assertEquals(listOf("jarvis", "j5"), h.first())
        assertEquals(listOf("user", "u6"), h[1])
        assertEquals(listOf("user", "u24"), h.last())
    }

    @Test
    fun historyOfNormalizesRoles() {
        val h = ChatHistory.historyOf(listOf("JARVIS" to "a", "You" to "b", "weird" to "c", "user" to "d"))
        assertEquals(listOf("jarvis", "user", "user", "user"), h.map { it[0] })
        assertEquals(listOf("a", "b", "c", "d"), h.map { it[1] })
    }
}
