package dev.jarvis.link

/**
 * Multi-turn chat history builder — Kotlin port of Flutter
 * `ChatCtrl.historyOf`. Pure Kotlin (no Android/JSON types) so unit
 * tests run on the JVM. Callers convert the pairs to JSONArray.
 */
object ChatHistory {
    /** Bridge expects at most the last 10 exchanges (20 messages). */
    const val MAX_MESSAGES = 20

    /**
     * Last [MAX_MESSAGES] log entries as [role, text] pairs, oldest first.
     * Roles normalize to `jarvis`|`user` (Flutter: jarvis stays, else user).
     * Input pairs are (role, text); roles are matched case-insensitively so
     * Home ("Jarvis"/"You") and Chat ("jarvis"/"user") logs both work.
     */
    fun historyOf(log: List<Pair<String, String>>): List<List<String>> =
        log.takeLast(MAX_MESSAGES).map { (role, text) ->
            listOf(if (role.equals("jarvis", ignoreCase = true)) "jarvis" else "user", text)
        }

    /** Convert pairs to the JSONArray shape POST /chat expects. */
    fun toJson(pairs: List<List<String>>): org.json.JSONArray =
        org.json.JSONArray().also { arr ->
            for ((role, text) in pairs) {
                arr.put(org.json.JSONArray().put(role).put(text))
            }
        }
}
