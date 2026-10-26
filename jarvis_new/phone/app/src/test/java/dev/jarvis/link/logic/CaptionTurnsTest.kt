package dev.jarvis.link.logic
import dev.jarvis.link.net.Caption
import org.junit.Assert.*
import org.junit.Test
class CaptionTurnsTest {
    @Test fun replacesInterimRowsWithFinalWithoutDuplicatingTurn() {
        val rows=listOf(Caption(1,"sir","I would…"),Caption(2,"sir","I would like…"),Caption(3,"sir","I would like tea"))
        assertEquals(listOf(rows.last()),CaptionTurns.coalesce(rows))
    }
    @Test fun preservesRepeatedDistinctFinalTurnsAndOtherSpeakers() {
        val rows=listOf(Caption(1,"sir","Yes"),Caption(3,"sir","Yes"),Caption(4,"jarvis","Understood"),Caption(5,"sir","Yes"))
        assertEquals(rows,CaptionTurns.coalesce(rows))
    }
    @Test fun stalePartialDoesNotReplaceNewTurn() {
        val rows=listOf(Caption(1,"sir","Old…"),Caption(30,"sir","A new turn"))
        assertEquals(rows,CaptionTurns.coalesce(rows))
    }
}
