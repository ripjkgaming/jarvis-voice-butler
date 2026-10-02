package dev.jarvis.link.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LimitsTest {
    @Test
    fun emptyGivesNoPieces() {
        assertEquals(emptyList<String>(), Limits.chunk(""))
    }

    @Test
    fun shorterThanMaxIsSinglePiece() {
        assertEquals(listOf("hello"), Limits.chunk("hello", 500))
    }

    @Test
    fun exactMaxIsSinglePiece() {
        val t = "x".repeat(Limits.TYPE_MAX)
        assertEquals(listOf(t), Limits.chunk(t))
    }

    @Test
    fun splitsAtWhitespace() {
        assertEquals(
            listOf("aa bb cc ", "dd ee ff ", "gg"),
            Limits.chunk("aa bb cc dd ee ff gg", 10),
        )
    }

    @Test
    fun noPieceLongerThanMaxAndJoinIsLossless() {
        val texts = listOf(
            "word ".repeat(200),
            "a ".repeat(50) + "b".repeat(600),
            "a b c d e f ".repeat(100),
        )
        for (t in texts) {
            val pieces = Limits.chunk(t, 500)
            assertTrue(pieces.all { it.length <= 500 })
            assertEquals(t, pieces.joinToString(""))
        }
    }

    @Test
    fun longTextWithNoSpacesSplitsEvenly() {
        val pieces = Limits.chunk("a".repeat(1200))
        assertEquals(listOf(500, 500, 200), pieces.map { it.length })
        assertEquals("a".repeat(1200), pieces.joinToString(""))
    }

    @Test
    fun doesNotSplitSurrogatePair() {
        val emoji = "\uD83D\uDE00" // 😀, a surrogate pair
        assertEquals(listOf("a".repeat(9), emoji), Limits.chunk("a".repeat(9) + emoji, 10))
        assertEquals(listOf("a".repeat(10), emoji), Limits.chunk("a".repeat(10) + emoji, 11))
    }

    @Test
    fun neverLeavesLoneSurrogates() {
        val emoji = "\uD83D\uDE00"
        for (pad in 0..12) {
            val t = "a".repeat(pad) + emoji + "b".repeat(30)
            val pieces = Limits.chunk(t, 10)
            assertEquals(t, pieces.joinToString(""))
            for (p in pieces) {
                val loneHigh = p.isNotEmpty() && Character.isHighSurrogate(p.last())
                val loneLow = p.isNotEmpty() && Character.isLowSurrogate(p.first())
                assertTrue("lone surrogate in piece of pad=$pad", !loneHigh && !loneLow)
            }
        }
    }
}
