package dev.jarvis.link.logic

import org.junit.Assert.assertEquals
import org.junit.Test

class TtsReplyTest {
    @Test
    fun firstNonBlankLineTrimmed() {
        assertEquals("hi there", TtsReply.firstLine("  \n  hi there  \nbye"))
        assertEquals("hello", TtsReply.firstLine("hello\nworld"))
    }

    @Test
    fun blankInputGivesEmpty() {
        assertEquals("", TtsReply.firstLine(""))
        assertEquals("", TtsReply.firstLine("   \n  \n "))
    }

    @Test
    fun cappedAt300() {
        val long = "a".repeat(500)
        val got = TtsReply.firstLine(long)
        assertEquals(300, got.length)
        assertEquals("a".repeat(300), got)
        assertEquals("a".repeat(300), TtsReply.firstLine("a".repeat(300)))
    }
}
