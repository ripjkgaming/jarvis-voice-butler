package dev.jarvis.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TtsReplyTest {
    @Test
    fun firstLineOnly() {
        assertEquals("Hello, Sir.", TtsReply.firstLine("Hello, Sir.\nSecond line here."))
        assertEquals("Only line.", TtsReply.firstLine("\n  Only line.  \n"))
        assertEquals("", TtsReply.firstLine("   \n  \n"))
        assertEquals("", TtsReply.firstLine(""))
    }

    @Test
    fun capped() {
        val long = "x".repeat(500)
        val out = TtsReply.firstLine(long)
        assertEquals(TtsReply.MAX_CHARS, out.length)
        assertTrue(out.all { it == 'x' })
    }
}

class TabsTest {
    @Test
    fun tenTabsInSpecOrder() {
        // Flutter parity tabs (Voice/Chat/Control) first, then every v1.3 tab kept.
        assertEquals(
            listOf(
                "Home", "Voice", "Chat", "Control",
                "Activity", "Screens", "Approvals", "Phone", "Alerts", "Settings",
            ),
            Tabs13.NAMES,
        )
    }
}
