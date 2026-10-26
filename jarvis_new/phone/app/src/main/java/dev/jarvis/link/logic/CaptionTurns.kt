package dev.jarvis.link.logic

import dev.jarvis.link.net.Caption

/** Observational captions can contain several interim … rows followed by one final row. */
object CaptionTurns {
    private fun partial(c: Caption) = role(c)=="user" && c.text.trimEnd().endsWith("…")
    private fun role(c: Caption) = if(c.role.lowercase() in listOf("sir","user")) "user" else c.role.lowercase()
    fun coalesce(rows: List<Caption>): List<Caption> {
        val out=mutableListOf<Caption>()
        for (row in rows) {
            val last=out.lastOrNull()
            val sameTurn=last!=null && role(last)==role(row) && row.ts>=last.ts && row.ts-last.ts<=12
            when {
                sameTurn && partial(last!!) -> out[out.lastIndex]=row
                else -> out.add(row)
            }
        }
        return out
    }
}
